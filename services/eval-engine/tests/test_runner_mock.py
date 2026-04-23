"""End-to-end runner test — the same code path the CLI smoke test exercises,
but encapsulated so ``pytest`` alone can catch regressions."""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from evalops.datasets import load_benchmark
from evalops.models import JudgeConfig, JudgeKind, Sut, SutKind
from evalops.runner import RunnerEngine

REPO_ROOT = Path(__file__).resolve().parents[3]
RAG_TOY = REPO_ROOT / "datasets" / "rag-toy"
AGENT_TOY = REPO_ROOT / "datasets" / "agent-toy"


@pytest.mark.parametrize("bench_path", [RAG_TOY, AGENT_TOY])
def test_runner_end_to_end_on_mock(bench_path: Path) -> None:
    bench, cases = load_benchmark(bench_path)
    engine = RunnerEngine(
        benchmark=bench,
        cases=cases,
        sut=Sut(name="mock", kind=SutKind.MOCK),
        judge_config=JudgeConfig(name="rule-smoke", kind=JudgeKind.RULE),
        concurrency=2,
    )
    run = anyio.run(engine.run)
    assert run.status in ("succeeded", "partial")
    assert len(run.results) == len(cases)
    # At least one metric was computed for every case
    for r in run.results:
        assert r.judge_result.metrics, f"no metrics for {r.case_id}"


def test_hallucination_case_fails_rag_f1() -> None:
    bench, cases = load_benchmark(RAG_TOY)
    hallu = [c for c in cases if "hallucinate" in c.id]
    assert hallu, "rag-toy must include a hallucination case"
    engine = RunnerEngine(
        benchmark=bench,
        cases=hallu,
        sut=Sut(name="mock", kind=SutKind.MOCK),
        judge_config=JudgeConfig(name="rule", kind=JudgeKind.RULE),
    )
    run = anyio.run(engine.run)
    assert run.results[0].passed is False


def test_unanswerable_case_passes_when_refusal_injected() -> None:
    bench, cases = load_benchmark(RAG_TOY)
    un = [c for c in cases if "unanswerable" in c.id]
    engine = RunnerEngine(
        benchmark=bench,
        cases=un,
        sut=Sut(name="mock", kind=SutKind.MOCK),
        judge_config=JudgeConfig(name="rule", kind=JudgeKind.RULE),
    )
    run = anyio.run(engine.run)
    assert run.results[0].passed is True, "mock in refuse mode should satisfy the rubric"
