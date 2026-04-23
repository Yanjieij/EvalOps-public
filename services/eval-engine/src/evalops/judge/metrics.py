"""Deterministic metric primitives shared across rule and hybrid judges.

Intentionally small and dependency-free: EM, F1, substring, citation
recall. Richer metrics (BLEU, ROUGE, ragas Faithfulness) come in Week 2
when we add the optional ``evalops[rag-metrics]`` extra.
"""

from __future__ import annotations

import re
import string
from collections.abc import Iterable

_PUNC_TABLE = str.maketrans("", "", string.punctuation)
_ARTICLES = re.compile(r"\b(a|an|the)\b", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """SQuAD-style normalization: lowercase, strip punct/articles, collapse spaces."""
    if text is None:
        return ""
    t = text.lower().translate(_PUNC_TABLE)
    t = _ARTICLES.sub(" ", t)
    return _WHITESPACE.sub(" ", t).strip()


def exact_match(prediction: str, references: Iterable[str]) -> float:
    pred = normalize(prediction)
    for ref in references:
        if pred == normalize(ref):
            return 1.0
    return 0.0


def substring_match(prediction: str, references: Iterable[str]) -> float:
    pred = normalize(prediction)
    for ref in references:
        if normalize(ref) in pred:
            return 1.0
    return 0.0


def f1_score(prediction: str, reference: str) -> float:
    """Token-level F1 (SQuAD-style)."""
    pred_tokens = normalize(prediction).split()
    ref_tokens = normalize(reference).split()
    if not pred_tokens and not ref_tokens:
        return 1.0
    if not pred_tokens or not ref_tokens:
        return 0.0
    common: dict[str, int] = {}
    for tok in pred_tokens:
        if tok in ref_tokens:
            common[tok] = min(pred_tokens.count(tok), ref_tokens.count(tok))
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(ref_tokens)
    return 2 * precision * recall / (precision + recall)


def best_f1(prediction: str, references: Iterable[str]) -> float:
    refs = list(references)
    if not refs:
        return 0.0
    return max(f1_score(prediction, ref) for ref in refs)


def citation_recall(
    returned_source_ids: Iterable[str],
    expected_source_ids: Iterable[str],
) -> float:
    expected = set(expected_source_ids)
    if not expected:
        return 1.0
    returned = set(returned_source_ids)
    hit = len(expected & returned)
    return hit / len(expected)


def tool_selection_accuracy(
    predicted_trace: list[dict],
    expected_trace: list[dict],
) -> float:
    """Fraction of expected steps whose tool name matches the prediction in order."""
    if not expected_trace:
        return 1.0 if not predicted_trace else 0.0
    matches = 0
    for i, expected_step in enumerate(expected_trace):
        if i >= len(predicted_trace):
            break
        pred_tool = (predicted_trace[i].get("action") or {}).get("tool")
        if pred_tool == expected_step.get("tool"):
            matches += 1
    return matches / len(expected_trace)


# ---------- Deep RAG metrics (no LLM, lightweight) ------------------------

def context_precision(
    returned_source_ids: Iterable[str],
    expected_source_ids: Iterable[str],
) -> float:
    """Of the retrieved chunks, what fraction are actually relevant?

    Dual of ``citation_recall``. A retriever that returns 10 chunks
    when 2 are relevant scores 0.2 here even if recall is 1.0.
    Useful for detecting verbose retrievers that drown the generator
    in noise.
    """
    returned = list(returned_source_ids)
    if not returned:
        return 1.0  # degenerate: no retrieval, no wasted budget
    expected = set(expected_source_ids)
    if not expected:
        return 0.0  # no ground truth → any retrieval is wasted
    hits = sum(1 for r in returned if r in expected)
    return hits / len(returned)


# Tokens that carry ~zero evidential value when checking faithfulness.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "and", "or", "in", "on", "at", "to", "for", "with", "by",
    "as", "that", "this", "these", "those", "it", "its", "he", "she",
    "they", "we", "i", "you", "from", "but", "not", "no", "yes",
    "s", "t", "d", "ll", "re", "ve", "m",
}


def _content_tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t and t not in _STOPWORDS}


def faithfulness_lite(answer: str, context_text: str) -> float:
    """Token-overlap faithfulness proxy: does every content token in
    the answer also appear somewhere in the retrieved context?

    This is the cheapest possible signal. It catches blatant
    hallucinations ("the capital of France is Berlin" where neither
    "Berlin" nor "France" appears in the retrieved chunk), but misses
    plausible paraphrases and fabricated details shared with the
    context. Week 3's hybrid judge escalates low-scoring cases to the
    LLM faithfulness judge for a real verdict.

    Returns ``|answer_tokens ∩ context_tokens| / |answer_tokens|``;
    1.0 if the answer has no content tokens (nothing to ground).
    """
    answer_tokens = _content_tokens(answer)
    if not answer_tokens:
        return 1.0
    context_tokens = _content_tokens(context_text)
    if not context_tokens:
        return 0.0
    return len(answer_tokens & context_tokens) / len(answer_tokens)
