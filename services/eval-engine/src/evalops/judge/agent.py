"""Agent-as-a-Judge — EvalOps's core differentiator for agent eval.

Most LLM-based agent evaluation frameworks score the agent on its final
answer and call it done. That throws away the interesting signal: *how*
the agent got there. An agent can stumble onto the right final answer
through broken reasoning, luck, or memorization, and an answer-only
judge will happily give it a perfect score.

`AgentJudge` handles ``JudgeKind.AGENT_TRACE``. It hands the judge model
(via LiteLLM) the full ReAct trace from ``SutOutput.agent_trace`` — every
thought, every action+args, every observation — plus the original task
and available toolset, and asks it for a 4-dimensional verdict:

    plan_quality          — was the decomposition sensible?
    tool_selection        — was each tool the right choice?
    reasoning_coherence   — does the chain-of-thought stay consistent?
    error_recovery        — when tools failed, did the agent adapt?

Each dimension returns a 0..1 score with a one-sentence rationale. We
then emit them as individual ``MetricScore`` entries so the capability
radar, version diff, and Cohen's κ aggregator all see per-dim values
without any extra plumbing. An ``agent_judge/overall`` metric (the mean
across dims, weighted by ``rubric.dimension_weights`` if provided) is
appended for dashboards that want a single headline number.

Testing: never hits a real API. Unit tests pass a ``LiteLLMClient``
subclass that returns canned JSON, same pattern as ``test_judge_llm``.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from evalops.logging import get_logger
from evalops.models import (
    Case,
    Cost,
    JudgeConfig,
    JudgeKind,
    JudgeResult,
    Metadata,
    MetricScore,
    SutOutput,
)

from .base import Judge
from .llm import LiteLLMClient, _parse_json_object
from .prompts import AGENT_TRACE_DIMENSIONS, AGENT_TRACE_USER, SYSTEM_PROMPT

log = get_logger(__name__)

__all__ = ["AgentJudge"]


# Hard cap on how much trace we ship to the judge. Long traces blow up
# token budgets and give the judge more room to cherry-pick. 40 steps
# is well beyond anything EvalOps's current benchmarks produce (τ-bench-
# lite maxes out around 6 steps) and still leaves headroom for Week 4's
# longer multi-turn agent benchmarks.
_MAX_TRACE_STEPS = 40


def _render_trace(trace: list[dict[str, Any]]) -> str:
    """Serialize a ReAct trace into a deterministic, judge-friendly string.

    Each step becomes a compact one-line JSON object. We truncate long
    observation blobs (file_read can return kilobytes) so the judge
    doesn't waste its attention window on raw file content.
    """
    if not trace:
        return "(no steps taken)"

    clipped = trace[:_MAX_TRACE_STEPS]
    lines: list[str] = []
    for i, step in enumerate(clipped):
        action = step.get("action") or {}
        observation = step.get("observation") or {}
        # Clip any string field in the observation to keep the prompt
        # bounded. We preserve the keys so the judge can still see
        # what was returned, just not a 10KB blob.
        clipped_obs: dict[str, Any] = {}
        for k, v in observation.items():
            if isinstance(v, str) and len(v) > 280:
                clipped_obs[k] = v[:280] + "…"
            else:
                clipped_obs[k] = v
        lines.append(
            json.dumps(
                {
                    "step": i,
                    "thought": step.get("thought", ""),
                    "action": {
                        "tool": action.get("tool", ""),
                        "args": action.get("args", {}),
                    },
                    "observation": clipped_obs,
                },
                ensure_ascii=False,
            )
        )
    if len(trace) > _MAX_TRACE_STEPS:
        lines.append(f'... (truncated, total {len(trace)} steps)')
    return "\n".join(lines)


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


class AgentJudge(Judge):
    """LiteLLM-backed 4-dim trace audit.

    Parameters
    ----------
    config : JudgeConfig
        Must have ``kind == JudgeKind.AGENT_TRACE`` and a non-empty
        ``model``. ``rubric.dimension_weights`` can override the default
        equal weighting for the ``agent_judge/overall`` aggregate.
    client : LiteLLMClient, optional
        Defaults to a new ``LiteLLMClient``. Tests inject a stub.
    """

    def __init__(
        self,
        config: JudgeConfig,
        *,
        client: LiteLLMClient | None = None,
    ) -> None:
        super().__init__(config)
        if config.kind != JudgeKind.AGENT_TRACE:
            raise ValueError(
                f"AgentJudge expects JudgeKind.AGENT_TRACE, got {config.kind!r}"
            )
        self.client = client or LiteLLMClient()

    async def score(
        self, case: Case, sut_output: SutOutput, metadata: Metadata
    ) -> JudgeResult:
        task = (case.input or {}).get("task", "")
        tools = (case.input or {}).get("tools") or []
        tools_str = ", ".join(tools) if tools else "(no explicit toolset)"
        trace_str = _render_trace(sut_output.agent_trace)
        final_answer = sut_output.answer or "(empty)"

        user = AGENT_TRACE_USER.format(
            task=task,
            tools=tools_str,
            trace=trace_str,
            final_answer=final_answer,
        )

        total_cost = Cost()
        raw_calls: list[dict[str, Any]] = []
        dim_scores: dict[str, list[float]] = {d: [] for d in AGENT_TRACE_DIMENSIONS}
        dim_rationales: dict[str, str] = {d: "" for d in AGENT_TRACE_DIMENSIONS}
        any_parse_error = False

        reps = max(1, self.config.repeats)
        for rep in range(reps):
            out = self.client.complete(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                response_format={"type": "json_object"},
            )
            total_cost = total_cost + Cost(
                prompt_tokens=out["prompt_tokens"],
                completion_tokens=out["completion_tokens"],
            )
            try:
                parsed = _parse_json_object(out["content"])
            except Exception as exc:
                any_parse_error = True
                raw_calls.append({"rep": rep, "error": str(exc), "raw": out["content"]})
                continue

            call_dump: dict[str, Any] = {"rep": rep, "parsed": {}}
            for dim in AGENT_TRACE_DIMENSIONS:
                entry = parsed.get(dim) or {}
                if not isinstance(entry, dict):
                    # Sometimes judges return a bare float.
                    try:
                        score = _clip01(float(entry))
                    except (TypeError, ValueError):
                        score = 0.0
                    rationale = ""
                else:
                    score = _clip01(float(entry.get("score", 0.0)))
                    rationale = str(entry.get("rationale", ""))
                dim_scores[dim].append(score)
                if not dim_rationales[dim]:
                    dim_rationales[dim] = rationale
                call_dump["parsed"][dim] = {"score": score, "rationale": rationale}
            raw_calls.append(call_dump)

        # Self-consistency: if any dim's stddev exceeds the threshold,
        # we flag the whole case unstable. Matches the LLMJudge policy
        # so the dashboard has one definition of "unstable".
        any_unstable = any_parse_error
        dim_means: dict[str, float] = {}
        dim_stdevs: dict[str, float] = {}
        threshold = float((case.rubric or {}).get("unstable_stddev", 0.15))
        for dim in AGENT_TRACE_DIMENSIONS:
            samples = dim_scores[dim]
            if not samples:
                dim_means[dim] = 0.0
                dim_stdevs[dim] = 0.0
                continue
            mean = statistics.fmean(samples)
            stdev = statistics.pstdev(samples) if len(samples) > 1 else 0.0
            dim_means[dim] = mean
            dim_stdevs[dim] = stdev
            if stdev > threshold:
                any_unstable = True

        # Per-dim metrics
        metrics: list[MetricScore] = []
        for dim in AGENT_TRACE_DIMENSIONS:
            metrics.append(
                MetricScore(
                    name=f"agent_judge/{dim}",
                    value=dim_means[dim],
                    confidence=1.0 - min(dim_stdevs[dim] * 2, 1.0),
                    rationale=dim_rationales[dim],
                )
            )

        # Overall — equal-weight mean unless rubric overrides.
        weights_raw = (case.rubric or {}).get("dimension_weights") or {}
        weights: dict[str, float] = {}
        for dim in AGENT_TRACE_DIMENSIONS:
            try:
                weights[dim] = float(weights_raw.get(dim, 1.0))
            except (TypeError, ValueError):
                weights[dim] = 1.0
        weight_sum = sum(weights.values()) or 1.0
        overall = sum(dim_means[d] * weights[d] for d in AGENT_TRACE_DIMENSIONS) / weight_sum
        metrics.append(
            MetricScore(
                name="agent_judge/overall",
                value=overall,
                rationale="weighted mean of 4 dimensions"
                if any(w != 1.0 for w in weights.values())
                else "equal-weight mean of 4 dimensions",
            )
        )

        return JudgeResult(
            metrics=metrics,
            cost=total_cost,
            judge_trace={
                "judge": "agent_trace",
                "model": self.config.model,
                "dims": AGENT_TRACE_DIMENSIONS,
                "calls": raw_calls,
                "stdevs": dim_stdevs,
            },
            unstable=any_unstable,
        )
