"""Unit tests for rule-judge metric primitives."""

from __future__ import annotations

import pytest

from evalops.judge.metrics import (
    best_f1,
    citation_recall,
    context_precision,
    exact_match,
    f1_score,
    faithfulness_lite,
    normalize,
    substring_match,
    tool_selection_accuracy,
)


def test_normalize_handles_articles_and_punct():
    assert normalize("The Paris, France!") == "paris france"


def test_exact_match_single():
    assert exact_match("Paris", ["paris"]) == 1.0
    assert exact_match("Lyon", ["paris"]) == 0.0


def test_exact_match_alias():
    assert exact_match("the city of Paris", ["Paris", "the city of Paris"]) == 1.0


def test_f1_full_overlap():
    assert f1_score("paris", "paris") == pytest.approx(1.0)


def test_f1_no_overlap():
    assert f1_score("paris", "lyon") == pytest.approx(0.0)


def test_best_f1_picks_best_reference():
    assert best_f1("the city of light", ["paris", "the city of light"]) == pytest.approx(1.0)


def test_substring_match_positive_and_negative():
    assert substring_match("the capital is paris.", ["paris"]) == 1.0
    assert substring_match("the capital is lyon.", ["paris"]) == 0.0


def test_citation_recall_full_hit():
    assert citation_recall(["a", "b"], ["a"]) == 1.0


def test_citation_recall_miss():
    assert citation_recall(["c"], ["a", "b"]) == 0.0


def test_citation_recall_empty_expected_is_trivially_one():
    assert citation_recall([], []) == 1.0


def test_tool_selection_all_match():
    pred = [
        {"action": {"tool": "rag_query"}},
        {"action": {"tool": "calc"}},
    ]
    expected = [{"tool": "rag_query"}, {"tool": "calc"}]
    assert tool_selection_accuracy(pred, expected) == 1.0


def test_tool_selection_partial_match():
    pred = [
        {"action": {"tool": "rag_query"}},
        {"action": {"tool": "file_read"}},
    ]
    expected = [{"tool": "rag_query"}, {"tool": "calc"}]
    assert tool_selection_accuracy(pred, expected) == pytest.approx(0.5)


# --- Deep RAG metrics -----------------------------------------------------


def test_context_precision_all_hits():
    assert context_precision(["a", "b"], ["a", "b", "c"]) == 1.0


def test_context_precision_mixed():
    # 1 relevant out of 3 returned -> 1/3
    assert context_precision(["a", "x", "y"], ["a"]) == pytest.approx(1 / 3)


def test_context_precision_empty_returned_is_one():
    # no retrieval at all = no wasted budget -> 1.0
    assert context_precision([], ["a"]) == 1.0


def test_context_precision_empty_expected_is_zero():
    # retrieved something but there's no ground truth relevance -> 0
    assert context_precision(["a"], []) == 0.0


def test_faithfulness_lite_full_support():
    answer = "The capital is Paris."
    context = "France is a country in Europe. Its capital is Paris."
    assert faithfulness_lite(answer, context) == pytest.approx(1.0)


def test_faithfulness_lite_hallucination():
    answer = "The capital of France is Berlin."
    context = "France is a country in Europe."
    # "capital" is in context, but "Berlin" and "France" tokens aren't
    # all supported — partial but low score.
    score = faithfulness_lite(answer, context)
    assert 0.0 <= score < 0.7


def test_faithfulness_lite_empty_answer_is_trivially_faithful():
    assert faithfulness_lite("", "some context") == 1.0


def test_faithfulness_lite_no_context_is_zero():
    assert faithfulness_lite("Paris is the capital.", "") == 0.0
