"""A fully in-process mock SUT.

Useful for three things:
1. Week 1 smoke tests without any external services.
2. CI jobs that want to validate EvalOps logic without racing real LLMs.
3. Regression tests for the judge engine: deterministic inputs produce
   deterministic outputs.

The mock behaves differently per CaseKind:
- RAG cases: returns the first expected ground-truth answer if present,
  otherwise a canned 'I do not know' string. It also echoes expected
  source snippets so the citation judge has something to score.
- Agent cases: walks the expected action trace and synthesizes plausible
  thought/action/observation tuples. Supports a `fail_after_step` rubric
  hint so we can verify the error-recovery path without a real Agent.
- Chat cases: echoes input with a prefix.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from evalops.models import Case, CaseKind, Cost, Metadata, SutOutput

from .base import SutAdapter


class MockAdapter(SutAdapter):
    async def call(self, case: Case, metadata: Metadata) -> SutOutput:
        # Simulate some latency so summaries look realistic
        latency_ms = random.randint(40, 120)
        await asyncio.sleep(latency_ms / 1000)

        if case.kind == CaseKind.RAG:
            return self._rag(case, latency_ms)
        if case.kind == CaseKind.AGENT:
            return self._agent(case, latency_ms)
        if case.kind == CaseKind.CHAT:
            return self._chat(case, latency_ms)
        return SutOutput(answer="", raw={"error": f"unsupported kind {case.kind}"})

    # ---- per-kind handlers ---------------------------------------------------

    def _rag(self, case: Case, latency_ms: int) -> SutOutput:
        expected_answer = (case.expected or {}).get("answer", "")
        # Controlled injection: the toy dataset marks some cases as
        # "hallucinate" so we can verify rule judges catch them.
        mode = (case.rubric or {}).get("mock_mode", "faithful")
        if mode == "hallucinate":
            answer = "The capital of France is Berlin."  # deliberately wrong
        elif mode == "refuse":
            answer = "I don't know based on the provided context."
        else:
            answer = expected_answer or "no answer"

        sources: list[dict[str, Any]] = []
        for snippet in (case.expected or {}).get("sources", []):
            sources.append({"id": snippet.get("id", "doc-0"),
                            "content": snippet.get("content", ""),
                            "score": 0.87})

        return SutOutput(
            answer=answer,
            sources=sources,
            latency_ms=latency_ms,
            cost=Cost(micro_usd=120, prompt_tokens=80, completion_tokens=30),
            raw={"adapter": "mock", "mode": mode},
        )

    def _agent(self, case: Case, latency_ms: int) -> SutOutput:
        # Build a plausible trace from the expected action sequence.
        expected_trace = (case.expected or {}).get("trace", [])
        fail_after = (case.rubric or {}).get("fail_after_step")
        trace: list[dict[str, Any]] = []
        for i, step in enumerate(expected_trace):
            if fail_after is not None and i >= fail_after:
                trace.append({
                    "step": i,
                    "thought": "tool unavailable; giving up",
                    "action": {"tool": step.get("tool"), "args": step.get("args")},
                    "observation": {"error": "tool returned 500"},
                })
                break
            trace.append({
                "step": i,
                "thought": step.get("thought", ""),
                "action": {"tool": step.get("tool"), "args": step.get("args")},
                "observation": step.get("observation", {}),
            })

        final_answer = (case.expected or {}).get("final_answer", "")
        return SutOutput(
            answer=final_answer if fail_after is None else "",
            agent_trace=trace,
            latency_ms=latency_ms,
            cost=Cost(micro_usd=450, prompt_tokens=250, completion_tokens=120),
            raw={"adapter": "mock", "fail_after": fail_after},
        )

    def _chat(self, case: Case, latency_ms: int) -> SutOutput:
        message = case.input.get("message", "")
        return SutOutput(
            answer=f"(mock echo) {message}",
            latency_ms=latency_ms,
            cost=Cost(micro_usd=60, prompt_tokens=30, completion_tokens=15),
        )
