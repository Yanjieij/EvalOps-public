"""LLM-as-a-Judge via LiteLLM.

Handles three of the five judge kinds:

- **LLM_SINGLE**: one model, one rubric, one score per metric.
- **LLM_PAIRWISE**: winner / loser vs. a baseline, with position-swap +
  majority vote mitigation for position bias.
- **LLM_DUAL**: two different providers score the same output; Cohen's
  kappa across the two rating vectors is our human-annotator-free
  inter-agreement proxy (GPT-4o × Claude 3.5 Sonnet by default).

Self-consistency: every call can be repeated ``config.repeats`` times
and the per-metric variance is checked against a threshold. Anything
above the threshold flags the result as ``unstable`` in the
``JudgeResult`` so the dashboard can highlight it.

Provider-agnostic by design: LiteLLM takes a model string like
"gpt-4o-2024-08-06", "claude-3-5-sonnet-20240620", "zhipu/glm-4-plus",
"gemini/gemini-1.5-pro", and routes it. No code branches on provider.
"""

from __future__ import annotations

import json
import os
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
from .prompts import PAIRWISE_USER, SINGLE_SCORE_TEMPLATES, SYSTEM_PROMPT

__all__ = [
    "LLMJudge",
    "LiteLLMClient",
    "_bin_score",
    "cohens_kappa_from_scores",
]

log = get_logger(__name__)


# --- LiteLLM client abstraction -----------------------------------------

class LiteLLMClient:
    """Thin wrapper around litellm.completion so tests can monkeypatch it.

    The wrapper is the only place that talks to litellm directly. Tests
    replace this instance (or one of its methods) with a stub that
    returns canned responses, so no real API key is required.
    """

    def __init__(self) -> None:
        self._completion = None  # lazy: don't import litellm unless used

    def _ensure(self) -> None:
        if self._completion is None:
            try:
                import litellm  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "LiteLLM not installed. Install the 'llm-judge' extra: "
                    "pip install -e '.[llm-judge]'"
                ) from exc
            # Quiet LiteLLM's noisy default logger — we have our own.
            os.environ.setdefault("LITELLM_LOG", "ERROR")
            self._completion = litellm.completion  # type: ignore[assignment]

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return a normalized {"content": str, "usage": {...}} dict."""
        self._ensure()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format
        resp = self._completion(**kwargs)  # type: ignore[misc]
        # LiteLLM normalizes to the OpenAI schema regardless of provider.
        choice = resp.choices[0]
        content = choice.message.content or ""
        usage = getattr(resp, "usage", None) or {}
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        return {
            "content": content,
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
        }


# --- Helpers ------------------------------------------------------------


def _parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response.

    Even with ``response_format={"type": "json_object"}`` some providers
    return the JSON wrapped in markdown fences. We locate the first
    ``{`` and decode forward; if that fails we raise so the runner
    flags the case as an error instead of silently scoring zero.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # drop leading ``` / ```json
        _, _, rest = stripped.partition("\n")
        stripped = rest.rsplit("```", 1)[0].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # find the first { and parse greedily
        first = stripped.find("{")
        last = stripped.rfind("}")
        if first != -1 and last != -1 and last > first:
            return json.loads(stripped[first : last + 1])
        raise


def _context_text(sut_output: SutOutput) -> str:
    if not sut_output.sources:
        return "(no retrieved context)"
    lines = []
    for i, s in enumerate(sut_output.sources, start=1):
        content = (s.get("content") or "").replace("\n", " ")
        lines.append(f"[{i}] {content}")
    return "\n".join(lines)


# --- LLMJudge -----------------------------------------------------------


class LLMJudge(Judge):
    """Handles LLM_SINGLE / LLM_PAIRWISE / LLM_DUAL.

    Instances are stateless; the underlying ``LiteLLMClient`` owns the
    one piece of state (lazy import). Runner can instantiate one judge
    per run without worrying about connection pooling — LiteLLM handles
    HTTP keep-alive internally.
    """

    def __init__(
        self,
        config: JudgeConfig,
        *,
        client: LiteLLMClient | None = None,
        unstable_stddev_threshold: float = 0.15,
    ) -> None:
        super().__init__(config)
        self.client = client or LiteLLMClient()
        self.unstable_stddev_threshold = unstable_stddev_threshold

    # ---- main dispatch ---------------------------------------------------

    async def score(
        self, case: Case, sut_output: SutOutput, metadata: Metadata
    ) -> JudgeResult:
        kind = self.config.kind
        if kind == JudgeKind.LLM_SINGLE:
            return self._score_single(case, sut_output)
        if kind == JudgeKind.LLM_PAIRWISE:
            return self._score_pairwise(case, sut_output)
        if kind == JudgeKind.LLM_DUAL:
            return self._score_dual(case, sut_output)
        raise ValueError(f"LLMJudge does not handle kind {kind!r}")

    # ---- single score ---------------------------------------------------

    def _score_single(self, case: Case, sut_output: SutOutput) -> JudgeResult:
        metrics_to_compute: list[str] = (case.rubric or {}).get(
            "llm_metrics", ["rag/faithfulness", "rag/answer_relevancy"]
        )
        total_cost = Cost()
        all_metrics: list[MetricScore] = []
        trace: dict[str, Any] = {"judge": "llm_single", "model": self.config.model, "calls": []}
        any_unstable = False

        question = (case.input or {}).get("query") or (case.input or {}).get("question", "")
        context = _context_text(sut_output)
        response = sut_output.answer

        for metric_name in metrics_to_compute:
            template = SINGLE_SCORE_TEMPLATES.get(metric_name)
            if template is None:
                all_metrics.append(
                    MetricScore(
                        name=metric_name,
                        value=0.0,
                        confidence=0.0,
                        rationale=f"no prompt template for {metric_name!r}",
                    )
                )
                continue

            user = template.format(
                question=question, context=context, response=response
            )
            scores: list[float] = []
            rationales: list[str] = []
            for rep in range(max(1, self.config.repeats)):
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
                    score = float(parsed.get("score", 0.0))
                    rationale = str(parsed.get("rationale", ""))
                except Exception as exc:
                    trace["calls"].append(
                        {"metric": metric_name, "rep": rep, "error": str(exc)}
                    )
                    continue
                scores.append(max(0.0, min(1.0, score)))
                rationales.append(rationale)
                trace["calls"].append(
                    {"metric": metric_name, "rep": rep, "score": score, "rationale": rationale}
                )

            if not scores:
                all_metrics.append(
                    MetricScore(name=metric_name, value=0.0, confidence=0.0,
                                rationale="judge produced no valid response")
                )
                any_unstable = True
                continue

            mean = statistics.fmean(scores)
            stdev = statistics.pstdev(scores) if len(scores) > 1 else 0.0
            unstable = stdev > self.unstable_stddev_threshold
            any_unstable = any_unstable or unstable
            all_metrics.append(
                MetricScore(
                    name=metric_name,
                    value=mean,
                    confidence=1.0 - min(stdev * 2, 1.0),
                    rationale=rationales[0],
                )
            )

        return JudgeResult(
            metrics=all_metrics,
            cost=total_cost,
            judge_trace=trace,
            unstable=any_unstable,
        )

    # ---- pairwise -------------------------------------------------------

    def _score_pairwise(self, case: Case, sut_output: SutOutput) -> JudgeResult:
        """Compare the SUT's answer against a baseline in case.expected.

        ``case.expected["baseline_answer"]`` holds the reference; we
        call the judge twice — once with (SUT=A, baseline=B), once
        swapped — and take the majority vote. This is the cheapest
        effective mitigation for LLM position bias (see Zheng et al.
        2023, "Judging LLM-as-a-Judge").
        """
        baseline = (case.expected or {}).get("baseline_answer", "")
        if not baseline:
            return JudgeResult(
                metrics=[
                    MetricScore(
                        name="llm/pairwise",
                        value=0.0,
                        confidence=0.0,
                        rationale="no baseline_answer on case.expected",
                    )
                ],
                unstable=True,
            )
        criterion = (case.rubric or {}).get(
            "pairwise_criterion",
            "overall correctness and answer relevancy",
        )
        question = (case.input or {}).get("query") or (case.input or {}).get("question", "")
        vote_sut, trace = self._pairwise_with_swap(
            question=question,
            sut_answer=sut_output.answer,
            baseline_answer=baseline,
            criterion=criterion,
        )
        # Map {WIN, LOSE, TIE} -> {1.0, 0.0, 0.5}
        value = {"WIN": 1.0, "LOSE": 0.0, "TIE": 0.5}[vote_sut]
        return JudgeResult(
            metrics=[
                MetricScore(
                    name="llm/pairwise_win_rate",
                    value=value,
                    rationale=trace.get("rationale", ""),
                )
            ],
            cost=trace.get("cost", Cost()),
            judge_trace=trace,
            unstable=trace.get("unstable", False),
        )

    def _pairwise_with_swap(
        self,
        *,
        question: str,
        sut_answer: str,
        baseline_answer: str,
        criterion: str,
    ) -> tuple[str, dict[str, Any]]:
        """Return (SUT vote, trace) where vote in {WIN, LOSE, TIE}.

        Position A is the SUT in the first call and the baseline in the
        second. If the two votes disagree we return TIE and mark unstable.
        """
        cost = Cost()
        calls: list[dict[str, Any]] = []
        votes: list[str] = []

        for rep, (a_label, b_label, a_text, b_text) in enumerate(
            [
                ("SUT", "baseline", sut_answer, baseline_answer),
                ("baseline", "SUT", baseline_answer, sut_answer),
            ]
        ):
            user = PAIRWISE_USER.format(
                criterion=criterion,
                question=question,
                response_a=a_text,
                response_b=b_text,
            )
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
            cost = cost + Cost(
                prompt_tokens=out["prompt_tokens"],
                completion_tokens=out["completion_tokens"],
            )
            parsed = _parse_json_object(out["content"])
            winner = parsed.get("winner", "TIE").upper()
            calls.append(
                {"rep": rep, "a": a_label, "b": b_label, "winner": winner,
                 "rationale": parsed.get("rationale", "")}
            )
            # Translate winner label to SUT-centric vote.
            if winner == "TIE":
                votes.append("TIE")
            elif (winner == "A" and a_label == "SUT") or (
                winner == "B" and b_label == "SUT"
            ):
                votes.append("WIN")
            else:
                votes.append("LOSE")

        if votes[0] == votes[1]:
            final = votes[0]
            unstable = False
        else:
            final = "TIE"
            unstable = True

        return final, {
            "judge": "llm_pairwise",
            "model": self.config.model,
            "calls": calls,
            "votes": votes,
            "final": final,
            "cost": cost,
            "rationale": calls[0].get("rationale", ""),
            "unstable": unstable,
        }

    # ---- dual-judge with Cohen's kappa ----------------------------------

    def _score_dual(self, case: Case, sut_output: SutOutput) -> JudgeResult:
        """Score with two providers and report per-case agreement.

        Cohen's κ is a **corpus-level** metric — computing it from a
        single score pair is meaningless. So at the case level we only
        report:

        1. The mean of the two providers' scores (for dashboards).
        2. A 3-bin per-case agreement in {0.0, 0.5, 1.0} based on bin
           distance — ``llm/dual_bin_agreement``.
        3. The raw (primary, secondary) scores stashed under
           ``judge_trace["dual_raw_pairs"]``.

        The runner's ``_summarize`` post-processing step then collects
        all dual-judge cases from a run and computes the true κ across
        the full corpus, writing it to ``RunSummary.judge_agreement``.
        """
        baseline_model = self.config.baseline_model
        if not baseline_model:
            return JudgeResult(
                metrics=[
                    MetricScore(
                        name="llm/dual",
                        value=0.0,
                        confidence=0.0,
                        rationale="JudgeConfig.baseline_model is required for LLM_DUAL",
                    )
                ],
                unstable=True,
            )

        primary_cfg = self.config.model_copy(update={"kind": JudgeKind.LLM_SINGLE})
        secondary_cfg = self.config.model_copy(
            update={"kind": JudgeKind.LLM_SINGLE, "model": baseline_model}
        )
        primary = LLMJudge(primary_cfg, client=self.client,
                           unstable_stddev_threshold=self.unstable_stddev_threshold)
        secondary = LLMJudge(secondary_cfg, client=self.client,
                             unstable_stddev_threshold=self.unstable_stddev_threshold)
        r1 = primary._score_single(case, sut_output)
        r2 = secondary._score_single(case, sut_output)

        r1_map = {m.name: m.value for m in r1.metrics}
        r2_map = {m.name: m.value for m in r2.metrics}
        shared = sorted(set(r1_map) & set(r2_map))

        merged_metrics: list[MetricScore] = []
        raw_pairs: list[dict[str, Any]] = []
        bin_hits = 0
        for name in shared:
            x, y = r1_map[name], r2_map[name]
            merged_metrics.append(
                MetricScore(
                    name=name,
                    value=(x + y) / 2,
                    confidence=1.0 - abs(x - y),
                    rationale=f"dual-judge mean across {self.config.model} and {baseline_model}",
                )
            )
            raw_pairs.append({"metric": name, "primary": x, "secondary": y})
            if _bin_score(x) == _bin_score(y):
                bin_hits += 1

        # Per-case bin agreement: how many metrics landed in the same bin.
        if shared:
            bin_agreement = bin_hits / len(shared)
            merged_metrics.append(
                MetricScore(
                    name="llm/dual_bin_agreement",
                    value=bin_agreement,
                    rationale=f"{bin_hits}/{len(shared)} metrics in same 3-way bin",
                )
            )

        return JudgeResult(
            metrics=merged_metrics,
            cost=r1.cost + r2.cost,
            judge_trace={
                "judge": "llm_dual",
                "primary_model": self.config.model,
                "secondary_model": baseline_model,
                "primary_trace": r1.judge_trace,
                "secondary_trace": r2.judge_trace,
                "dual_raw_pairs": raw_pairs,
            },
            unstable=r1.unstable or r2.unstable,
        )


# --- Cohen's kappa ------------------------------------------------------


def _bin_score(value: float) -> int:
    """Discretize a 0..1 score into one of three agreement bins.

    We use {low, mid, high} because raw floats give us near-zero chance
    of ever matching exactly. Three bins keep the metric meaningful
    without collapsing it to binary agreement.
    """
    if value < 1 / 3:
        return 0
    if value < 2 / 3:
        return 1
    return 2


def cohens_kappa_from_scores(xs: list[float], ys: list[float]) -> float:
    """Cohen's κ on paired continuous scores, after 3-way binning.

    ``len(xs)`` must equal ``len(ys)``. With n == 1 we report the
    degenerate {+1, 0, -1} — perfect / mid / total-disagreement — which
    is what individual case-level agreement lets us distinguish.
    """
    if len(xs) != len(ys) or not xs:
        return 0.0
    x_bins = [_bin_score(x) for x in xs]
    y_bins = [_bin_score(y) for y in ys]
    n = len(xs)

    # Observed agreement.
    po = sum(1 for a, b in zip(x_bins, y_bins, strict=True) if a == b) / n

    # Expected agreement under marginal independence.
    categories = [0, 1, 2]
    x_marg = {c: x_bins.count(c) / n for c in categories}
    y_marg = {c: y_bins.count(c) / n for c in categories}
    pe = sum(x_marg[c] * y_marg[c] for c in categories)

    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)
