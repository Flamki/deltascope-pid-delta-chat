from __future__ import annotations

import json
import os
from pathlib import Path

from src.chat import answer_question
from src.delta import compare_documents
from src.ingest.router import AdapterRouter

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "eval" / "datasets" / "ground_truth.json"


def regression_thresholds() -> dict[str, float]:
    return {
        "delta_f1": float(os.getenv("EVAL_MIN_DELTA_F1", "0.90")),
        "chat_correctness": float(os.getenv("EVAL_MIN_CHAT_CORRECTNESS", "0.90")),
        "citation_accuracy": float(os.getenv("EVAL_MIN_CITATION_ACCURACY", "0.90")),
        "groundedness": float(os.getenv("EVAL_MIN_GROUNDEDNESS", "0.90")),
        "retrieval_recall_at_k": float(os.getenv("EVAL_MIN_RETRIEVAL_RECALL_AT_K", "0.90")),
    }


def regression_failures(scorecard: dict, thresholds: dict[str, float]) -> list[dict]:
    actual = {
        "delta_f1": scorecard["delta"]["f1"],
        "chat_correctness": scorecard["chat"]["answer_correctness"],
        "citation_accuracy": scorecard["chat"]["citation_accuracy"],
        "groundedness": scorecard["chat"]["groundedness"],
        "retrieval_recall_at_k": scorecard["retrieval"]["recall_at_k"],
    }
    return [
        {"metric": metric, "actual": actual[metric], "minimum": minimum}
        for metric, minimum in thresholds.items()
        if actual[metric] < minimum
    ]


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
    question_total = question_correct = citation_correct = grounded_correct = 0
    retrieval_hit = 0
    reciprocal_rank = 0.0
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
            evidence_tokens = [token.lower() for token in qa["evidence_must_contain"]]
            support_positions = [
                index
                for index, citation in enumerate(result["citations"])
                if all(token in citation["excerpt"].lower() for token in evidence_tokens)
            ]
            supported = bool(support_positions)
            question_total += 1
            question_correct += all(token.lower() in answer for token in qa["answer_must_contain"])
            syntactically_valid = bool(result["citations"]) and all(
                citation["source"] in {"PID-A", "PID-B", "DELTA"} and citation["id"]
                for citation in result["citations"]
            )
            citation_correct += syntactically_valid and supported
            grounded_correct += bool(result["grounded"]) and supported
            retrieval_hit += supported
            reciprocal_rank += 1 / (support_positions[0] + 1) if support_positions else 0
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
            "groundedness": round(grounded_correct / max(1, question_total), 4),
            "questions": question_total,
        },
        "retrieval": {
            "method": "okapi-bm25",
            "recall_at_k": round(retrieval_hit / max(1, question_total), 4),
            "mean_reciprocal_rank": round(reciprocal_rank / max(1, question_total), 4),
        },
        "pairs": pair_results,
        "known_failures": [
            "Unsupported 3D solids and proprietary DWG proxy entities may be reduced to type and bounds.",
            "Semantic P&ID connectivity and topology are not reconstructed from dense vector-line drawings.",
            "OCR bounding boxes are approximate and low-quality scans may split labels.",
        ],
    }
    thresholds = regression_thresholds()
    failures = regression_failures(scorecard, thresholds)
    scorecard["regression_gate"] = {
        "passed": not failures,
        "thresholds": thresholds,
        "failures": failures,
    }
    output = ROOT / "artifacts" / "eval-scorecard.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print(json.dumps(scorecard, indent=2))
    return scorecard


if __name__ == "__main__":
    result = run()
    if not result["regression_gate"]["passed"]:
        raise SystemExit(1)
