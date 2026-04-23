"""Judge protocol — all judges (rule, LLM, agent, hybrid) implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod

from evalops.models import Case, JudgeConfig, JudgeResult, Metadata, SutOutput


class Judge(ABC):
    """A judge scores one (case, sut_output) pair.

    It can be deterministic (rule) or model-backed (LLM / agent). Either way
    it returns a uniform `JudgeResult`: a list of `MetricScore`s plus cost
    + audit trail. Unstable results (self-consistency failure, disagreement
    across repeats) are signalled via `JudgeResult.unstable`.
    """

    def __init__(self, config: JudgeConfig) -> None:
        self.config = config

    @abstractmethod
    async def score(
        self, case: Case, sut_output: SutOutput, metadata: Metadata
    ) -> JudgeResult:
        ...
