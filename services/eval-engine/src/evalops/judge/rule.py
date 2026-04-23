"""Rule-based judge — deterministic, free, first line of the hybrid funnel.

The rule judge dispatches on `Case.kind` and emits a kind-appropriate set
of metrics. The rubric on each case may toggle which metrics are computed
and provide a pass threshold; if no rubric override is present we fall
back to sensible defaults per kind.
"""

from __future__ import annotations

from evalops.models import (
    Case,
    CaseKind,
    JudgeResult,
    Metadata,
    MetricScore,
    SutOutput,
)

from .base import Judge
from .metrics import (
    best_f1,
    citation_recall,
    context_precision,
    exact_match,
    faithfulness_lite,
    substring_match,
    tool_selection_accuracy,
)


class RuleJudge(Judge):
    async def score(
        self, case: Case, sut_output: SutOutput, metadata: Metadata
    ) -> JudgeResult:
        if case.kind == CaseKind.RAG:
            metrics = self._rag_metrics(case, sut_output)
        elif case.kind == CaseKind.AGENT:
            metrics = self._agent_metrics(case, sut_output)
        elif case.kind == CaseKind.CHAT:
            metrics = self._chat_metrics(case, sut_output)
        else:
            metrics = []

        trace = {
            "judge": "rule",
            "case_kind": case.kind,
            "rubric_keys": sorted((case.rubric or {}).keys()),
        }
        return JudgeResult(metrics=metrics, judge_trace=trace)

    # --- per-kind metric sets ------------------------------------------------

    def _rag_metrics(self, case: Case, sut_output: SutOutput) -> list[MetricScore]:
        expected_answers: list[str] = []
        primary = (case.expected or {}).get("answer")
        if isinstance(primary, str) and primary:
            expected_answers.append(primary)
        for alt in (case.expected or {}).get("aliases", []) or []:
            if isinstance(alt, str):
                expected_answers.append(alt)

        em = exact_match(sut_output.answer, expected_answers)
        sub = substring_match(sut_output.answer, expected_answers)
        f1 = best_f1(sut_output.answer, expected_answers)

        # Retrieval quality: citation recall + context precision.
        expected_sources = (case.expected or {}).get("source_ids", []) or []
        returned_source_ids = [
            s.get("id", "") for s in (sut_output.sources or []) if s.get("id")
        ]
        cit = citation_recall(returned_source_ids, expected_sources)
        cp = context_precision(returned_source_ids, expected_sources)

        # Lightweight faithfulness proxy against the retrieved context.
        context_text = " ".join(
            (s.get("content") or "") for s in (sut_output.sources or [])
        )
        faith = faithfulness_lite(sut_output.answer, context_text)

        # Unanswerable detection — if rubric.expected_refusal is true, we
        # reward an explicit "I don't know" style answer and punish fabrication.
        refusal_metric: list[MetricScore] = []
        if (case.rubric or {}).get("expected_refusal"):
            refuses = any(
                phrase in sut_output.answer.lower()
                for phrase in [
                    "i don't know",
                    "i do not know",
                    "not enough",
                    "cannot determine",
                    "无法确定",
                    "不知道",
                ]
            )
            refusal_metric.append(
                MetricScore(
                    name="rag/unanswerable_handling",
                    value=1.0 if refuses else 0.0,
                    rationale="expected refusal" if refuses else "fabricated an answer",
                )
            )

        return [
            MetricScore(name="rag/exact_match", value=em),
            MetricScore(name="rag/substring_match", value=sub),
            MetricScore(name="rag/f1", value=f1),
            MetricScore(name="rag/citation_recall", value=cit),
            MetricScore(name="rag/context_precision", value=cp),
            MetricScore(name="rag/faithfulness_lite", value=faith),
            *refusal_metric,
        ]

    def _agent_metrics(self, case: Case, sut_output: SutOutput) -> list[MetricScore]:
        expected = case.expected or {}
        expected_trace = expected.get("trace", []) or []
        expected_final = expected.get("final_answer", "") or ""

        final_em = exact_match(sut_output.answer, [expected_final]) if expected_final else 0.0
        final_f1 = best_f1(sut_output.answer, [expected_final]) if expected_final else 0.0
        tool_acc = tool_selection_accuracy(sut_output.agent_trace, expected_trace)

        plan_len_ratio = 0.0
        if expected_trace:
            pred_len = max(1, len(sut_output.agent_trace))
            plan_len_ratio = min(1.0, len(expected_trace) / pred_len)

        # Error recovery: if the rubric injected a tool failure, we credit
        # the agent only if it still produced a non-empty final answer.
        recovery: list[MetricScore] = []
        if (case.rubric or {}).get("inject_failure"):
            recovered = bool(sut_output.answer) or any(
                "retry" in (step.get("thought") or "").lower()
                for step in sut_output.agent_trace
            )
            recovery.append(
                MetricScore(
                    name="agent/error_recovery",
                    value=1.0 if recovered else 0.0,
                    rationale="recovered from injected failure" if recovered else "gave up",
                )
            )

        return [
            MetricScore(name="agent/final_em", value=final_em),
            MetricScore(name="agent/final_f1", value=final_f1),
            MetricScore(name="agent/tool_selection", value=tool_acc),
            MetricScore(name="agent/plan_efficiency", value=plan_len_ratio),
            *recovery,
        ]

    def _chat_metrics(self, case: Case, sut_output: SutOutput) -> list[MetricScore]:
        expected = (case.expected or {}).get("answer", "")
        if not expected:
            return [MetricScore(name="chat/non_empty",
                                value=1.0 if sut_output.answer else 0.0)]
        return [
            MetricScore(name="chat/exact_match", value=exact_match(sut_output.answer, [expected])),
            MetricScore(name="chat/f1", value=best_f1(sut_output.answer, [expected])),
        ]
