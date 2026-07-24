from __future__ import annotations

import math
import re
from collections import Counter

from src.canonical import CanonicalDocument
from .providers import ProviderError, generate_with_configured_llm


def terms(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9][a-z0-9./_-]*", text.lower()) if len(word) > 2]


def delta_item(finding: dict) -> dict:
    reference = finding.get("after") or finding.get("before") or {}
    return {
        "kind": "delta",
        "source": "DELTA",
        "id": finding["id"],
        "page": reference.get("page"),
        "region": reference.get("region"),
        "target_source": reference.get("source"),
        "target_block_id": reference.get("block_id"),
        "text": finding["description"],
    }


def answer_question(
    question: str,
    documents: list[CanonicalDocument],
    report: dict,
    session_id: str | None = None,
) -> dict:
    query = Counter(terms(question))
    corpus: list[dict] = []
    for document in documents:
        for block in document.blocks:
            corpus.append(
                {
                    "kind": "document",
                    "source": document.pid,
                    "id": block.id,
                    "page": block.page,
                    "region": vars(block.region),
                    "text": block.text,
                }
            )
    for finding in report["findings"]:
        corpus.append(delta_item(finding))

    document_frequency = Counter()
    tokenized = []
    for item in corpus:
        item_terms = terms(item["text"])
        tokenized.append(item_terms)
        document_frequency.update(set(item_terms))
    scored = []
    for item, item_terms in zip(corpus, tokenized):
        counts = Counter(item_terms)
        score = 0.0
        for word, query_count in query.items():
            if word in counts:
                score += query_count * (1 + math.log(1 + counts[word])) * math.log(2 + len(corpus) / (1 + document_frequency[word]))
        if score:
            scored.append((score, item))
    scored.sort(key=lambda value: (-value[0], value[1]["id"]))

    lower = question.lower()
    if "only in file b" in lower or "only in pid-b" in lower or "added" in lower:
        added = [finding for finding in report["findings"] if finding["change_type"] == "added"]
        selected = [delta_item(finding) for finding in added[:6]]
        lead = f"I found {len(added)} additions in File B."
    elif any(phrase in lower for phrase in ("what changed", "summarize", "biggest change", "critical change", "differences", "delta")):
        selected = [delta_item(finding) for finding in report["findings"][:6]]
        lead = (
            f"I found {sum(report['counts'].values())} changes: "
            f"{report['counts']['added']} added, {report['counts']['removed']} removed, "
            f"and {report['counts']['modified']} modified."
        )
    else:
        selected = [item for _, item in scored[:5]]
        lead = "The strongest supporting evidence is below."

    if not selected:
        return {
            "answer": "I cannot answer that from the uploaded documents or delta report. Try a more specific question or inspect the source drawings.",
            "citations": [],
            "retrieval_hits": 0,
            "grounded": True,
            "provider": "local-extractive-v2",
            "prompt": question,
            "input_tokens": len(terms(question)),
            "output_tokens": 25,
            "estimated_cost_usd": 0,
        }

    statements = [lead]
    citations = []
    for index, item in enumerate(selected, 1):
        excerpt = re.sub(r"\s+", " ", item["text"]).strip()[:320]
        statements.append(f"{index}. {excerpt} [{item['id']}]")
        citations.append(
            {
                "id": item["id"],
                "source": item["source"],
                "page": item["page"],
                "region": item["region"],
                "excerpt": excerpt,
                "kind": item["kind"],
                "target_source": item.get("target_source") or (item["source"] if item["source"].startswith("PID-") else None),
                "target_block_id": item.get("target_block_id") or (item["id"] if item["source"].startswith("PID-") else None),
            }
        )
    response = "\n".join(statements)
    provider_error = None
    try:
        generated = generate_with_configured_llm(question, selected, response, session_id=session_id)
    except ProviderError as exc:
        generated = None
        provider_error = str(exc)
    if generated:
        response = generated["answer"]
        selected_citation_ids = set(generated["cited_ids"])
        citations = [citation for citation in citations if citation["id"] in selected_citation_ids]
    return {
        "answer": response,
        "citations": citations,
        "retrieval_hits": len(selected),
        "grounded": True,
        "provider": generated["provider"] if generated else "local-extractive-v2",
        "prompt": generated["prompt"] if generated else question,
        "input_tokens": generated["input_tokens"] if generated else len(terms(question)) + sum(len(terms(item["text"])) for item in selected),
        "output_tokens": generated["output_tokens"] if generated else len(terms(response)),
        "estimated_cost_usd": generated["estimated_cost_usd"] if generated else 0,
        "response_id": generated.get("response_id") if generated else None,
        "finish_reason": generated.get("finish_reason") if generated else "deterministic",
        "provider_error": provider_error,
    }
