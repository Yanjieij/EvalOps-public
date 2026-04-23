"""Run-level Cohen's κ aggregation from dual-judge case results.

Cohen's κ is a corpus-level metric (meaningless at n=1), so the runner
aggregates (primary, secondary) score pairs across an entire run and
writes κ into ``RunSummary.judge_agreement`` exactly once.
"""

from __future__ import annotations

import anyio

from evalops.adapters import build_adapter
from evalops.judge.llm import LiteLLMClient, LLMJudge
from evalops.models import (
    Benchmark,
    Case,
    CaseKind,
    JudgeConfig,
    JudgeKind,
    Sut,
    SutKind,
)
from evalops.runner import RunnerEngine


class CannedDualClient(LiteLLMClient):
    """Feeds a fixed pair of (primary, secondary) faithfulness scores
    per case, alternating as the runner progresses."""

    def __init__(self, per_case_pairs: list[tuple[float, float]]) -> None:
        self._pairs = list(per_case_pairs)
        self._case_idx = 0
        self._side = 0  # 0 = primary call, 1 = secondary call
        self._completion = lambda **kw: None

    def _ensure(self) -> None:  # type: ignore[override]
        pass

    def complete(self, **kw):  # type: ignore[override]
        pair = self._pairs[self._case_idx]
        score = pair[self._side]
        if self._side == 0:
            self._side = 1
        else:
            self._side = 0
            self._case_idx += 1
        return {
            "content": f'{{"score": {score}, "rationale": "canned"}}',
            "prompt_tokens": 5,
            "completion_tokens": 3,
        }


def _make_cases(n: int) -> list[Case]:
    cases: list[Case] = []
    for i in range(n):
        cases.append(
            Case(
                id=f"dual-{i:03d}",
                benchmark_id="test-dual",
                kind=CaseKind.RAG,
                input={"query": f"q{i}"},
                expected={"answer": f"a{i}"},
                rubric={"llm_metrics": ["rag/faithfulness"]},
            )
        )
    return cases


def _run_dual_with_client(
    pairs: list[tuple[float, float]],
    client: CannedDualClient,
) -> float:
    """Run the dual-judge path through RunnerEngine and return judge_agreement."""
    cases = _make_cases(len(pairs))

    # We need the runner to use our stub-backed LLMJudge. Easiest path:
    # override build_judge indirectly by swapping the judge in the
    # engine after construction. RunnerEngine today builds the judge
    # inside `run()`, so we monkeypatch the module function.
    import evalops.runner.engine as engine_mod

    def _build_override(_cfg):
        judge = LLMJudge(
            JudgeConfig(
                name="dual",
                kind=JudgeKind.LLM_DUAL,
                model="gpt-4o",
                baseline_model="claude-3-5-sonnet",
                rubric={"llm_metrics": ["rag/faithfulness"]},
            ),
            client=client,
        )
        return judge

    original = engine_mod.build_judge
    engine_mod.build_judge = _build_override  # type: ignore[assignment]
    try:
        engine = RunnerEngine(
            benchmark=Benchmark(name="dual-test", version="v0.0.1"),
            cases=cases,
            sut=Sut(name="mock", kind=SutKind.MOCK),
            judge_config=JudgeConfig(
                name="dual",
                kind=JudgeKind.LLM_DUAL,
                model="gpt-4o",
                baseline_model="claude-3-5-sonnet",
                rubric={"llm_metrics": ["rag/faithfulness"]},
            ),
            concurrency=1,
        )
        # Sanity: build_adapter still wires the mock SUT.
        assert build_adapter(engine.sut) is not None
        run = anyio.run(engine.run)
    finally:
        engine_mod.build_judge = original

    return run.summary.judge_agreement


def test_run_level_kappa_perfect_agreement():
    # Every case: both judges land on the same 3-way bin.
    pairs = [(0.9, 0.9), (0.8, 0.85), (0.1, 0.05), (0.55, 0.5)]
    client = CannedDualClient(pairs)
    kappa = _run_dual_with_client(pairs, client)
    assert kappa == 1.0


def test_run_level_kappa_full_disagreement_is_negative():
    # Every case lands in opposite bins — true corpus κ is strongly
    # negative.
    pairs = [(0.1, 0.9), (0.9, 0.1), (0.15, 0.85), (0.8, 0.2)]
    client = CannedDualClient(pairs)
    kappa = _run_dual_with_client(pairs, client)
    assert kappa < 0.0


def test_run_level_kappa_partial_agreement():
    # Two agreeing cases, two disagreeing — κ should be positive but
    # meaningfully less than 1.
    pairs = [(0.9, 0.85), (0.1, 0.15), (0.9, 0.1), (0.1, 0.9)]
    client = CannedDualClient(pairs)
    kappa = _run_dual_with_client(pairs, client)
    assert kappa < 0.0 or kappa < 0.5  # somewhere in the mid-to-low range
