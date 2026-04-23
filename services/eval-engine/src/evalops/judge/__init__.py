"""Judge engine — rule / LLM / agent / hybrid.

Week 2 replaced the Week 1 stub with a LiteLLM-backed LLM judge.
Week 3 adds the Agent-as-a-Judge trace auditor and the Hybrid funnel
that escalates rule → LLM → agent by case and confidence.

The ``EVALOPS_LLM_JUDGE=stub`` environment variable still maps
``LLM_SINGLE``/``LLM_PAIRWISE``/``LLM_DUAL`` to the deterministic
stub so CI can exercise the paths without burning tokens.
"""

from __future__ import annotations

import os

from evalops.models import JudgeConfig, JudgeKind

from .agent import AgentJudge
from .base import Judge
from .hybrid import HybridJudge
from .llm import LLMJudge
from .llm_stub import LLMJudgeStub
from .rule import RuleJudge


def build_judge(config: JudgeConfig) -> Judge:
    """Resolve a JudgeConfig to a concrete Judge instance."""
    if config.kind == JudgeKind.RULE:
        return RuleJudge(config)
    if config.kind in (JudgeKind.LLM_SINGLE, JudgeKind.LLM_PAIRWISE, JudgeKind.LLM_DUAL):
        if os.getenv("EVALOPS_LLM_JUDGE", "").lower() == "stub":
            return LLMJudgeStub(config)
        return LLMJudge(config)
    if config.kind == JudgeKind.AGENT_TRACE:
        # Agent-as-a-Judge has no stub path — agent traces are too
        # structured for the rule-derived stub. Tests inject a stub
        # LiteLLMClient directly via the class constructor instead.
        return AgentJudge(config)
    if config.kind == JudgeKind.HYBRID:
        return HybridJudge(config)
    raise ValueError(f"No judge implementation for kind {config.kind!r}")


__all__ = [
    "AgentJudge",
    "HybridJudge",
    "Judge",
    "LLMJudge",
    "LLMJudgeStub",
    "RuleJudge",
    "build_judge",
]
