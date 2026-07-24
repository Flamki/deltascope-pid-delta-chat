from __future__ import annotations

import json
from pathlib import Path

from src.chat import answer_question
from src.delta import compare_documents
from src.ingest.router import AdapterRouter

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "eval" / "datasets" / "ground_truth.json"


def finding_text(finding: dict) -> str:
    return " ".join(
        str(value)
        for value in (
            finding.get("description"),
            (finding.get("before") or {}).get("text"),
            (finding.get("after") or {}).get("text"),
        )
        if value
    ).lower()


def run():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    router = AdapterRouter()
    true_positive = false_positive = false_negative = 0
    question_total = question_correct = citation_correct = 0
    pair_results = []

    for pair in dataset["pairs"]:
        base = router.ingest("PID-A", ROOT / pair["base"])
        revised = router.ingest("PID-B", ROOT / pair["revised"])
        report = compare_documents(base, revised)
        predictions = [finding_text(finding) for finding in report["findings"]]
        matched_predictions: set[int] = set()
        matched_expected = 0
        for expected in pair["expected_changes"]:
            tokens = [token.lower() for token in expected["must_contain"]]
            match = next(
                (
                    index
                    for index, prediction in enumerate(predictions)
                    if index not in matched_predictions and all(token in prediction for token in tokens)
                ),
                None,
            )
            if match is not None:
                matched_expected += 1
                matched_predictions.add(match)
        true_positive += matched_expected
        false_negative += len(pair["expected_changes"]) - matched_expected
        # Ignore low-value extraction fragments; count unmatched high-confidence findings.
        false_positive += sum(
            index not in matched_predictions and report["findings"][index]["confidence"] >= 0.9
            for index in range(len(predictions))
        )

        for qa in pair["questions"]:
            result = answer_question(qa["question"], [base, revised], report)
            answer = result["answer"].lower()
            question_total += 1
            question_correct += all(token.lower() in answer for token in qa["answer_must_contain"])
            citation_correct += bool(result["citations"]) and all(
                citation["source"] in {"PID-A", "PID-B", "DELTA"} and citation["id"]
                for citation in result["citations"]
            )
        pair_results.append(
            {
                "id": pair["id"],
                "formats": [base.format, revised.format],
                "findings": len(report["findings"]),
                "expected_matched": matched_expected,
                "expected_total": len(pair["expected_changes"]),
            }
        )

    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2 * precision * recall / max(0.0001, precision + recall)
    scorecard = {
        "dataset_pairs": len(dataset["pairs"]),
        "delta": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
        },
        "chat": {
            "answer_correctness": round(question_correct / max(1, question_total), 4),
            "citation_accuracy": round(citation_correct / max(1, question_total), 4),
            "questions": question_total,
        },
        "pairs": pair_results,
        "known_failures": [
            "DWG geometry and symbol changes require an external LibreDWG/ODA converter.",
            "Dense P&ID vector-line changes without associated text are not detected.",
            "OCR bounding boxes are approximate and low-quality scans may split labels.",
        ],
    }
    output = ROOT / "artifacts" / "eval-scorecard.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print(json.dumps(scorecard, indent=2))
    return scorecard


if __name__ == "__main__":
    run()

