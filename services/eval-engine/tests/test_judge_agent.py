"""Unit tests for AgentJudge — the 4-dim trace auditor.

The pattern mirrors ``test_judge_llm``: swap in a ``StubClient`` that
returns canned JSON from a FIFO queue, and assert on the structured
``JudgeResult``. No real API is ever called.

We cover:

1. **Happy path** — all four dimensions parse cleanly and land as
   ``agent_judge/*`` metrics, plus the equal-weight overall.
2. **Trace clipping** — a long trace still renders and fits under the
   rendered-string cap without crashing the prompt template.
3. **Self-consistency** — two disagreeing reps for one dimension tip
   the whole result into ``unstable=True``.
4. **Weighted overall** — ``rubric.dimension_weights`` shifts the
   ``agent_judge/overall`` score away from the plain mean.
5. **Parse error** — when the judge returns garbage, we still emit
   all four metrics at 0.0 and flag unstable instead of raising.
"""

from __future__ import annotations

import json
from typing import Any

import anyio

from evalops.judge.agent import AgentJudge, _render_trace
from evalops.judge.llm import LiteLLMClient
from evalops.models import (
    Case,
    CaseKind,
    JudgeConfig,
    JudgeKind,
    Metadata,
    SutOutput,
)

# --- Shared helpers ------------------------------------------------------


class StubClient(LiteLLMClient):
    """FIFO queue of canned LiteLLM responses — see test_judge_llm.py."""

    def __init__(self, responses: list[Any]) -> None:
        self._queue = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._completion = lambda **kw: None

    def _ensure(self) -> None:  # type: ignore[override]
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:  # type: ignore[override]
        self.calls.append(kw)
        if not self._queue:
            raise RuntimeError("StubClient queue exhausted")
        entry = self._queue.pop(0)
        if isinstance(entry, str):
            return {"content": entry, "prompt_tokens": 20, "completion_tokens": 10}
        return {
            "content": entry.get("content", ""),
            "prompt_tokens": entry.get("prompt_tokens", 20),
            "completion_tokens": entry.get("completion_tokens", 10),
        }


def _verdict_json(
    plan: float = 1.0,
    tool: float = 1.0,
    reasoning: float = 1.0,
    recovery: float = 1.0,
) -> str:
    return json.dumps(
        {
            "plan_quality": {"score": plan, "rationale": "clear"},
            "tool_selection": {"score": tool, "rationale": "correct"},
            "reasoning_coherence": {"score": reasoning, "rationale": "coherent"},
            "error_recovery": {"score": recovery, "rationale": "n/a"},
        }
    )


def _agent_case(
    case_id: str = "c1",
    rubric: dict[str, Any] | None = None,
    final_answer: str = "Paris",
    trace: list[dict[str, Any]] | None = None,
) -> tuple[Case, SutOutput]:
    case = Case(
        id=case_id,
        benchmark_id="test",
        kind=CaseKind.AGENT,
        input={
            "task": "Find the capital of France",
            "tools": ["rag_query", "calc"],
        },
        expected={"final_answer": "Paris"},
        rubric=rubric or {},
    )
    sut_output = SutOutput(
        answer=final_answer,
        agent_trace=trace
        or [
            {
                "step": 0,
                "thought": "look up France",
                "action": {"tool": "rag_query", "args": {"query": "France capital"}},
                "observation": {"answer": "Paris"},
            }
        ],
    )
    return case, sut_output


def _config(
    *,
    repeats: int = 1,
    model: str = "gpt-4o",
) -> JudgeConfig:
    return JudgeConfig(
        name="test-agent-judge",
        kind=JudgeKind.AGENT_TRACE,
        model=model,
        repeats=repeats,
    )


# --- Tests --------------------------------------------------------------


def test_happy_path_emits_four_dim_metrics_and_overall() -> None:
    case, out = _agent_case()
    client = StubClient([_verdict_json(plan=0.9, tool=1.0, reasoning=0.8, recovery=1.0)])
    judge = AgentJudge(_config(), client=client)
    result = anyio.run(judge.score, case, out, Metadata())

    names = [m.name for m in result.metrics]
    # 4 dimensions + overall
    assert "agent_judge/plan_quality" in names
    assert "agent_judge/tool_selection" in names
    assert "agent_judge/reasoning_coherence" in names
    assert "agent_judge/error_recovery" in names
    assert "agent_judge/overall" in names
    assert len(result.metrics) == 5

    by_name = {m.name: m.value for m in result.metrics}
    assert by_name["agent_judge/plan_quality"] == 0.9
    assert by_name["agent_judge/tool_selection"] == 1.0
    assert by_name["agent_judge/reasoning_coherence"] == 0.8
    assert by_name["agent_judge/error_recovery"] == 1.0
    # Equal-weight overall mean
    assert abs(by_name["agent_judge/overall"] - (0.9 + 1.0 + 0.8 + 1.0) / 4) < 1e-9
    assert result.unstable is False
    assert result.cost.prompt_tokens == 20


def test_trace_clipping_long_observation_does_not_crash() -> None:
    long_trace = [
        {
            "step": i,
            "thought": f"step {i}",
            "action": {"tool": "file_read", "args": {"path": f"f{i}.txt"}},
            "observation": {"content": "x" * 10_000},  # way over the 280-char clip
        }
        for i in range(5)
    ]
    rendered = _render_trace(long_trace)
    # The 280-char clip should trim each observation inline
    assert "…" in rendered
    assert len(rendered) < 10_000 * 5
    # And it should still be parseable as 5 JSON lines
    assert rendered.count("\n") == 4


def test_self_consistency_flags_unstable_on_dim_stddev() -> None:
    case, out = _agent_case(rubric={"unstable_stddev": 0.15})
    # Two reps disagree on plan_quality: 1.0 vs 0.0 → stddev = 0.5
    client = StubClient(
        [
            _verdict_json(plan=1.0, tool=1.0, reasoning=1.0, recovery=1.0),
            _verdict_json(plan=0.0, tool=1.0, reasoning=1.0, recovery=1.0),
        ]
    )
    judge = AgentJudge(_config(repeats=2), client=client)
    result = anyio.run(judge.score, case, out, Metadata())
    assert result.unstable is True
    by_name = {m.name: m.value for m in result.metrics}
    assert by_name["agent_judge/plan_quality"] == 0.5  # mean across reps


def test_weighted_overall_shifts_with_dimension_weights() -> None:
    case, out = _agent_case(
        rubric={
            "dimension_weights": {
                "plan_quality": 0.0,
                "tool_selection": 0.0,
                "reasoning_coherence": 0.0,
                "error_recovery": 3.0,
            }
        }
    )
    client = StubClient([_verdict_json(plan=0.0, tool=0.0, reasoning=0.0, recovery=1.0)])
    judge = AgentJudge(_config(), client=client)
    result = anyio.run(judge.score, case, out, Metadata())

    by_name = {m.name: m.value for m in result.metrics}
    # With only error_recovery weighted, overall should be 1.0.
    assert by_name["agent_judge/overall"] == 1.0


def test_parse_error_yields_zero_metrics_and_unstable() -> None:
    case, out = _agent_case()
    client = StubClient(["not valid json at all, sorry"])
    judge = AgentJudge(_config(), client=client)
    result = anyio.run(judge.score, case, out, Metadata())

    assert result.unstable is True
    by_name = {m.name: m.value for m in result.metrics}
    # All 4 dims fall back to 0.0
    assert by_name["agent_judge/plan_quality"] == 0.0
    assert by_name["agent_judge/tool_selection"] == 0.0
    assert by_name["agent_judge/reasoning_coherence"] == 0.0
    assert by_name["agent_judge/error_recovery"] == 0.0
    assert by_name["agent_judge/overall"] == 0.0


def test_empty_trace_still_renders() -> None:
    # An empty trace is a legal shape — the agent might have been stopped
    # before doing anything. We should still produce a prompt and a
    # verdict, not crash.
    case, out = _agent_case(trace=[])
    client = StubClient([_verdict_json(plan=0.2, tool=0.2, reasoning=0.2, recovery=1.0)])
    judge = AgentJudge(_config(), client=client)
    result = anyio.run(judge.score, case, out, Metadata())
    assert len(result.metrics) == 5
    assert result.metrics[0].value == 0.2
