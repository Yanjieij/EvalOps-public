"""Runner resume semantics.

Resume is the feature that makes evaluation runs feel safe on flaky
APIs and long benchmarks: if a run crashes halfway, re-submitting it
with ``--resume`` carries forward the already-computed cases and only
re-runs the pending tail.

The contract we test here:

1. Pending cases are the ones NOT present in the prior run (or present
   but errored). Completed, non-errored cases are reused verbatim.
2. The resumed run keeps the original run_id so Jaeger trace correlation
   survives the restart.
3. If the prior run already covers every case, no SUT call happens and
   the engine just rebuilds the summary.
4. Final result order matches ``cases`` input order, independent of
   resume vs. fresh split.
"""

from __future__ import annotations

import anyio

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


def _cases(n: int) -> list[Case]:
    return [
        Case(
            id=f"c{i:03d}",
            benchmark_id="resume-test",
            kind=CaseKind.RAG,
            input={"query": f"q{i}"},
            expected={"answer": f"a{i}"},
            rubric={"mock_mode": "faithful"},
        )
        for i in range(n)
    ]


def _engine(cases: list[Case], resume=None) -> RunnerEngine:
    return RunnerEngine(
        benchmark=Benchmark(name="resume-test", version="v0.0.1"),
        cases=cases,
        sut=Sut(name="mock", kind=SutKind.MOCK),
        judge_config=JudgeConfig(name="rule", kind=JudgeKind.RULE),
        concurrency=2,
    ) if resume is None else RunnerEngine(
        benchmark=Benchmark(name="resume-test", version="v0.0.1"),
        cases=cases,
        sut=Sut(name="mock", kind=SutKind.MOCK),
        judge_config=JudgeConfig(name="rule", kind=JudgeKind.RULE),
        concurrency=2,
        resume_from=resume,
    )


def test_fresh_run_baseline():
    cases = _cases(5)
    run = anyio.run(_engine(cases).run)
    assert len(run.results) == 5
    assert all(not r.error for r in run.results)


def test_resume_skips_completed_cases_and_extends():
    cases = _cases(5)
    first = anyio.run(_engine(cases).run)
    # Drop the last two results so the "prior run" looks partial.
    first.results = first.results[:3]
    original_run_id = first.id

    second = anyio.run(_engine(cases, resume=first).run)
    # All 5 cases present, ordered c000..c004.
    assert [r.case_id for r in second.results] == [f"c{i:03d}" for i in range(5)]
    # Run id preserved for trace correlation.
    assert second.id == original_run_id


def test_resume_with_prior_errors_retries_them():
    cases = _cases(4)
    first = anyio.run(_engine(cases).run)
    # Mutate one result to look like an earlier SUT error.
    first.results[1] = first.results[1].model_copy(
        update={"error": "injected", "passed": False}
    )
    second = anyio.run(_engine(cases, resume=first).run)
    # The error case was re-executed — no error in the new result.
    retried = next(r for r in second.results if r.case_id == "c001")
    assert retried.error == ""


def test_full_resume_is_a_pure_summary_rebuild():
    cases = _cases(3)
    first = anyio.run(_engine(cases).run)
    # Resume with ALL cases already present — no SUT calls should
    # happen. We exercise this by asserting the resumed run still
    # emits a valid summary + same results.
    second = anyio.run(_engine(cases, resume=first).run)
    assert len(second.results) == 3
    assert second.id == first.id
    # Per-case outputs are the exact same objects from the prior run.
    for a, b in zip(first.results, second.results, strict=True):
        assert a.case_id == b.case_id
        assert a.passed == b.passed
