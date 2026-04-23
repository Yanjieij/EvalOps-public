"""RunnerEngine — the workhorse that turns a benchmark into a Run.

Week 1 scope:
- Structured concurrency with `anyio` semaphore.
- Per-case metadata propagation (run_id, case_id, request_id).
- Deterministic pass/fail aggregation from JudgeResult metrics.
- Error isolation: one bad case doesn't sink the whole run.
- Cost and pass-rate aggregation into `RunSummary`.

Deferred to Week 2+:
- Idempotent resume from a partial run (skip completed case_ids).
- gRPC streaming RunEvent emission.
- Distributed execution (current engine is single-process).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable

import anyio

from evalops.adapters import SutAdapter, build_adapter
from evalops.judge import Judge, build_judge
from evalops.logging import bind_case, bind_request, bind_run, get_logger
from evalops.models import (
    Benchmark,
    Case,
    CaseResult,
    Cost,
    JudgeConfig,
    JudgeResult,
    Metadata,
    MetricScore,
    Run,
    RunStatus,
    RunSummary,
    Sut,
    SutOutput,
)
from evalops.observability import (
    case_span,
    record_case_done,
    record_judge_call,
    record_run_finish,
    record_run_start,
    run_span,
)

log = get_logger(__name__)


class RunnerEngine:
    def __init__(
        self,
        *,
        benchmark: Benchmark,
        cases: Iterable[Case],
        sut: Sut,
        judge_config: JudgeConfig,
        concurrency: int = 4,
        pass_threshold: float = 0.5,
        resume_from: Run | None = None,
    ) -> None:
        self.benchmark = benchmark
        self.cases = list(cases)
        self.sut = sut
        self.judge_config = judge_config
        self.concurrency = max(1, concurrency)
        self.pass_threshold = pass_threshold
        # Cases already completed in a prior run are carried forward
        # verbatim. We key by case_id so the resume is tolerant to case
        # reordering — helpful when a benchmark file is edited between
        # attempts.
        self._prior_results: dict[str, CaseResult] = {}
        self._resume_run_id: str | None = None
        if resume_from is not None:
            for r in resume_from.results:
                if not r.error:
                    self._prior_results[r.case_id] = r
            self._resume_run_id = resume_from.id

    # ---- public API ---------------------------------------------------------

    async def run(self) -> Run:
        # If we're resuming, keep the original run_id so the Jaeger
        # harvester can correlate the resumed tail with the first
        # attempt. Status starts as running regardless.
        run_id = self._resume_run_id or str(uuid.uuid4())
        run = Run(
            id=run_id,
            benchmark=self.benchmark,
            sut=self.sut,
            judge_config=self.judge_config,
            status="running",
            started_at_unix=int(time.time()),
            concurrency=self.concurrency,
        )
        bind_run(run.id)
        record_run_start(benchmark=self.benchmark.name, sut=self.sut.name)

        pending_cases = [c for c in self.cases if c.id not in self._prior_results]
        log.info("run.start",
                 run_id=run.id,
                 benchmark=self.benchmark.name,
                 sut=self.sut.name,
                 judge=self.judge_config.name,
                 n_cases=len(self.cases),
                 n_pending=len(pending_cases),
                 n_resumed=len(self._prior_results),
                 concurrency=self.concurrency)

        # One run_span wraps the entire run; case_spans below are its
        # children. OTel's default no-op tracer returns a cheap stub
        # when EVALOPS_OTEL_EXPORTER_ENDPOINT is unset, so this block
        # remains free in CI.
        with run_span(
            run_id=run.id,
            benchmark=self.benchmark.name,
            sut=self.sut.name,
            judge=self.judge_config.name,
            concurrency=self.concurrency,
        ):
            # If everything was already done last time, we still rebuild
            # the run summary (metrics may have gained new fields) but
            # skip the adapter entirely.
            if not pending_cases:
                run.results = [self._prior_results[c.id] for c in self.cases]
                run.summary = self._summarize(run.results)
                run.finished_at_unix = int(time.time())
                run.status = self._final_status(run.results)
                self._emit_run_metrics(run)
                log.info("run.finish",
                         run_id=run.id,
                         status=run.status,
                         pass_rate=run.summary.pass_rate,
                         note="all cases resumed from prior run")
                return run

            adapter = build_adapter(self.sut)
            judge = build_judge(self.judge_config)

            fresh_results: list[CaseResult] = [None] * len(pending_cases)  # type: ignore[list-item]
            try:
                limiter = anyio.Semaphore(self.concurrency)

                async def worker(idx: int, case: Case) -> None:
                    async with limiter:
                        fresh_results[idx] = await self._run_one(
                            adapter, judge, run.id, case
                        )

                async with anyio.create_task_group() as tg:
                    for i, case in enumerate(pending_cases):
                        tg.start_soon(worker, i, case)
            finally:
                await adapter.aclose()

            # Reassemble in original case order: resumed + freshly computed.
            by_id = {r.case_id: r for r in fresh_results if r is not None}
            by_id.update(self._prior_results)
            run.results = [by_id[c.id] for c in self.cases if c.id in by_id]

            run.summary = self._summarize(run.results)
            run.finished_at_unix = int(time.time())
            run.status = self._final_status(run.results)
            self._emit_run_metrics(run)
            log.info("run.finish",
                     run_id=run.id,
                     status=run.status,
                     pass_rate=run.summary.pass_rate,
                     total_cost_micro_usd=run.summary.total_cost.micro_usd,
                     duration_s=run.finished_at_unix - run.started_at_unix,
                     resumed=len(self._prior_results))
            return run

    # ---- observability helpers ---------------------------------------------

    def _emit_run_metrics(self, run: Run) -> None:
        """Push summary-level numbers to the Prometheus registry.

        Called at the very end of ``run()`` so ``evalops_ee_runs_total``
        only ever sees a terminal status. The started counter is bumped
        separately at the top of ``run()``.
        """
        status_str = str(run.status.value) if hasattr(run.status, "value") else str(run.status)
        record_run_finish(
            benchmark=run.benchmark.name,
            sut=run.sut.name,
            status=status_str,
            duration_seconds=max(0, run.finished_at_unix - run.started_at_unix),
            pass_rate=run.summary.pass_rate,
            judge_agreement=run.summary.judge_agreement,
        )
        # Per-judge cost: sum across all case results' judge_result.cost
        # and attribute to the run-level judge_config kind/model. This
        # lets Grafana separate the cost of rule vs llm vs agent tiers.
        if run.results:
            total_micro = sum(r.judge_result.cost.micro_usd for r in run.results)
            record_judge_call(
                kind=str(self.judge_config.kind.value)
                if hasattr(self.judge_config.kind, "value")
                else str(self.judge_config.kind),
                model=self.judge_config.model or "",
                cost_micro_usd=total_micro,
            )

    # ---- per-case path ------------------------------------------------------

    async def _run_one(
        self,
        adapter: SutAdapter,
        judge: Judge,
        run_id: str,
        case: Case,
    ) -> CaseResult:
        bind_case(case.id)
        req_id = str(uuid.uuid4())
        bind_request(req_id)
        metadata = Metadata(
            request_id=req_id,
            run_id=run_id,
            case_id=case.id,
        )
        kind_str = str(case.kind.value) if hasattr(case.kind, "value") else str(case.kind)
        with case_span(
            run_id=run_id,
            case_id=case.id,
            kind=kind_str,
            benchmark=self.benchmark.name,
            sut=self.sut.name,
        ):
            started = time.perf_counter()
            try:
                sut_output = await adapter.call(case, metadata)
            except Exception as exc:
                latency = int((time.perf_counter() - started) * 1000)
                log.error("case.sut_error", case_id=case.id, error=str(exc))
                record_case_done(
                    benchmark=self.benchmark.name,
                    sut=self.sut.name,
                    kind=kind_str,
                    duration_seconds=latency / 1000.0,
                )
                return CaseResult(
                    case_id=case.id,
                    passed=False,
                    sut_output=SutOutput(latency_ms=latency),
                    judge_result=JudgeResult(metrics=[], unstable=True),
                    latency_ms=latency,
                    error=f"sut: {exc}",
                )

            try:
                judge_result = await judge.score(case, sut_output, metadata)
            except Exception as exc:
                latency = int((time.perf_counter() - started) * 1000)
                log.error("case.judge_error", case_id=case.id, error=str(exc))
                record_case_done(
                    benchmark=self.benchmark.name,
                    sut=self.sut.name,
                    kind=kind_str,
                    duration_seconds=latency / 1000.0,
                )
                return CaseResult(
                    case_id=case.id,
                    passed=False,
                    sut_output=sut_output,
                    judge_result=JudgeResult(metrics=[], unstable=True),
                    latency_ms=latency,
                    error=f"judge: {exc}",
                )

            latency = int((time.perf_counter() - started) * 1000)
            passed = self._derive_passed(case, judge_result.metrics)
            log.info("case.done", case_id=case.id, passed=passed, latency_ms=latency)
            record_case_done(
                benchmark=self.benchmark.name,
                sut=self.sut.name,
                kind=kind_str,
                duration_seconds=latency / 1000.0,
            )
            return CaseResult(
                case_id=case.id,
                passed=passed,
                sut_output=sut_output,
                judge_result=judge_result,
                latency_ms=latency,
            )

    # ---- aggregation --------------------------------------------------------

    def _derive_passed(self, case: Case, metrics: list[MetricScore]) -> bool:
        """A case passes iff its primary metric is >= the pass threshold.

        Primary metric is:
        - `rubric.primary_metric` if set on the case, else
        - `agent/final_f1` for agent cases, else
        - `rag/f1` for rag cases, else
        - the first metric in the list.
        """
        if not metrics:
            return False
        by_name = {m.name: m.value for m in metrics}
        key = (case.rubric or {}).get("primary_metric")
        if key and key in by_name:
            return by_name[key] >= self.pass_threshold
        for fallback in ("rag/f1", "agent/final_f1", "chat/f1", "llm/overall"):
            if fallback in by_name:
                return by_name[fallback] >= self.pass_threshold
        return metrics[0].value >= self.pass_threshold

    def _summarize(self, results: list[CaseResult]) -> RunSummary:
        if not results:
            return RunSummary()
        total_cost = Cost()
        metric_sums: dict[str, float] = {}
        metric_counts: dict[str, int] = {}
        passed = 0
        unstable = 0
        # For dual-judge runs, collect (primary, secondary) score pairs
        # across every dual-judged case. Cohen's κ is computed once per
        # run from the full corpus — it's a corpus-level metric.
        dual_primary: list[float] = []
        dual_secondary: list[float] = []
        for r in results:
            total_cost = total_cost + r.cost
            if r.passed:
                passed += 1
            if r.judge_result.unstable:
                unstable += 1
            for m in r.judge_result.metrics:
                metric_sums[m.name] = metric_sums.get(m.name, 0.0) + m.value
                metric_counts[m.name] = metric_counts.get(m.name, 0) + 1
            if r.judge_result.judge_trace.get("judge") == "llm_dual":
                for pair in r.judge_result.judge_trace.get("dual_raw_pairs", []):
                    dual_primary.append(float(pair["primary"]))
                    dual_secondary.append(float(pair["secondary"]))

        metrics_avg = {
            name: metric_sums[name] / metric_counts[name]
            for name in metric_sums
        }

        judge_agreement = -1.0
        if dual_primary:
            # Local import keeps the LiteLLM-heavy judge package out of
            # the runner's critical path when no dual-judge ran.
            from evalops.judge.llm import cohens_kappa_from_scores
            judge_agreement = cohens_kappa_from_scores(dual_primary, dual_secondary)

        return RunSummary(
            metrics=metrics_avg,
            total_cost=total_cost,
            pass_rate=passed / len(results),
            unstable_cases=unstable,
            judge_agreement=judge_agreement,
        )

    def _final_status(self, results: list[CaseResult]) -> RunStatus:
        n_errors = sum(1 for r in results if r.error)
        if not results:
            return RunStatus.FAILED
        if n_errors == len(results):
            return RunStatus.FAILED
        if n_errors > 0:
            return RunStatus.PARTIAL
        return RunStatus.SUCCEEDED
