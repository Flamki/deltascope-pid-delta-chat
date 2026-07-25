from __future__ import annotations

import math
import os
import re
import time
from collections import Counter

from src.canonical import CanonicalDocument
from .providers import ProviderError, generate_with_configured_llm

LOCAL_ANSWER_PROVIDER = "local-grounded-synthesis-v3"

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

DOCUMENT_SUMMARY_TERMS = ("summarize", "summary", "overview", "describe", "explain")
DOCUMENT_SUMMARY_KEYWORDS = {
    "alarm",
    "compressor",
    "control",
    "cooling",
    "design",
    "discharge",
    "flow",
    "gas",
    "instrument",
    "leakage",
    "lubrication",
    "pressure",
    "seal",
    "service",
    "setpoint",
    "suction",
    "system",
    "trip",
    "valve",
    "vent",
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


def greeting_response(question: str) -> str | None:
    normalized = re.sub(r"\s+", " ", question.lower()).strip(" !?.")
    if normalized in {"hi", "hey", "hello", "hello there", "hey there", "yo"}:
        return (
            "Hey — I’m ready. Ask me to summarize File A or File B, compare a tag or value, "
            "explain the most important changes, or select an area on the drawing."
        )
    if normalized in {"thanks", "thank you", "thank you so much"}:
        return "You’re welcome. I’m here if you want to inspect a change or trace something across both files."
    return None


def document_summary_source(question: str) -> str | None:
    lowered = re.sub(r"[_-]+", " ", question.lower())
    if not any(term in lowered for term in DOCUMENT_SUMMARY_TERMS):
        return None
    file_a = re.search(r"\b(?:file|pid)\s*a\b", lowered)
    file_b = re.search(r"\b(?:file|pid)\s*b\b", lowered)
    if file_a and not file_b:
        return "PID-A"
    if file_b and not file_a:
        return "PID-B"
    return None


def document_summary_items(
    documents: list[CanonicalDocument],
    source: str,
    top_k: int,
) -> list[dict]:
    document = next((item for item in documents if item.pid == source), None)
    if document is None:
        return []
    seen: set[str] = set()
    candidates: list[tuple[float, dict]] = []
    for block in document.blocks:
        text = re.sub(r"\s+", " ", block.text).strip()
        normalized = text.lower()
        if len(text) < 5 or normalized in seen:
            continue
        seen.add(normalized)
        words = set(terms(text))
        keyword_hits = len(words & DOCUMENT_SUMMARY_KEYWORDS)
        tag_hits = len(re.findall(r"\b[A-Z]{1,5}[- ]?\d{2,5}[A-Z]?\b", text.upper()))
        numeric_detail = 1 if re.search(r"\d", text) else 0
        useful_length = min(len(text), 240) / 240
        score = keyword_hits * 4 + tag_hits * 2 + numeric_detail + useful_length
        candidates.append(
            (
                score,
                {
                    "kind": "document",
                    "source": document.pid,
                    "id": block.id,
                    "page": block.page,
                    "region": vars(block.region),
                    "text": block.text,
                },
            )
        )
    candidates.sort(key=lambda value: (-value[0], value[1]["page"], value[1]["id"]))
    return [item for _, item in candidates[:top_k]]


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
        "change_type": finding.get("change_type"),
        "severity": finding.get("severity"),
        "item_type": finding.get("item_type"),
        "before_block_id": (finding.get("before") or {}).get("block_id"),
        "after_block_id": (finding.get("after") or {}).get("block_id"),
        "before_text": (finding.get("before") or {}).get("text"),
        "after_text": (finding.get("after") or {}).get("text"),
    }


def is_contextual_follow_up(question: str, history: list[dict] | None) -> bool:
    if not history:
        return False
    lowered = question.lower().strip()
    follow_up_phrases = (
        "what about",
        "how about",
        "and the",
        "what is the revised",
        "what is the old",
        "where exactly",
        "why did",
        "does that",
        "is that",
        "show me that",
    )
    pronouns = {"it", "that", "this", "those", "these", "there", "they", "them"}
    return lowered.startswith(follow_up_phrases) or bool(set(terms(lowered)) & pronouns) or len(terms(lowered)) <= 3


def contextualize_question(question: str, history: list[dict] | None) -> str:
    if not is_contextual_follow_up(question, history):
        return question
    prior_user_messages = [
        str(item.get("content", "")).strip()
        for item in history or []
        if item.get("role") == "user" and str(item.get("content", "")).strip()
    ]
    if not prior_user_messages:
        return question
    return f"{prior_user_messages[-1]} {question}"


def naturalize_delta(item: dict) -> str:
    description = re.sub(r"\s+", " ", item["text"]).strip()
    added = re.fullmatch(r"(.+?) added: '(.+)'\.", description)
    if added:
        return f"File B adds {added.group(2)}."
    removed = re.fullmatch(r"(.+?) removed: '(.+)'\.", description)
    if removed:
        return f"File B removes {removed.group(2)}."
    changed = re.fullmatch(r"(.+?) changed from '(.+)' to '(.+)'\.", description)
    if changed:
        subject = changed.group(1).strip().capitalize()
        return f"{subject} changed from {changed.group(2)} to {changed.group(3)}."
    return description


def source_label(source: str) -> str:
    return {"PID-A": "File A", "PID-B": "File B", "DELTA": "the delta report"}.get(source, source)


def evidence_text(item: dict, limit: int = 360) -> str:
    return re.sub(r"\s+", " ", item["text"]).strip()[:limit]


def render_grounded_answer(
    question: str,
    evidence: list[dict],
    report: dict,
    mode: str,
    selection: dict | None,
) -> str:
    delta_evidence = [item for item in evidence if item["kind"] == "delta"]
    document_evidence = [item for item in evidence if item["kind"] == "document"]
    lines: list[str] = []

    if selection:
        label = source_label(str(selection.get("source")))
        page = selection.get("page", 1)
        lines.append(f"Here’s what I can confirm in the selected area on {label}, page {page}:")
        selected_source = [
            item for item in document_evidence if item["source"] == selection.get("source")
        ]
        for item in selected_source:
            lines.append(f"• {evidence_text(item)} [{item['id']}]")
        for item in delta_evidence:
            lines.append(f"• Revision check: {naturalize_delta(item)} [{item['id']}]")
        for item in document_evidence:
            if item in selected_source:
                continue
            lines.append(
                f"• Related evidence in {source_label(item['source'])}: "
                f"{evidence_text(item)} [{item['id']}]"
            )
        lines.append("If you want, select a tighter area and I’ll isolate a specific tag, note, or instrument.")
        return "\n".join(lines)

    if mode == "summary":
        counts = report["counts"]
        lines.append(
            f"The comparison contains {sum(counts.values())} changes: "
            f"{counts['added']} added, {counts['removed']} removed, and "
            f"{counts['modified']} modified. The highest-priority evidence is:"
        )
        for item in evidence:
            severity = str(item.get("severity") or "review").capitalize()
            detail = naturalize_delta(item) if item["kind"] == "delta" else evidence_text(item)
            lines.append(f"• {severity}: {detail} [{item['id']}]")
        return "\n".join(lines)

    if mode == "added":
        added_count = report["counts"]["added"]
        lines.append(
            f"File B has {added_count} addition{'s' if added_count != 1 else ''}. "
            "Here are the relevant additions:"
        )
        for item in evidence:
            detail = naturalize_delta(item) if item["kind"] == "delta" else evidence_text(item)
            lines.append(f"• {detail} [{item['id']}]")
        return "\n".join(lines)

    if mode == "removed":
        removed_count = report["counts"]["removed"]
        lines.append(
            f"File B has {removed_count} removal{'s' if removed_count != 1 else ''}. "
            "Here are the relevant removals:"
        )
        for item in evidence:
            detail = naturalize_delta(item) if item["kind"] == "delta" else evidence_text(item)
            lines.append(f"• {detail} [{item['id']}]")
        return "\n".join(lines)

    if mode == "document_summary":
        label = source_label(evidence[0]["source"])
        lines.append(f"{label} summary, grounded in the drawing:")
        for item in evidence:
            lines.append(f"• {evidence_text(item)} [{item['id']}]")
        return "\n".join(lines)

    if delta_evidence:
        first = delta_evidence[0]
        lowered_question = question.lower()
        asks_revised = any(
            phrase in lowered_question
            for phrase in ("revised", "new value", "current value", "what is it now", "changed to")
        )
        asks_original = any(
            phrase in lowered_question
            for phrase in ("original", "old value", "previous value", "changed from")
        )
        if asks_revised and first.get("after_text"):
            lines.append(f"The revised document states: {first['after_text']}. [{first['id']}]")
            for item in document_evidence:
                if item["source"] == "PID-B":
                    lines.append(
                        f"• File B, page {item.get('page')}: {evidence_text(item)} [{item['id']}]"
                    )
            return "\n".join(lines)
        if asks_original and first.get("before_text"):
            lines.append(f"The original document states: {first['before_text']}. [{first['id']}]")
            for item in document_evidence:
                if item["source"] == "PID-A":
                    lines.append(
                        f"• File A, page {item.get('page')}: {evidence_text(item)} [{item['id']}]"
                    )
            return "\n".join(lines)
        direct = naturalize_delta(first)
        yes_no = question.lower().strip().startswith(("is ", "are ", "was ", "were ", "did ", "does "))
        prefix = "Yes — " if yes_no and first.get("change_type") == "added" else ""
        lines.append(f"{prefix}{direct} [{first['id']}]")
        for item in delta_evidence[1:]:
            lines.append(f"• Also relevant: {naturalize_delta(item)} [{item['id']}]")
        for item in document_evidence:
            lines.append(
                f"• {source_label(item['source'])}, page {item.get('page')}: "
                f"{evidence_text(item)} [{item['id']}]"
            )
        return "\n".join(lines)

    first = document_evidence[0]
    lines.append(
        f"{source_label(first['source'])}, page {first.get('page')}, states: "
        f"{evidence_text(first)} [{first['id']}]"
    )
    for item in document_evidence[1:]:
        lines.append(
            f"• {source_label(item['source'])}, page {item.get('page')}: "
            f"{evidence_text(item)} [{item['id']}]"
        )
    return "\n".join(lines)


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


def keep_relevant_scores(scored: list[tuple[float, dict]]) -> tuple[list[tuple[float, dict]], float]:
    if not scored:
        return [], 0.0
    relative_floor = min(1.0, max(0.0, float(os.getenv("RETRIEVAL_RELATIVE_SCORE_FLOOR", "0.32"))))
    cutoff = scored[0][0] * relative_floor
    return [item for item in scored if item[0] >= cutoff], round(cutoff, 6)


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
    history: list[dict] | None = None,
) -> dict:
    retrieval_started = time.perf_counter()
    greeting = greeting_response(question)
    if greeting:
        retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
        return {
            "answer": greeting,
            "citations": [],
            "retrieval_hits": 0,
            "grounded": True,
            "provider": LOCAL_ANSWER_PROVIDER,
            "prompt": question,
            "input_tokens": len(terms(question)),
            "output_tokens": len(terms(greeting)),
            "estimated_cost_usd": 0,
            "retrieval": {
                "method": "conversation-intent",
                "query": question,
                "contextualized": False,
                "corpus_size": 0,
                "top_scores": [],
                "score_by_id": {},
            },
            "stage_timings_ms": {
                "retrieval": retrieval_ms,
                "answer_draft": 0.0,
                "llm": 0.0,
                "answer": 0.0,
            },
        }

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
    summary_source = document_summary_source(question)
    retrieval_query = question if summary_source else contextualize_question(question, history)
    if selection and region_items:
        retrieval_query = f"{retrieval_query} {' '.join(item['text'] for item in region_items[:4])}"
    scored, retrieval = bm25_rank(retrieval_query, corpus)
    relevant_scored, score_cutoff = keep_relevant_scores(scored)
    retrieval["query"] = retrieval_query
    retrieval["contextualized"] = retrieval_query != question
    retrieval["relative_score_cutoff"] = score_cutoff

    lower = question.lower()
    normalized_question = lower.strip(" ?.")
    summary_questions = {
        "what changed",
        "summarize what changed",
        "summarize changes",
        "what are the most critical changes",
        "what are the biggest changes",
        "differences",
        "delta",
        "give me a summary",
        "give me an overview",
    }
    added_questions = {
        "what was added",
        "what is added",
        "show additions",
        "list additions",
        "added items",
    }
    removed_questions = {
        "what was removed",
        "what is removed",
        "show removals",
        "list removals",
        "removed items",
    }
    mode = "specific"
    if selection:
        if not region_items:
            selected = []
        else:
            selected = region_items[: min(3, top_k)]
            region_ids = {item["id"] for item in region_items}
            related_delta = [
                delta_item(finding)
                for finding in report["findings"]
                if {
                    (finding.get("before") or {}).get("block_id"),
                    (finding.get("after") or {}).get("block_id"),
                }
                & region_ids
            ]
            selected_ids = {item["id"] for item in selected}
            selected.extend(item for item in related_delta if item["id"] not in selected_ids)
            selected_ids.update(item["id"] for item in selected)
            selected.extend(item for _, item in relevant_scored if item["id"] not in selected_ids)
            selected = selected[:top_k]
            retrieval["method"] = "region+okapi-bm25"
            retrieval["selection_hits"] = len(region_items)
            retrieval["selection"] = selection
    elif summary_source:
        selected = document_summary_items(documents, summary_source, top_k)
        mode = "document_summary"
        retrieval["method"] = "document-summary-router"
        retrieval["summary_source"] = summary_source
        retrieval["score_by_id"] = {
            item["id"]: retrieval["score_by_id"].get(item["id"], 0)
            for item in selected
        }
    elif (
        "only in file b" in lower
        or "only in pid-b" in lower
        or normalized_question in added_questions
    ):
        added = [finding for finding in report["findings"] if finding["change_type"] == "added"]
        selected = [delta_item(finding) for finding in added[:top_k]]
        mode = "added"
    elif (
        "only in file a" in lower
        or "only in pid-a" in lower
        or normalized_question in removed_questions
    ):
        removed = [finding for finding in report["findings"] if finding["change_type"] == "removed"]
        selected = [delta_item(finding) for finding in removed[:top_k]]
        mode = "removed"
    elif (
        normalized_question in summary_questions
        or ("critical" in lower and ("change" in lower or "difference" in lower))
    ):
        selected = [delta_item(finding) for finding in report["findings"][:top_k]]
        mode = "summary"
    else:
        selected = [item for _, item in relevant_scored[:top_k]]

    retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
    if not selected:
        answer_started = time.perf_counter()
        answer = (
            "I don’t have enough evidence in these files to answer that confidently. "
            "Try asking about a specific tag, note, pressure, alarm/trip setpoint, page, "
            "or listed change—or select the relevant drawing area."
        )
        return {
            "answer": answer,
            "citations": [],
            "retrieval_hits": 0,
            "grounded": True,
            "provider": LOCAL_ANSWER_PROVIDER,
            "prompt": retrieval_query,
            "input_tokens": len(terms(retrieval_query)),
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
    response = render_grounded_answer(question, selected, report, mode, selection)
    citations = []
    for item in selected:
        excerpt = evidence_text(item, 320)
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
    draft_ms = round((time.perf_counter() - draft_started) * 1000, 2)

    provider_error = None
    llm_started = time.perf_counter()
    try:
        generated = generate_with_configured_llm(
            question,
            selected,
            response,
            session_id=session_id,
            history=history,
        )
    except ProviderError as exc:
        generated = None
        provider_error = str(exc)
    llm_ms = round((time.perf_counter() - llm_started) * 1000, 2)

    answer_started = time.perf_counter()
    if generated:
        response = generated["answer"]
        selected_citation_ids = set(generated["cited_ids"])
        citations = [citation for citation in citations if citation["id"] in selected_citation_ids]
    else:
        selected_citation_ids = set(re.findall(r"\[([A-Za-z0-9-]+)\]", response))
        citations = [citation for citation in citations if citation["id"] in selected_citation_ids]
    answer_ms = round((time.perf_counter() - answer_started) * 1000, 2)
    return {
        "answer": response,
        "citations": citations,
        "retrieval_hits": len(selected),
        "grounded": True,
        "provider": generated["provider"] if generated else LOCAL_ANSWER_PROVIDER,
        "prompt": generated["prompt"] if generated else retrieval_query,
        "input_tokens": generated["input_tokens"] if generated else len(terms(retrieval_query)) + sum(len(terms(item["text"])) for item in selected),
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
