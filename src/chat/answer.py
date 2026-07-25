from __future__ import annotations

import math
import os
import re
import time
from collections import Counter

from src.canonical import CanonicalDocument
from .providers import ProviderError, generate_with_configured_llm

ENGINEERING_SHORT_TERMS = {
    "dp",
    "fi",
    "ft",
    "hh",
    "ka",
    "ll",
    "pi",
    "ps",
    "pt",
    "sp",
    "ti",
    "tt",
}


def terms(text: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[a-z0-9][a-z0-9./_-]*", text.lower())
        if len(word) > 2 or word in ENGINEERING_SHORT_TERMS
    ]


def query_terms(text: str) -> list[str]:
    values = terms(text)
    present = set(values)
    if "pressure" in present:
        values.extend(["sp", "dp", "bar", "barg", "psi"])
    if "setpoint" in present or {"set", "point"}.issubset(present):
        values.extend(["sp", "trip", "alarm"])
    if "tag" in present:
        values.extend(["number", "instrument"])
    if "compressor" in present:
        values.append("ka")
    return values


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


def bm25_rank(query_text: str, corpus: list[dict]) -> tuple[list[tuple[float, dict]], dict]:
    """Rank evidence with a compact, deterministic Okapi BM25 implementation."""

    query = Counter(query_terms(query_text))
    tokenized = [terms(item["text"]) for item in corpus]
    document_frequency = Counter()
    for item_terms in tokenized:
        document_frequency.update(set(item_terms))
    average_length = sum(map(len, tokenized)) / max(1, len(tokenized))
    k1 = float(os.getenv("RETRIEVAL_BM25_K1", "1.5"))
    b = float(os.getenv("RETRIEVAL_BM25_B", "0.75"))
    scored: list[tuple[float, dict]] = []
    score_by_id: dict[str, float] = {}
    for item, item_terms in zip(corpus, tokenized):
        counts = Counter(item_terms)
        length_normalization = k1 * (1 - b + b * len(item_terms) / max(1, average_length))
        score = 0.0
        for word, query_count in query.items():
            frequency = counts[word]
            if not frequency:
                continue
            inverse_document_frequency = math.log(
                1 + (len(corpus) - document_frequency[word] + 0.5) / (document_frequency[word] + 0.5)
            )
            score += (
                query_count
                * inverse_document_frequency
                * frequency
                * (k1 + 1)
                / (frequency + length_normalization)
            )
        if score > 0:
            rounded = round(score, 6)
            scored.append((rounded, item))
            score_by_id[item["id"]] = rounded
    scored.sort(key=lambda value: (-value[0], value[1]["id"]))
    return scored, {
        "method": "okapi-bm25",
        "k1": k1,
        "b": b,
        "corpus_size": len(corpus),
        "top_scores": [
            {"id": item["id"], "source": item["source"], "score": score}
            for score, item in scored[:10]
        ],
        "score_by_id": score_by_id,
    }


def selected_region_items(documents: list[CanonicalDocument], selection: dict | None) -> list[dict]:
    if not selection:
        return []
    source = str(selection.get("source", ""))
    try:
        page_number = int(selection.get("page", 1))
        region = selection["region"]
        normalized = [float(region[key]) for key in ("x0", "y0", "x1", "y1")]
    except (KeyError, TypeError, ValueError):
        return []
    if source not in {"PID-A", "PID-B"} or not all(0 <= value <= 1 for value in normalized):
        return []
    x0, y0, x1, y1 = normalized
    if x1 <= x0 or y1 <= y0:
        return []
    document = next((item for item in documents if item.pid == source), None)
    page = next((item for item in document.pages if item.number == page_number), None) if document else None
    if page is None:
        return []
    selected = []
    for block in page.blocks:
        intersection_width = max(0.0, min(block.region.x1, x1 * page.width) - max(block.region.x0, x0 * page.width))
        intersection_height = max(0.0, min(block.region.y1, y1 * page.height) - max(block.region.y0, y0 * page.height))
        if intersection_width <= 0 or intersection_height <= 0:
            continue
        selected.append(
            {
                "kind": "document",
                "source": document.pid,
                "id": block.id,
                "page": block.page,
                "region": vars(block.region),
                "text": block.text,
                "_selection_overlap": intersection_width * intersection_height,
            }
        )
    selected.sort(key=lambda item: (-item["_selection_overlap"], item["id"]))
    for item in selected:
        item.pop("_selection_overlap", None)
    return selected


def answer_question(
    question: str,
    documents: list[CanonicalDocument],
    report: dict,
    session_id: str | None = None,
    selection: dict | None = None,
) -> dict:
    retrieval_started = time.perf_counter()
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

    top_k = max(1, int(os.getenv("RETRIEVAL_TOP_K", "6")))
    region_items = selected_region_items(documents, selection)
    retrieval_query = question
    if selection and region_items:
        retrieval_query = f"{question} {' '.join(item['text'] for item in region_items[:4])}"
    scored, retrieval = bm25_rank(retrieval_query, corpus)

    lower = question.lower()
    if selection:
        if not region_items:
            selected = []
            lead = "No indexed text or engineering label intersects the selected area."
        else:
            selected = region_items[: min(3, top_k)]
            selected_ids = {item["id"] for item in selected}
            selected.extend(item for _, item in scored if item["id"] not in selected_ids)
            selected = selected[:top_k]
            source_label = "File A" if selection.get("source") == "PID-A" else "File B"
            lead = (
                f"I grounded this answer in the selected area on {source_label}, "
                f"page {selection.get('page', 1)}, plus matching evidence from both documents and the delta."
            )
            retrieval["method"] = "region+okapi-bm25"
            retrieval["selection_hits"] = len(region_items)
            retrieval["selection"] = selection
    elif "only in file b" in lower or "only in pid-b" in lower or "added" in lower:
        added = [finding for finding in report["findings"] if finding["change_type"] == "added"]
        selected = [delta_item(finding) for finding in added[:top_k]]
        lead = f"I found {len(added)} additions in File B."
    elif lower.strip(" ?.") in {
        "what changed",
        "summarize what changed",
        "summarize changes",
        "what are the most critical changes",
        "what are the biggest changes",
        "differences",
        "delta",
    }:
        selected = [delta_item(finding) for finding in report["findings"][:top_k]]
        lead = (
            f"I found {sum(report['counts'].values())} changes: "
            f"{report['counts']['added']} added, {report['counts']['removed']} removed, "
            f"and {report['counts']['modified']} modified."
        )
    else:
        selected = [item for _, item in scored[:top_k]]
        lead = "The strongest supporting evidence is below."

    retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
    if not selected:
        answer_started = time.perf_counter()
        answer = "I cannot answer that from the uploaded documents or delta report. Try a more specific question or inspect the source drawings."
        return {
            "answer": answer,
            "citations": [],
            "retrieval_hits": 0,
            "grounded": True,
            "provider": "local-extractive-v2",
            "prompt": question,
            "input_tokens": len(terms(question)),
            "output_tokens": 25,
            "estimated_cost_usd": 0,
            "retrieval": retrieval,
            "stage_timings_ms": {
                "retrieval": retrieval_ms,
                "answer_draft": round((time.perf_counter() - answer_started) * 1000, 2),
                "llm": 0.0,
                "answer": 0.0,
            },
        }

    draft_started = time.perf_counter()
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
    draft_ms = round((time.perf_counter() - draft_started) * 1000, 2)

    provider_error = None
    llm_started = time.perf_counter()
    try:
        generated = generate_with_configured_llm(question, selected, response, session_id=session_id)
    except ProviderError as exc:
        generated = None
        provider_error = str(exc)
    llm_ms = round((time.perf_counter() - llm_started) * 1000, 2)

    answer_started = time.perf_counter()
    if generated:
        response = generated["answer"]
        selected_citation_ids = set(generated["cited_ids"])
        citations = [citation for citation in citations if citation["id"] in selected_citation_ids]
    answer_ms = round((time.perf_counter() - answer_started) * 1000, 2)
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
        "retrieval": {
            **retrieval,
            "score_by_id": {
                item["id"]: retrieval["score_by_id"].get(item["id"], 0)
                for item in selected
            },
        },
        "stage_timings_ms": {
            "retrieval": retrieval_ms,
            "answer_draft": draft_ms,
            "llm": llm_ms,
            "answer": answer_ms,
        },
    }
