"""Smoke test — the τ-bench-lite benchmark loads, runs, and scores.

This exercises three things in one flow so CI fails loudly if any of
them regress:

1. **Dataset loader** reads 20 cases across 3 YAML files under
   ``datasets/tau-bench-lite/cases/``.
2. **MockAdapter replay** produces byte-identical traces from each
   case's ``expected.trace`` — we assert on a specific case's trace
   shape so a future mock refactor doesn't silently drop steps.
3. **RuleJudge end-to-end** passes a sensible fraction. tbl-020 is
   deliberately designed to fail (the Agent gave up). With the mock
   adapter and the rule judge, we expect the pass rate to land at
   exactly 19/20 = 95%.

We don't run AgentJudge here — it needs a real LLM or a heavy stub
setup per-case. ``test_judge_agent`` + ``test_judge_hybrid`` already
cover the agent-judge code path with stubs.
"""

from __future__ import annotations

from pathlib import Path

import anyio

from evalops.datasets import load_benchmark
from evalops.models import JudgeConfig, JudgeKind, Sut, SutKind
from evalops.runner import RunnerEngine

REPO_ROOT = Path(__file__).resolve().parents[3]
TAU_BENCH_LITE = REPO_ROOT / "datasets" / "tau-bench-lite"


def test_tau_bench_lite_loads_with_20_cases() -> None:
    bench, cases = load_benchmark(TAU_BENCH_LITE)
    assert bench.name == "tau-bench-lite"
    assert bench.version == "v0.1.0"
    assert len(cases) == 20
    ids = {c.id for c in cases}
    assert "tbl-001-capital-france" in ids
    assert "tbl-020-recovery-gave-up" in ids


def test_recovery_case_trace_shape() -> None:
    """tbl-018 must encode the full 2-step recovery trace."""
    _, cases = load_benchmark(TAU_BENCH_LITE)
    recovery = next(c for c in cases if c.id == "tbl-018-recovery-bad-calc-then-fallback")
    expected_trace = recovery.expected["trace"]
    assert len(expected_trace) == 2
    assert expected_trace[0]["observation"].get("error")
    assert "retry" in expected_trace[1]["thought"].lower()
    assert recovery.rubric.get("inject_failure") is True
    # No fail_after_step — the mock must replay both steps.
    assert "fail_after_step" not in recovery.rubric


def test_mock_replay_end_to_end_95_percent_pass_rate() -> None:
    bench, cases = load_benchmark(TAU_BENCH_LITE)
    engine = RunnerEngine(
        benchmark=bench,
        cases=cases,
        sut=Sut(name="mock", kind=SutKind.MOCK),
        judge_config=JudgeConfig(name="rule", kind=JudgeKind.RULE),
        concurrency=4,
    )
    run = anyio.run(engine.run)

    assert run.status.value == "succeeded"
    assert len(run.results) == 20
    # tbl-020 is the designed negative case: agent gave up, no retry,
    # so agent/error_recovery is 0 → case fails. Everything else passes.
    failing = [r.case_id for r in run.results if not r.passed]
    assert failing == ["tbl-020-recovery-gave-up"], failing
    assert run.summary.pass_rate == 0.95


def test_preset_plans_present_on_lookup_cases() -> None:
    """Sidecar integration path: every lookup case must ship a preset_plan."""
    _, cases = load_benchmark(TAU_BENCH_LITE)
    lookup_ids = {
        "tbl-001-capital-france",
        "tbl-002-capital-japan",
        "tbl-003-capital-germany",
        "tbl-004-largest-planet",
        "tbl-005-product-response-time",
        "tbl-006-product-sla",
    }
    for c in cases:
        if c.id in lookup_ids:
            plan = c.input.get("preset_plan")
            assert plan, f"{c.id} missing preset_plan"
            assert isinstance(plan, list) and len(plan) >= 1
            assert plan[0]["tool"] == "rag_query"
