"""Rubric prompt templates for LLM-as-a-Judge.

Every template is a system + user prompt pair. The user prompt uses
Python's ``str.format`` with keyword placeholders so they can be filled
safely with untrusted content (we f-string the plain text fields but
never the placeholders themselves).

Design rules:

1. **Rubrics are narrow, not generic.** A RAG faithfulness prompt asks
   only about faithfulness — it does not attempt to grade fluency or
   correctness. Mixing criteria causes judges to anchor on one axis and
   ignore the others. Separate metrics -> separate prompts -> separate
   calls.
2. **Every prompt ends with a structured JSON schema.** We parse the
   result with ``json.loads``; free-form scoring is what gives LLM
   judges their reputation for unreliability.
3. **Pairwise prompts always produce one of 3 labels: A / B / TIE.** No
   continuous scores in pairwise mode — that's what single-score mode
   is for. A position-swap + majority vote mitigates position bias; see
   ``llm.LLMJudge._pairwise_with_swap``.
"""

from __future__ import annotations

SYSTEM_PROMPT = (
    "You are a meticulous evaluator scoring a language-model response. "
    "Follow the rubric literally. Never invent your own criteria. Never "
    "agree with the response just to be polite — if it is wrong, say "
    "so. Reply ONLY with JSON matching the schema the user specifies."
)


# --- Single-score RAG faithfulness -------------------------------------

SINGLE_FAITHFULNESS_USER = """\
# Rubric — RAG Faithfulness (0.0–1.0)

A response is **faithful** iff every factual claim it makes is either
(a) directly supported by the provided context, or (b) clearly flagged
as uncertain / outside the context. Hallucinated facts, plausible-but-
unsupported details, and confident wrong claims all lower the score.

Scoring anchors:
- 1.0 — every claim is supported by the context, nothing fabricated.
- 0.7 — minor additions not contradicted by context.
- 0.4 — at least one unsupported confident claim mixed with supported ones.
- 0.0 — the main claim is fabricated or contradicts the context.

## Input

Question:
{question}

Context:
{context}

Response to evaluate:
{response}

## Output

Return a single JSON object:
{{"score": <float 0.0-1.0>, "rationale": "<one sentence>"}}"""


SINGLE_ANSWER_RELEVANCY_USER = """\
# Rubric — Answer Relevancy (0.0–1.0)

A response is **relevant** iff it directly answers the question asked.
A factually-correct but off-topic response scores low.

Scoring anchors:
- 1.0 — answers exactly what was asked, nothing extra.
- 0.7 — answers the question with some tangential content.
- 0.4 — partially addresses the question.
- 0.0 — ignores the question.

## Input

Question:
{question}

Response to evaluate:
{response}

## Output

Return a single JSON object:
{{"score": <float 0.0-1.0>, "rationale": "<one sentence>"}}"""


# --- Pairwise comparison ------------------------------------------------

PAIRWISE_USER = """\
# Rubric — Pairwise preference

You are given a question and two responses, A and B. Decide which
response is better according to the criterion below. If they are
equivalent, return TIE.

Criterion: {criterion}

## Input

Question:
{question}

Response A:
{response_a}

Response B:
{response_b}

## Output

Return a single JSON object:
{{"winner": "A" | "B" | "TIE", "rationale": "<one sentence>"}}"""


# Registry so JudgeConfig.rubric.metric can pick a template by name.
SINGLE_SCORE_TEMPLATES: dict[str, str] = {
    "rag/faithfulness": SINGLE_FAITHFULNESS_USER,
    "rag/answer_relevancy": SINGLE_ANSWER_RELEVANCY_USER,
}


# --- Agent-as-a-Judge: trace audit -------------------------------------
#
# This is EvalOps's core differentiator against the LLM-as-a-Judge crowd.
# Instead of asking the judge to score the Agent's *final answer*, we
# hand it the full ReAct trace (thought / action / observation tuples)
# and ask for a 4-dimensional assessment of how the Agent got there.
#
# The 4 dims are Yang et al. 2024 "Agent-as-a-Judge" style:
#
#   plan_quality         — did the Agent decompose the task sensibly?
#   tool_selection       — was each tool the right choice for its step?
#   reasoning_coherence  — does the thought chain stay internally consistent?
#   error_recovery       — when a tool failed, did the Agent adapt?
#
# Every dim is scored 0.0..1.0 with a rationale. Keeping them as
# separate fields (rather than one blended "overall") means the
# dashboard can plot per-dim drift over time and localize regressions.
#
# Rubric anchors are deliberately narrow: we tell the judge exactly
# what a 1.0 looks like, what a 0.4 looks like, and what a 0.0 looks
# like. Anchoring cuts inter-run variance by a lot in practice.

AGENT_TRACE_USER = """\
# Rubric — Agent trace audit (Agent-as-a-Judge)

You are auditing the execution trace of an autonomous agent that was given a
task, a fixed set of tools, and a step budget. Score the trace on 4 independent
dimensions. Do NOT score the final answer's correctness — a separate judge
handles that. You are only assessing the *process*.

## Dimensions (each 0.0–1.0)

**plan_quality** — did the Agent decompose the task into the right sub-steps?
- 1.0 — each step is necessary, no redundant or missing steps
- 0.7 — mostly right, maybe one redundant or slightly out-of-order step
- 0.4 — has the right idea but misses a key step or wastes budget
- 0.0 — fundamentally wrong plan, does not address the task

**tool_selection** — for every step, was the chosen tool the right one?
- 1.0 — every step uses the best available tool with sensible arguments
- 0.7 — one step uses a suboptimal but still valid tool
- 0.4 — picks the wrong tool for a critical step
- 0.0 — uses tools randomly or ignores the toolset

**reasoning_coherence** — do the thoughts stay consistent with prior observations?
- 1.0 — every thought cleanly builds on the last observation
- 0.7 — minor inconsistencies or ungrounded assumptions
- 0.4 — ignores observation content, repeats itself, or contradicts earlier steps
- 0.0 — hallucinates facts not in the trace, unrelated reasoning

**error_recovery** — when a tool failed or returned empty, did the Agent adapt?
- 1.0 — explicitly noticed the failure and tried a different tool / argument
- 0.7 — retried the same tool once, which is still a valid fallback
- 0.4 — gave up after the first failure but acknowledged it
- 0.0 — ignored the failure entirely, or no recovery was needed and the field
       should be reported as 1.0 (N/A). If there is no failure in the trace,
       always return 1.0 for this dimension.

## Input

Task given to the Agent:
{task}

Available tools: {tools}

Execution trace (step 0 first, each step is a JSON object):
{trace}

Final answer produced by the Agent:
{final_answer}

## Output

Return a single JSON object with this exact schema:
{{
  "plan_quality":         {{"score": <float 0.0-1.0>, "rationale": "<one sentence>"}},
  "tool_selection":       {{"score": <float 0.0-1.0>, "rationale": "<one sentence>"}},
  "reasoning_coherence":  {{"score": <float 0.0-1.0>, "rationale": "<one sentence>"}},
  "error_recovery":       {{"score": <float 0.0-1.0>, "rationale": "<one sentence>"}}
}}"""


# The 4 dimension names the runner / dashboard will see, in canonical
# order. Exposed as a module-level constant so tests and the hybrid
# judge can iterate without string-typing them.
AGENT_TRACE_DIMENSIONS: tuple[str, ...] = (
    "plan_quality",
    "tool_selection",
    "reasoning_coherence",
    "error_recovery",
)
