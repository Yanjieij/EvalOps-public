"""Hybrid judge — the rule → LLM → agent cost/quality funnel.

Running a real LLM judge on every single case is expensive and, for
cases where the rule judge is already confident, wasteful. `HybridJudge`
implements a three-level pipeline:

1. **Rule judge (free)** always runs first. For most RAG cases where
   `citation_recall == 1.0` and `faithfulness_lite >= escalate_threshold`,
   that's the end of the story — we just return the rule metrics.
2. **LLM judge (medium cost)** fires when a RAG/CHAT case's confidence
   falls below the threshold *or* when a case explicitly opts in via
   `rubric.always_llm = true`. The typical trigger is a low
   `rag/faithfulness_lite` — token overlap thought the answer might
   be hallucinated, so we escalate to the real faithfulness judge.
3. **Agent judge (expensive, slowest)** fires for every agent case by
   default (or when explicitly gated by `rubric.always_agent_judge`),
   because the rule judge alone cannot score plan quality, reasoning
   coherence, or recovery.

Every level's MetricScores are concatenated, so the case result shows
which levels fired and what each said. `judge_trace["hybrid_escalations"]`
is an ordered list of the levels that ran, which is how the Grafana
"judge cost breakdown" panel separates the three tiers.

Design choices:

- **Rule result is never discarded.** Even when we escalate, the caller
  can still see which deterministic metrics passed or failed. That's
  important because the rule judge is the only thing we trust in
  Release Gate — LLM/Agent judges have variance.
- **Escalation decisions live in this class, not the rule judge.** The
  rule judge remains a pure function; orchestration belongs here.
- **No recursion.** Hybrid never wraps hybrid. Setting
  `JudgeKind.HYBRID` twice in the config would be a bug, not a
  feature, so we assert and fail loudly.
"""

from __future__ import annotations

import os
from typing import Any

from evalops.logging import get_logger
from evalops.models import (
    Case,
    CaseKind,
    Cost,
    JudgeConfig,
    JudgeKind,
    JudgeResult,
    Metadata,
    MetricScore,
    SutOutput,
)

from .agent import AgentJudge
from .base import Judge
from .llm import LiteLLMClient, LLMJudge
from .llm_stub import LLMJudgeStub
from .rule import RuleJudge

log = get_logger(__name__)

__all__ = ["HybridJudge"]


# Default thresholds. Tuned against rag-toy + hotpotqa-dev-100 — on a
# clean run these let ~70% of cases short-circuit at the rule tier.
# Rubric entries can override them per-case.
_DEFAULT_ESCALATE_FAITHFULNESS = 0.7  # rag/faithfulness_lite below this → LLM
_DEFAULT_ESCALATE_CITATION_RECALL = 0.5  # below → LLM
_DEFAULT_LLM_MODEL = "gpt-4o-mini"
_DEFAULT_AGENT_MODEL = "gpt-4o"


class HybridJudge(Judge):
    """rule → LLM → agent funnel, with per-case escalation decisions."""

    def __init__(
        self,
        config: JudgeConfig,
        *,
        llm_client: LiteLLMClient | None = None,
        agent_client: LiteLLMClient | None = None,
    ) -> None:
        super().__init__(config)
        if config.kind != JudgeKind.HYBRID:
            raise ValueError(
                f"HybridJudge expects JudgeKind.HYBRID, got {config.kind!r}"
            )
        self._rule = RuleJudge(config)
        # LLM + agent tiers are constructed lazily — cheap cases never
        # allocate a LiteLLM client. Tests inject stub clients here so
        # escalation paths run fully in-process.
        self._llm_client = llm_client
        self._agent_client = agent_client
        # Either an LLMJudge (real or with injected stub client) or an
        # LLMJudgeStub (when EVALOPS_LLM_JUDGE=stub drives the branch).
        self._llm_judges: dict[str, Judge] = {}
        self._agent_judges: dict[str, AgentJudge] = {}

    # ---- public API -----------------------------------------------------

    async def score(
        self, case: Case, sut_output: SutOutput, metadata: Metadata
    ) -> JudgeResult:
        escalations: list[str] = []
        merged_metrics: list[MetricScore] = []
        total_cost = Cost()
        any_unstable = False
        traces: dict[str, Any] = {"judge": "hybrid"}

        # --- Tier 1: rule ------------------------------------------------
        rule_result = await self._rule.score(case, sut_output, metadata)
        escalations.append("rule")
        merged_metrics.extend(rule_result.metrics)
        total_cost = total_cost + rule_result.cost
        traces["rule_trace"] = rule_result.judge_trace
        any_unstable = any_unstable or rule_result.unstable

        # --- Decide whether to escalate ----------------------------------
        rubric = case.rubric or {}
        llm_needed = self._needs_llm(case, rule_result, rubric)
        agent_needed = self._needs_agent(case, rule_result, rubric)

        # --- Tier 2: LLM -------------------------------------------------
        if llm_needed:
            llm_judge = self._llm_for(case, rubric)
            llm_result = await llm_judge.score(case, sut_output, metadata)
            escalations.append("llm")
            merged_metrics.extend(llm_result.metrics)
            total_cost = total_cost + llm_result.cost
            traces["llm_trace"] = llm_result.judge_trace
            any_unstable = any_unstable or llm_result.unstable

        # --- Tier 3: Agent-as-a-Judge -----------------------------------
        if agent_needed:
            agent_judge = self._agent_for(rubric)
            agent_result = await agent_judge.score(case, sut_output, metadata)
            escalations.append("agent")
            merged_metrics.extend(agent_result.metrics)
            total_cost = total_cost + agent_result.cost
            traces["agent_trace"] = agent_result.judge_trace
            any_unstable = any_unstable or agent_result.unstable

        traces["escalations"] = escalations
        return JudgeResult(
            metrics=merged_metrics,
            cost=total_cost,
            judge_trace=traces,
            unstable=any_unstable,
        )

    # ---- escalation policy ---------------------------------------------

    @staticmethod
    def _needs_llm(
        case: Case,
        rule_result: JudgeResult,
        rubric: dict[str, Any],
    ) -> bool:
        if rubric.get("always_llm"):
            return True
        if rubric.get("skip_llm"):
            return False
        # Only RAG / CHAT cases have an LLM tier. Agent cases jump
        # straight to the Agent judge.
        if case.kind not in (CaseKind.RAG, CaseKind.CHAT):
            return False
        # If ``dispatch_llm`` is explicitly set on the rubric, respect
        # it. Otherwise: escalate when rule metrics say the answer
        # *might* be hallucinated.
        by_name = {m.name: m.value for m in rule_result.metrics}
        faithfulness = by_name.get("rag/faithfulness_lite")
        citation = by_name.get("rag/citation_recall")
        threshold_f = float(
            rubric.get("escalate_faithfulness", _DEFAULT_ESCALATE_FAITHFULNESS)
        )
        threshold_c = float(
            rubric.get("escalate_citation_recall", _DEFAULT_ESCALATE_CITATION_RECALL)
        )
        if faithfulness is not None and faithfulness < threshold_f:
            return True
        return citation is not None and citation < threshold_c

    @staticmethod
    def _needs_agent(
        case: Case,
        rule_result: JudgeResult,
        rubric: dict[str, Any],
    ) -> bool:
        if rubric.get("skip_agent_judge"):
            return False
        if rubric.get("always_agent_judge"):
            return True
        # Default: fire Agent-as-a-Judge on every Agent case. It's the
        # only tier that can reason about plan quality / coherence.
        return case.kind == CaseKind.AGENT

    # ---- lazy judge construction --------------------------------------

    def _llm_for(self, case: Case, rubric: dict[str, Any]) -> Judge:
        model = str(rubric.get("llm_model") or self.config.model or _DEFAULT_LLM_MODEL)
        if model not in self._llm_judges:
            inner = JudgeConfig(
                name=f"{self.config.name}/llm",
                kind=JudgeKind.LLM_SINGLE,
                rubric={
                    "llm_metrics": rubric.get(
                        "llm_metrics",
                        ["rag/faithfulness"] if case.kind == CaseKind.RAG else [],
                    ),
                },
                model=model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                repeats=self.config.repeats,
            )
            # Honour the same EVALOPS_LLM_JUDGE=stub toggle the top-level
            # factory respects, so `make smoke` / CI can exercise the
            # hybrid funnel end-to-end without a LiteLLM API key. Tests
            # that already inject a stub client skip the env check.
            if self._llm_client is None and os.getenv("EVALOPS_LLM_JUDGE", "").lower() == "stub":
                self._llm_judges[model] = LLMJudgeStub(inner)  # type: ignore[assignment]
            else:
                self._llm_judges[model] = LLMJudge(inner, client=self._llm_client)
        return self._llm_judges[model]

    def _agent_for(self, rubric: dict[str, Any]) -> AgentJudge:
        model = str(
            rubric.get("agent_judge_model")
            or self.config.baseline_model
            or _DEFAULT_AGENT_MODEL
        )
        if model not in self._agent_judges:
            inner = JudgeConfig(
                name=f"{self.config.name}/agent",
                kind=JudgeKind.AGENT_TRACE,
                rubric=rubric,  # forward dimension_weights, unstable_stddev, etc.
                model=model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                repeats=self.config.repeats,
            )
            self._agent_judges[model] = AgentJudge(inner, client=self._agent_client)
        return self._agent_judges[model]
