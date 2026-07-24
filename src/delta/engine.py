from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass

from rapidfuzz import fuzz
from src.canonical import CanonicalBlock, CanonicalDocument


@dataclass
class Finding:
    id: str
    change_type: str
    item_type: str
    description: str
    before: dict | None
    after: dict | None
    confidence: float
    severity: str


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def block_ref(pid: str, block: CanonicalBlock) -> dict:
    return {
        "source": pid,
        "block_id": block.id,
        "page": block.page,
        "region": asdict(block.region),
        "text": block.text,
    }


def similarity(left: str, right: str) -> float:
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    sequence = fuzz.ratio(a, b) / 100
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    return 0.65 * sequence + 0.35 * jaccard


def blocking_keys(text: str) -> set[str]:
    tokens = normalize(text).split()
    keys = {f"t:{token}" for token in tokens}
    keys.update(f"p:{token[:3]}" for token in tokens if len(token) >= 4)
    return keys


def severity(kind: str, text: str) -> str:
    value = text.upper()
    if kind in {"instrument", "dimension"} or re.search(r"\b(?:TRIP|HH|LL|DESIGN PRESS|SET POINT|PSV)\b", value):
        return "critical"
    if kind in {"connection", "note"}:
        return "high"
    return "medium"


def compare_documents(base: CanonicalDocument, revised: CanonicalDocument) -> dict:
    minimum_text_length = int(os.getenv("DELTA_MIN_TEXT_LENGTH", "3"))
    similarity_threshold = float(os.getenv("DELTA_SIMILARITY_THRESHOLD", "0.56"))
    alignment_threshold = float(os.getenv("DELTA_ALIGNMENT_THRESHOLD", "0.35"))
    left = [block for block in base.blocks if len(normalize(block.text)) >= minimum_text_length]
    right = [block for block in revised.blocks if len(normalize(block.text)) >= minimum_text_length]
    right_by_text: dict[str, list[int]] = {}
    for index, block in enumerate(right):
        right_by_text.setdefault(normalize(block.text), []).append(index)

    matched_left: set[int] = set()
    matched_right: set[int] = set()
    exact = 0
    for left_index, block in enumerate(left):
        candidates = right_by_text.get(normalize(block.text), [])
        right_index = next((index for index in candidates if index not in matched_right), None)
        if right_index is not None:
            matched_left.add(left_index)
            matched_right.add(right_index)
            exact += 1

    right_index: dict[str, set[int]] = {}
    for right_index_value, right_block in enumerate(right):
        if right_index_value in matched_right:
            continue
        for key in blocking_keys(right_block.text):
            right_index.setdefault(key, set()).add(right_index_value)

    candidates: list[tuple[float, int, int]] = []
    for left_index, left_block in enumerate(left):
        if left_index in matched_left:
            continue
        candidate_indices: set[int] = set()
        for key in blocking_keys(left_block.text):
            candidate_indices.update(right_index.get(key, set()))
        for right_index_value in candidate_indices:
            if right_index_value in matched_right:
                continue
            right_block = right[right_index_value]
            score = similarity(left_block.text, right_block.text)
            if score >= similarity_threshold:
                candidates.append((score, left_index, right_index_value))
    candidates.sort(reverse=True)

    findings: list[Finding] = []
    counter = 1
    for score, left_index, right_index in candidates:
        if left_index in matched_left or right_index in matched_right:
            continue
        before, after = left[left_index], right[right_index]
        matched_left.add(left_index)
        matched_right.add(right_index)
        kind = after.kind if after.kind != "text" else before.kind
        findings.append(
            Finding(
                f"D-{counter:04d}",
                "modified",
                kind,
                f"{kind.replace('_', ' ').title()} changed from '{before.text[:180]}' to '{after.text[:180]}'.",
                block_ref(base.pid, before),
                block_ref(revised.pid, after),
                round(min(0.99, 0.58 + score * 0.4), 3),
                severity(kind, before.text + " " + after.text),
            )
        )
        counter += 1

    for left_index, block in enumerate(left):
        if left_index in matched_left:
            continue
        findings.append(
            Finding(
                f"D-{counter:04d}",
                "removed",
                block.kind,
                f"{block.kind.replace('_', ' ').title()} removed: '{block.text[:240]}'.",
                block_ref(base.pid, block),
                None,
                round(0.74 + 0.24 * block.confidence, 3),
                severity(block.kind, block.text),
            )
        )
        counter += 1

    for right_index, block in enumerate(right):
        if right_index in matched_right:
            continue
        findings.append(
            Finding(
                f"D-{counter:04d}",
                "added",
                block.kind,
                f"{block.kind.replace('_', ' ').title()} added: '{block.text[:240]}'.",
                None,
                block_ref(revised.pid, block),
                round(0.74 + 0.24 * block.confidence, 3),
                severity(block.kind, block.text),
            )
        )
        counter += 1

    order = {"critical": 0, "high": 1, "medium": 2}
    findings.sort(key=lambda finding: (order[finding.severity], finding.change_type, finding.id))
    for index, finding in enumerate(findings, 1):
        finding.id = f"D-{index:04d}"
    alignment = exact / max(1, max(len(left), len(right)))
    counts = {
        change_type: sum(finding.change_type == change_type for finding in findings)
        for change_type in ("added", "removed", "modified")
    }
    return {
        "alignment": {
            "score": round(alignment, 3),
            "status": "aligned" if alignment >= alignment_threshold else "review_required",
            "message": (
                "Documents share enough exact content for revision-style comparison."
                if alignment >= alignment_threshold
                else "Low exact-content alignment. Treat this as a cross-document comparison and review findings."
            ),
        },
        "configuration": {
            "minimum_text_length": minimum_text_length,
            "similarity_threshold": similarity_threshold,
            "alignment_threshold": alignment_threshold,
        },
        "counts": counts,
        "findings": [asdict(finding) for finding in findings],
        "unchanged_blocks": exact,
    }
