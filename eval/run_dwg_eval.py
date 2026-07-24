from __future__ import annotations

import json
from pathlib import Path

from src.chat import answer_question
from src.delta import compare_documents
from src.ingest.router import AdapterRouter

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [
    ["pressure", "10", "12"],
    ["dimension", "100", "110"],
    ["control_valve", "60", "70"],
    ["line", "100", "110"],
    ["psv-102"],
    ["circle", "90", "8"],
]


def run():
    router = AdapterRouter()
    base = router.ingest("PID-A", ROOT / "data/eval/dwg-geometry-a.dwg")
    revised = router.ingest("PID-B", ROOT / "data/eval/dwg-geometry-b.dwg")
    if not base.metadata.get("geometry_available") or not revised.metadata.get("geometry_available"):
        raise SystemExit("Full DWG geometry is unavailable. Run `make dwg-setup` first.")
    report = compare_documents(base, revised)
    descriptions = [finding["description"].lower() for finding in report["findings"]]
    matched: set[int] = set()
    for expectation in EXPECTED:
        index = next(
            (
                candidate
                for candidate, description in enumerate(descriptions)
                if candidate not in matched
                and all(token.lower() in description for token in expectation)
            ),
            None,
        )
        if index is not None:
            matched.add(index)
    true_positive = len(matched)
    false_negative = len(EXPECTED) - true_positive
    false_positive = len(descriptions) - true_positive
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2 * precision * recall / max(0.0001, precision + recall)
    chat = answer_question(
        "What geometry, dimension, and design pressure changed?",
        [base, revised],
        report,
    )
    scorecard = {
        "formats": [base.format, revised.format],
        "adapters": [base.adapter, revised.adapter],
        "geometry_available": True,
        "entities": {
            "base": base.metadata["entity_count"],
            "revised": revised.metadata["entity_count"],
            "types": revised.metadata["entity_counts"],
            "layers": revised.metadata["layers"],
        },
        "delta": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "expected": len(EXPECTED),
            "matched": true_positive,
            "findings": len(descriptions),
        },
        "chat": {
            "grounded": chat["grounded"],
            "citations": len(chat["citations"]),
            "retrieval": chat["retrieval"]["method"],
        },
    }
    output = ROOT / "artifacts/dwg-eval-scorecard.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print(json.dumps(scorecard, indent=2))
    return scorecard


if __name__ == "__main__":
    run()
