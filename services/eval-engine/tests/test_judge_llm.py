"""Unit tests for the LiteLLM-backed judge.

The tests never hit a real API — every call replaces ``LiteLLMClient``
with a stub that returns canned JSON. This lets us verify the three
hardest pieces of the implementation deterministically:

1. **Single-score self-consistency**: if all reps agree, unstable=False;
   if they disagree past the threshold, unstable=True.
2. **Pairwise swap+vote**: if the judge flips its answer on position
   swap, we return TIE and mark unstable.
3. **Dual-judge Cohen's kappa**: when both providers agree on the same
   bin, κ → 1.0; when they disagree, κ drops.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest

from evalops.judge.llm import (
    LiteLLMClient,
    LLMJudge,
    _bin_score,
    cohens_kappa_from_scores,
)
from evalops.models import (
    Case,
    CaseKind,
    JudgeConfig,
    JudgeKind,
    Metadata,
    SutOutput,
)

# --- Stub client helpers ------------------------------------------------


class StubClient(LiteLLMClient):
    """Replaces the real LiteLLM call with a FIFO queue of canned responses.

    Each entry in ``responses`` is either a dict (returned directly as
    ``content`` / ``prompt_tokens`` / ``completion_tokens``) or a string
    (wrapped into a zero-cost response).
    """

    def __init__(self, responses: list[Any]) -> None:
        self._queue = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._completion = lambda **kw: None  # satisfy _ensure

    def _ensure(self) -> None:  # type: ignore[override]
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:  # type: ignore[override]
        self.calls.append(kw)
        if not self._queue:
            raise RuntimeError("StubClient queue exhausted")
        entry = self._queue.pop(0)
        if isinstance(entry, str):
            return {"content": entry, "prompt_tokens": 10, "completion_tokens": 5}
        return {
            "content": entry.get("content", ""),
            "prompt_tokens": entry.get("prompt_tokens", 10),
            "completion_tokens": entry.get("completion_tokens", 5),
        }


def _rag_case() -> Case:
    return Case(
        id="t1",
        benchmark_id="test",
        kind=CaseKind.RAG,
        input={"query": "What is the capital of France?"},
        expected={"answer": "Paris"},
        rubric={"llm_metrics": ["rag/faithfulness"]},
    )


def _sut_output(answer: str) -> SutOutput:
    return SutOutput(
        answer=answer,
        sources=[{"id": "doc-1", "content": "France's capital is Paris."}],
    )


# --- Single-score tests -------------------------------------------------


def test_single_score_happy_path():
    responses = [json.dumps({"score": 0.9, "rationale": "supported"})]
    client = StubClient(responses)
    judge = LLMJudge(
        JudgeConfig(name="t", kind=JudgeKind.LLM_SINGLE, model="gpt-4o-mini"),
        client=client,
    )
    result = anyio.run(
        judge.score, _rag_case(), _sut_output("Paris"), Metadata(request_id="r1")
    )
    assert len(result.metrics) == 1
    assert result.metrics[0].name == "rag/faithfulness"
    assert result.metrics[0].value == pytest.approx(0.9)
    assert result.unstable is False
    assert result.cost.prompt_tokens == 10


def test_single_score_self_consistency_stable():
    responses = [
        json.dumps({"score": 0.8, "rationale": "ok"}),
        json.dumps({"score": 0.82, "rationale": "ok"}),
        json.dumps({"score": 0.78, "rationale": "ok"}),
    ]
    client = StubClient(responses)
    judge = LLMJudge(
        JudgeConfig(name="t", kind=JudgeKind.LLM_SINGLE, model="m", repeats=3),
        client=client,
    )
    result = anyio.run(
        judge.score, _rag_case(), _sut_output("Paris"), Metadata(request_id="r2")
    )
    assert result.unstable is False
    assert 0.79 <= result.metrics[0].value <= 0.81


def test_single_score_self_consistency_unstable():
    # Wildly disagreeing scores should trip the unstable flag.
    responses = [
        json.dumps({"score": 0.95, "rationale": "a"}),
        json.dumps({"score": 0.10, "rationale": "b"}),
        json.dumps({"score": 0.70, "rationale": "c"}),
    ]
    client = StubClient(responses)
    judge = LLMJudge(
        JudgeConfig(name="t", kind=JudgeKind.LLM_SINGLE, model="m", repeats=3),
        client=client,
    )
    result = anyio.run(
        judge.score, _rag_case(), _sut_output("Paris"), Metadata(request_id="r3")
    )
    assert result.unstable is True


def test_parse_json_tolerates_markdown_fence():
    responses = [
        "```json\n" + json.dumps({"score": 0.5, "rationale": "x"}) + "\n```"
    ]
    client = StubClient(responses)
    judge = LLMJudge(
        JudgeConfig(name="t", kind=JudgeKind.LLM_SINGLE, model="m"),
        client=client,
    )
    result = anyio.run(
        judge.score, _rag_case(), _sut_output("Paris"), Metadata(request_id="r4")
    )
    assert result.metrics[0].value == pytest.approx(0.5)


# --- Pairwise swap+vote --------------------------------------------------


def _pairwise_case() -> Case:
    return Case(
        id="p1",
        benchmark_id="test",
        kind=CaseKind.RAG,
        input={"query": "Capital of France?"},
        expected={"baseline_answer": "Lyon"},
        rubric={"pairwise_criterion": "factual correctness"},
    )


def test_pairwise_consistent_win():
    # Call 1 says A wins (A=SUT), call 2 says B wins (B=SUT, swap).
    # Both are SUT=WIN => final WIN, stable.
    responses = [
        json.dumps({"winner": "A", "rationale": "sut correct"}),
        json.dumps({"winner": "B", "rationale": "sut correct"}),
    ]
    client = StubClient(responses)
    judge = LLMJudge(
        JudgeConfig(name="t", kind=JudgeKind.LLM_PAIRWISE, model="m"),
        client=client,
    )
    result = anyio.run(
        judge.score, _pairwise_case(), _sut_output("Paris"),
        Metadata(request_id="p")
    )
    assert result.metrics[0].value == 1.0
    assert result.unstable is False


def test_pairwise_position_bias_disagreement():
    # Call 1 says A (=SUT) wins; call 2 ALSO says A (=baseline) wins.
    # That's classic position bias — both votes prefer "A". We expect
    # the judge to flag it as unstable and collapse to TIE.
    responses = [
        json.dumps({"winner": "A", "rationale": "prefers A"}),
        json.dumps({"winner": "A", "rationale": "prefers A"}),
    ]
    client = StubClient(responses)
    judge = LLMJudge(
        JudgeConfig(name="t", kind=JudgeKind.LLM_PAIRWISE, model="m"),
        client=client,
    )
    result = anyio.run(
        judge.score, _pairwise_case(), _sut_output("Paris"),
        Metadata(request_id="p")
    )
    assert result.metrics[0].value == 0.5
    assert result.unstable is True


def test_pairwise_missing_baseline_flags_unstable():
    case = Case(
        id="p-no-baseline",
        kind=CaseKind.RAG,
        input={"query": "?"},
        expected={},  # no baseline_answer
    )
    client = StubClient([])
    judge = LLMJudge(
        JudgeConfig(name="t", kind=JudgeKind.LLM_PAIRWISE, model="m"),
        client=client,
    )
    result = anyio.run(
        judge.score, case, _sut_output("x"), Metadata(request_id="p")
    )
    assert result.unstable is True
    assert result.metrics[0].confidence == 0.0


# --- Dual-judge: per-case agreement + run-level kappa ------------------


def test_dual_judge_case_level_mean_and_bin_agreement():
    # Both providers return the same score -> bin_agreement = 1.0,
    # and the reported metric is the mean (still 0.9).
    responses = [
        json.dumps({"score": 0.9, "rationale": "ok"}),   # primary
        json.dumps({"score": 0.9, "rationale": "ok"}),   # secondary
    ]
    client = StubClient(responses)
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
    result = anyio.run(
        judge.score, _rag_case(), _sut_output("Paris"), Metadata(request_id="d1")
    )
    by_name = {m.name: m.value for m in result.metrics}
    assert by_name["rag/faithfulness"] == pytest.approx(0.9)
    assert by_name["llm/dual_bin_agreement"] == pytest.approx(1.0)
    assert result.judge_trace["judge"] == "llm_dual"
    assert result.judge_trace["dual_raw_pairs"] == [
        {"metric": "rag/faithfulness", "primary": 0.9, "secondary": 0.9}
    ]


def test_dual_judge_case_level_disagreement_flags_bin_zero():
    # 0.1 vs 0.9 -> different bins, bin_agreement = 0.0.
    responses = [
        json.dumps({"score": 0.1, "rationale": "disagree"}),
        json.dumps({"score": 0.9, "rationale": "disagree"}),
    ]
    client = StubClient(responses)
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
    result = anyio.run(
        judge.score, _rag_case(), _sut_output("Paris"), Metadata(request_id="d2")
    )
    by_name = {m.name: m.value for m in result.metrics}
    assert by_name["llm/dual_bin_agreement"] == pytest.approx(0.0)
    # The dashboard-facing metric is the mean of the two scores.
    assert by_name["rag/faithfulness"] == pytest.approx(0.5)


# --- Cohen's kappa primitive --------------------------------------------


def test_bin_score_boundaries():
    assert _bin_score(0.0) == 0
    assert _bin_score(0.32) == 0
    assert _bin_score(0.34) == 1
    assert _bin_score(0.65) == 1
    assert _bin_score(0.67) == 2
    assert _bin_score(1.0) == 2


def test_kappa_all_agree():
    xs = [0.9, 0.8, 0.2, 0.5]
    ys = [0.95, 0.85, 0.25, 0.55]
    # All pairs bin identically.
    assert cohens_kappa_from_scores(xs, ys) == pytest.approx(1.0)


def test_kappa_all_disagree():
    xs = [0.9, 0.1]
    ys = [0.1, 0.9]
    # Perfect anti-agreement, binning-wise, still yields a negative κ.
    assert cohens_kappa_from_scores(xs, ys) < 0.0


def test_kappa_degenerate_empty_is_zero():
    assert cohens_kappa_from_scores([], []) == 0.0
