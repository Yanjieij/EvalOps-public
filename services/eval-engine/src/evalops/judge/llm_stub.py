"""Placeholder LLM judge for Week 1.

Emits a deterministic score derived from the rule judge so the runner's
end-to-end path exercises `JudgeKind.LLM_*`. Week 2 replaces this with the
real GPT-4o / Claude 3.5 Sonnet implementation, the swap+vote position
bias mitigation, and self-consistency.
"""

from __future__ import annotations

from evalops.models import Case, JudgeResult, Metadata, MetricScore, SutOutput

from .base import Judge
from .rule import RuleJudge


class LLMJudgeStub(Judge):
    async def score(
        self, case: Case, sut_output: SutOutput, metadata: Metadata
    ) -> JudgeResult:
        underlying = await RuleJudge(self.config).score(case, sut_output, metadata)
        # Wrap the rule metric in an "llm_overall" metric so dashboards can
        # tell the two apart once the real LLM judge lands.
        if underlying.metrics:
            avg = sum(m.value for m in underlying.metrics) / len(underlying.metrics)
        else:
            avg = 0.0
        overall = MetricScore(
            name="llm/overall",
            value=avg,
            confidence=0.5,
            rationale="Week 1 stub — derived from rule judge, not an actual LLM call",
        )
        return JudgeResult(
            metrics=[*underlying.metrics, overall],
            judge_trace={
                "judge": "llm_stub",
                "kind": self.config.kind,
                "note": "Week 1 stub; see services/eval-engine/src/evalops/judge/llm_stub.py",
            },
        )


# The class is intentionally the only export — the Week 2 replacement will
# subclass this or take its place in the __init__ registry.
__all__ = ["LLMJudgeStub"]
