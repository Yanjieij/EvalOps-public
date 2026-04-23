"""HotpotQA format adapter.

Upstream schema (https://hotpotqa.github.io/) for each example:

    {
      "_id": "...",
      "question": "...",
      "answer": "...",
      "type": "bridge" | "comparison",
      "level": "easy" | "medium" | "hard",
      "supporting_facts": [[title, sentence_idx], ...],
      "context": [[title, [sentence, sentence, ...]], ...]
    }

We collapse each example into the EvalOps ``Case`` shape:

- ``input.query`` = question
- ``input.collection`` = "hotpotqa-distractor"
- ``expected.answer`` = gold answer
- ``expected.aliases`` = [] (HotpotQA doesn't ship aliases)
- ``expected.source_ids`` = unique titles from supporting_facts
- ``expected.supporting_sentences`` = the exact gold sentences
- ``expected.full_context`` = all paragraphs (flattened) so the judge
  can reason about faithfulness even without a real retriever hooked up
- ``rubric.primary_metric`` = "rag/f1"
- ``capability_tags`` = ["rag/multi_hop", "rag/level/<level>"]
- ``difficulty`` = 1 (easy) / 3 (medium) / 5 (hard)
"""

from __future__ import annotations

from typing import Any

_DIFFICULTY = {"easy": 1, "medium": 3, "hard": 5}


def raw_to_case_dict(row: dict[str, Any], *, benchmark_id: str) -> dict[str, Any]:
    """Transform one raw HotpotQA record into a YAML-serializable case dict.

    Kept as plain dicts (not ``Case`` instances) so the fetch script can
    dump via ``yaml.safe_dump`` without going through pydantic.
    """
    support_titles = []
    for fact in row.get("supporting_facts", []):
        title = fact[0] if fact else None
        if title and title not in support_titles:
            support_titles.append(title)

    # Collapse the distractor context into a flat text block so that
    # faithfulness judges have something to ground against even before
    # a production retriever is wired in.
    paragraphs: list[dict[str, Any]] = []
    title_to_text: dict[str, str] = {}
    for title, sentences in row.get("context", []):
        text = " ".join(sentences)
        paragraphs.append({"id": title, "content": text})
        title_to_text[title] = text

    # Gold supporting sentences keyed by title for faithfulness evaluation.
    supporting_sentences: list[dict[str, Any]] = []
    for fact in row.get("supporting_facts", []):
        if not fact or len(fact) < 2:
            continue
        title, idx = fact[0], fact[1]
        # locate the sentence in its context entry
        sent = ""
        for ctx_title, sents in row.get("context", []):
            if ctx_title == title and idx < len(sents):
                sent = sents[idx]
                break
        if sent:
            supporting_sentences.append({"title": title, "sentence": sent})

    level = row.get("level", "medium")
    return {
        "id": f"hotpot-{row['_id']}",
        "benchmark_id": benchmark_id,
        "kind": "rag",
        "difficulty": _DIFFICULTY.get(level, 3),
        "source": "public:hotpotqa",
        "input": {
            "query": row["question"],
            "collection": "hotpotqa-distractor",
            "top_k": len(paragraphs),
        },
        "expected": {
            "answer": row.get("answer", ""),
            "aliases": [],
            "source_ids": support_titles,
            "supporting_sentences": supporting_sentences,
            "sources": paragraphs,
            "full_context": "\n\n".join(
                f"[{p['id']}] {p['content']}" for p in paragraphs
            ),
        },
        "rubric": {
            "primary_metric": "rag/f1",
            "hotpot_type": row.get("type", "bridge"),
            "level": level,
        },
        "capability_tags": [
            {"path": "rag/multi_hop"},
            {"path": f"rag/level/{level}"},
            {"path": f"rag/{row.get('type', 'bridge')}"},
        ],
    }
