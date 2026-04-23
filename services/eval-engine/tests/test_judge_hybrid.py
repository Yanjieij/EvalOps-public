"""Unit tests for HybridJudge — the rule → LLM → agent funnel.

We exercise the escalation policy end-to-end with stub LiteLLM clients,
so no real API is ever called. The assertions target:

1. **Rule-only short circuit**: a high-confidence RAG case stays on
   tier 1 and never touches the LLM or agent clients.
2. **LLM escalation on low faithfulness_lite**: rule gives a low
   ``rag/faithfulness_lite``; hybrid calls the injected LLM stub once;
   both rule and llm metrics appear in the merged result.
3. **Agent escalation always fires for agent cases** and produces
   ``agent_judge/*`` metrics alongside the rule tier.
4. **``escalations`` trace is the source of truth**: downstream
   dashboards read this to attribute cost per tier, so it's pinned.
"""

from __future__ import annotations

import json
from typing import Any

import anyio

from evalops.judge.hybrid import HybridJudge
from evalops.judge.llm import LiteLLMClient
from evalops.models import (
    Case,
    CaseKind,
    JudgeConfig,
    JudgeKind,
    Metadata,
    SutOutput,
)

# --- Stub client (shared shape with test_judge_agent) ------------------


class StubClient(LiteLLMClient):
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
            return {"content": entry, "prompt_tokens": 12, "completion_tokens": 8}
        return {
            "content": entry.get("content", ""),
            "prompt_tokens": entry.get("prompt_tokens", 12),
            "completion_tokens": entry.get("completion_tokens", 8),
        }


def _agent_verdict(score: float = 0.85) -> str:
    return json.dumps(
        {
            "plan_quality": {"score": score, "rationale": "ok"},
            "tool_selection": {"score": score, "rationale": "ok"},
            "reasoning_coherence": {"score": score, "rationale": "ok"},
            "error_recovery": {"score": 1.0, "rationale": "n/a"},
        }
    )


def _hybrid_config() -> JudgeConfig:
    return JudgeConfig(
        name="test-hybrid",
        kind=JudgeKind.HYBRID,
        model="gpt-4o-mini",
        baseline_model="gpt-4o",
    )


# --- Rule-only short-circuit ------------------------------------------


def test_rag_case_with_high_confidence_stays_at_rule_tier() -> None:
    """Rule metrics pass the escalation threshold → no LLM call."""
    case = Case(
        id="rag-clean",
        benchmark_id="test",
        kind=CaseKind.RAG,
        input={"query": "What is the capital of France?"},
        expected={
            "answer": "Paris",
            "source_ids": ["doc-paris"],
        },
    )
    sut_output = SutOutput(
        answer="Paris",
        sources=[{"id": "doc-paris", "content": "Paris is the capital of France."}],
    )

    # No responses queued — if hybrid tries to escalate, StubClient will
    # raise "queue exhausted" and the test fails loudly.
    llm_client = StubClient([])
    agent_client = StubClient([])
    judge = HybridJudge(
        _hybrid_config(),
        llm_client=llm_client,
        agent_client=agent_client,
    )
    result = anyio.run(judge.score, case, sut_output, Metadata())

    assert result.judge_trace["escalations"] == ["rule"]
    assert not llm_client.calls
    assert not agent_client.calls
    metric_names = {m.name for m in result.metrics}
    # Only rule-tier metrics present
    assert "rag/faithfulness_lite" in metric_names
    assert not any(n.startswith("llm/") for n in metric_names)
    assert not any(n.startswith("agent_judge/") for n in metric_names)


def test_rag_case_with_low_faithfulness_escalates_to_llm() -> None:
    """A hallucinated answer trips the faithfulness_lite threshold."""
    case = Case(
        id="rag-halluc",
        benchmark_id="test",
        kind=CaseKind.RAG,
        input={"query": "What is the capital of France?"},
        expected={"answer": "Paris", "source_ids": ["doc-paris"]},
        rubric={
            "escalate_faithfulness": 0.9,  # force escalation
            "llm_metrics": ["rag/faithfulness"],
        },
    )
    sut_output = SutOutput(
        answer="Berlin",
        sources=[{"id": "doc-paris", "content": "Paris is the capital of France."}],
    )
    llm_client = StubClient(
        [json.dumps({"score": 0.1, "rationale": "hallucination detected"})]
    )
    agent_client = StubClient([])
    judge = HybridJudge(
        _hybrid_config(),
        llm_client=llm_client,
        agent_client=agent_client,
    )
    result = anyio.run(judge.score, case, sut_output, Metadata())

    assert result.judge_trace["escalations"] == ["rule", "llm"]
    assert len(llm_client.calls) == 1
    assert not agent_client.calls
    metric_names = {m.name for m in result.metrics}
    # Rule tier metrics still present
    assert "rag/faithfulness_lite" in metric_names
    # LLM tier metric appended
    assert "rag/faithfulness" in metric_names
    by_name = {m.name: m.value for m in result.metrics}
    assert by_name["rag/faithfulness"] == 0.1


def test_agent_case_always_escalates_to_agent_judge() -> None:
    case = Case(
        id="agent-1",
        benchmark_id="test",
        kind=CaseKind.AGENT,
        input={"task": "Look up the capital of France.", "tools": ["rag_query"]},
        expected={"final_answer": "Paris"},
    )
    sut_output = SutOutput(
        answer="Paris",
        agent_trace=[
            {
                "step": 0,
                "thought": "use rag_query",
                "action": {"tool": "rag_query", "args": {"query": "France capital"}},
                "observation": {"answer": "Paris"},
            }
        ],
    )
    llm_client = StubClient([])  # should NOT be called for agent cases
    agent_client = StubClient([_agent_verdict(score=0.9)])
    judge = HybridJudge(
        _hybrid_config(),
        llm_client=llm_client,
        agent_client=agent_client,
    )
    result = anyio.run(judge.score, case, sut_output, Metadata())

    assert result.judge_trace["escalations"] == ["rule", "agent"]
    assert not llm_client.calls
    assert len(agent_client.calls) == 1
    metric_names = {m.name for m in result.metrics}
    # Rule-tier agent metrics
    assert "agent/tool_selection" in metric_names
    # Agent-judge metrics
    assert "agent_judge/overall" in metric_names
    assert "agent_judge/plan_quality" in metric_names


def test_skip_flags_disable_escalation_even_on_agent_kind() -> None:
    case = Case(
        id="agent-2",
        benchmark_id="test",
        kind=CaseKind.AGENT,
        input={"task": "x", "tools": ["calc"]},
        expected={"final_answer": "4"},
        rubric={"skip_agent_judge": True},
    )
    sut_output = SutOutput(
        answer="4",
        agent_trace=[{"thought": "t", "action": {"tool": "calc"}, "observation": {}}],
    )
    llm_client = StubClient([])
    agent_client = StubClient([])  # never called because skip_agent_judge
    judge = HybridJudge(
        _hybrid_config(),
        llm_client=llm_client,
        agent_client=agent_client,
    )
    result = anyio.run(judge.score, case, sut_output, Metadata())
    assert result.judge_trace["escalations"] == ["rule"]
    assert not agent_client.calls


def test_always_llm_flag_forces_escalation_on_passing_case() -> None:
    """Even a clean case should hit the LLM tier when rubric demands it."""
    case = Case(
        id="rag-clean-forced",
        benchmark_id="test",
        kind=CaseKind.RAG,
        input={"query": "Capital?"},
        expected={"answer": "Paris", "source_ids": ["doc-paris"]},
        rubric={"always_llm": True, "llm_metrics": ["rag/faithfulness"]},
    )
    sut_output = SutOutput(
        answer="Paris",
        sources=[{"id": "doc-paris", "content": "Paris is the capital."}],
    )
    llm_client = StubClient(
        [json.dumps({"score": 0.95, "rationale": "checked"})]
    )
    judge = HybridJudge(
        _hybrid_config(),
        llm_client=llm_client,
        agent_client=StubClient([]),
    )
    result = anyio.run(judge.score, case, sut_output, Metadata())
    assert result.judge_trace["escalations"] == ["rule", "llm"]
    assert len(llm_client.calls) == 1
