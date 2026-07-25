from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from app import aggregate_metrics
from eval.run_eval import regression_failures
from src.canonical import CanonicalDocument
from src.chat import answer_question
from src.chat.answer import bm25_rank
from src.chat.providers import _line_has_valid_citation
from src.delta import compare_documents
from src.ingest.router import AdapterRouter
from src.ingest.dwg import DwgAdapter
from src.markup import colorize_ink, create_dwg_svg, create_highlight_pdf, create_markup_pdf


def create_pdf(path: Path, lines: list[str]):
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    for index, line in enumerate(lines):
        page.insert_text((60, 80 + index * 35), line, fontsize=14)
    document.save(path)
    document.close()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base_path = self.root / "base.pdf"
        self.revised_path = self.root / "revised.pdf"
        create_pdf(
            self.base_path,
            ["PUMP P-101", "DESIGN PRESSURE 10 BARG", "NOTE 1: CARBON STEEL"],
        )
        create_pdf(
            self.revised_path,
            ["PUMP P-101", "DESIGN PRESSURE 12 BARG", "NOTE 1: STAINLESS STEEL", "PSV-102 ADDED"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_native_ingestion_and_locations(self):
        router = AdapterRouter()
        document = router.ingest("PID-A", self.base_path)
        self.assertEqual(document.format, "native_pdf")
        self.assertGreaterEqual(len(document.blocks), 3)
        self.assertGreater(document.blocks[0].region.x1, document.blocks[0].region.x0)

    def test_canonical_document_round_trip_supports_session_restore(self):
        original = AdapterRouter().ingest("PID-A", self.base_path)
        restored = CanonicalDocument.from_dict(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())

    def test_delta_is_structured(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        report = compare_documents(base, revised)
        self.assertGreater(sum(report["counts"].values()), 0)
        self.assertTrue(all(finding["confidence"] > 0 for finding in report["findings"]))
        self.assertTrue(any((finding["after"] or finding["before"])["region"] for finding in report["findings"]))

    def test_delta_thresholds_are_environment_configurable(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        with patch.dict(os.environ, {"DELTA_SIMILARITY_THRESHOLD": "1.1"}, clear=False):
            report = compare_documents(base, revised)
        self.assertEqual(report["configuration"]["similarity_threshold"], 1.1)
        self.assertEqual(report["counts"]["modified"], 0)

    def test_chat_is_grounded_and_cited(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        report = compare_documents(base, revised)
        result = answer_question("What changed about design pressure?", [base, revised], report)
        self.assertTrue(result["grounded"])
        self.assertGreater(result["retrieval_hits"], 0)
        self.assertTrue(result["citations"])
        self.assertTrue(all(citation["source"] in {"PID-A", "PID-B", "DELTA"} for citation in result["citations"]))
        self.assertEqual(set(result["stage_timings_ms"]), {"retrieval", "answer_draft", "llm", "answer"})
        self.assertTrue(all(value >= 0 for value in result["stage_timings_ms"].values()))
        self.assertNotIn("strongest supporting evidence", result["answer"].lower())
        self.assertIn("changed from", result["answer"].lower())
        self.assertNotIn("stainless steel", result["answer"].lower())
        self.assertEqual(result["provider"], "local-grounded-synthesis-v3")

    def test_chat_resolves_a_short_follow_up_from_recent_context(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        report = compare_documents(base, revised)
        result = answer_question(
            "What is the revised value?",
            [base, revised],
            report,
            history=[
                {"role": "user", "content": "What changed about the design pressure?"},
                {
                    "role": "assistant",
                    "content": "Design pressure changed from 10 BARG to 12 BARG. [D-0002]",
                },
            ],
        )
        self.assertTrue(result["retrieval"]["contextualized"])
        self.assertTrue(result["answer"].startswith("The revised document states:"))
        self.assertIn("12 BARG", result["answer"])
        self.assertTrue(any("PRESSURE" in citation["excerpt"] for citation in result["citations"]))

    def test_chat_refuses_when_the_documents_have_no_support(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        report = compare_documents(base, revised)
        result = answer_question("What should the maintenance team order for lunch?", [base, revised], report)
        self.assertEqual(result["retrieval_hits"], 0)
        self.assertEqual(result["citations"], [])
        self.assertIn("don’t have enough evidence", result["answer"])

    def test_chat_handles_a_greeting_without_a_false_refusal(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        report = compare_documents(base, revised)
        result = answer_question("hey", [base, revised], report)
        self.assertEqual(result["retrieval"]["method"], "conversation-intent")
        self.assertEqual(result["retrieval_hits"], 0)
        self.assertEqual(result["citations"], [])
        self.assertTrue(result["answer"].startswith("Hey"))
        self.assertNotIn("enough evidence", result["answer"].lower())

    def test_chat_summarizes_one_document_with_only_that_files_citations(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        report = compare_documents(base, revised)
        result = answer_question("summarize file a", [base, revised], report)
        self.assertEqual(result["retrieval"]["method"], "document-summary-router")
        self.assertEqual(result["retrieval"]["summary_source"], "PID-A")
        self.assertGreater(result["retrieval_hits"], 0)
        self.assertTrue(result["citations"])
        self.assertTrue(all(citation["source"] == "PID-A" for citation in result["citations"]))
        self.assertIn("file a summary", result["answer"].lower())
        self.assertNotIn("enough evidence", result["answer"].lower())

    def test_selected_region_is_mapped_to_canonical_evidence(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        report = compare_documents(base, revised)
        pressure = next(block for block in base.blocks if "PRESSURE" in block.text)
        page = base.pages[pressure.page - 1]
        padding = 2
        selection = {
            "source": "PID-A",
            "page": pressure.page,
            "region": {
                "x0": max(0, pressure.region.x0 - padding) / page.width,
                "y0": max(0, pressure.region.y0 - padding) / page.height,
                "x1": min(page.width, pressure.region.x1 + padding) / page.width,
                "y1": min(page.height, pressure.region.y1 + padding) / page.height,
            },
        }
        result = answer_question(
            "What is shown in this selected area?",
            [base, revised],
            report,
            selection=selection,
        )
        self.assertEqual(result["retrieval"]["method"], "region+okapi-bm25")
        self.assertGreaterEqual(result["retrieval"]["selection_hits"], 1)
        self.assertEqual(result["citations"][0]["id"], pressure.id)
        self.assertIn("DESIGN PRESSURE 10 BARG", result["citations"][0]["excerpt"])
        self.assertTrue(any(citation["source"] == "DELTA" for citation in result["citations"]))
        self.assertIn("Revision check", result["answer"])

    def test_moved_unchanged_blocks_are_not_reported_as_changes(self):
        before = self.root / "moved-before.pdf"
        after = self.root / "moved-after.pdf"
        create_pdf(before, ["PUMP P-301", "DESIGN PRESSURE 40 BARG", "NOTE 8 ROUTE TO FLARE"])
        create_pdf(after, ["NOTE 8 ROUTE TO FLARE", "PUMP P-301", "DESIGN PRESSURE 45 BARG"])
        router = AdapterRouter()
        report = compare_documents(router.ingest("PID-A", before), router.ingest("PID-B", after))
        self.assertEqual(report["counts"], {"added": 0, "removed": 0, "modified": 1})
        self.assertIn("40 BARG", report["findings"][0]["description"])
        self.assertIn("45 BARG", report["findings"][0]["description"])

    def test_eval_gate_detects_a_regression(self):
        degraded = {
            "delta": {"f1": 0.75},
            "chat": {"answer_correctness": 1.0, "citation_accuracy": 1.0, "groundedness": 1.0},
            "retrieval": {"recall_at_k": 1.0},
        }
        failures = regression_failures(
            degraded,
            {
                "delta_f1": 0.9,
                "chat_correctness": 0.9,
                "citation_accuracy": 0.9,
                "groundedness": 0.9,
                "retrieval_recall_at_k": 0.9,
            },
        )
        self.assertEqual(failures, [{"metric": "delta_f1", "actual": 0.75, "minimum": 0.9}])

    def test_specific_what_changed_question_uses_relevant_evidence(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        report = compare_documents(base, revised)
        result = answer_question("What changed about the design pressure?", [base, revised], report)
        supporting = [
            citation
            for citation in result["citations"]
            if all(token in citation["excerpt"].lower() for token in ("pressure", "10", "12"))
        ]
        self.assertTrue(supporting)
        self.assertEqual(result["retrieval"]["method"], "okapi-bm25")

    def test_bm25_ranks_rare_engineering_tag_first(self):
        corpus = [
            {"id": "A", "source": "PID-A", "text": "general compressor pressure note"},
            {"id": "B", "source": "PID-B", "text": "PSV-102 set pressure 12 barg"},
        ]
        ranked, metadata = bm25_rank("What is PSV-102 pressure?", corpus)
        self.assertEqual(ranked[0][1]["id"], "B")
        self.assertEqual(metadata["method"], "okapi-bm25")

    def test_dwg_upload_uses_same_canonical_model(self):
        path = self.root / "drawing.dwg"
        path.write_bytes(b"AC1027\x00PUMP P-101\x00DESIGN PRESSURE 10 BARG\x00")
        document = AdapterRouter().ingest("PID-A", path)
        self.assertEqual(document.format, "dwg")
        self.assertTrue(document.metadata["signature"].startswith("AC"))
        self.assertTrue(document.warnings)

    @unittest.skipUnless(
        DwgAdapter.converter_available()
        and Path("data/eval/dwg-geometry-a.dwg").is_file()
        and Path("data/eval/dwg-geometry-b.dwg").is_file(),
        "real DWG geometry test requires LibreDWG and generated fixtures",
    )
    def test_real_dwg_geometry_layers_blocks_and_dimensions(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", Path("data/eval/dwg-geometry-a.dwg"))
        revised = router.ingest("PID-B", Path("data/eval/dwg-geometry-b.dwg"))
        self.assertTrue(base.metadata["geometry_available"])
        self.assertIn("PIPING", base.metadata["layers"])
        self.assertGreaterEqual(base.metadata["entity_counts"]["DIMENSION"], 1)
        report = compare_documents(base, revised)
        descriptions = " ".join(finding["description"].lower() for finding in report["findings"])
        self.assertIn("control_valve", descriptions)
        self.assertIn("measurement=100", descriptions)
        self.assertIn("measurement=110", descriptions)
        self.assertTrue(any(finding["item_type"] == "geometry" for finding in report["findings"]))
        highlighted = next(block for block in revised.blocks if "DESIGN PRESSURE 12" in block.text)
        svg = create_dwg_svg(revised, highlighted.id)
        self.assertIn(b"<svg", svg)
        self.assertIn(b"citation-highlight", svg)
        self.assertIn(highlighted.id.encode(), svg)
        self.assertIn(b'id="cad-entities"', svg)

    def test_hosted_answer_requires_allowed_citation_per_line(self):
        allowed = {"D-0001", "PID-A-P1-B2"}
        self.assertTrue(_line_has_valid_citation("Pressure changed. [D-0001]", allowed))
        self.assertTrue(_line_has_valid_citation("Want me to trace that tag across both revisions?", allowed))
        self.assertFalse(_line_has_valid_citation("Pressure changed.", allowed))
        self.assertFalse(_line_has_valid_citation("Pressure changed. [D-9999]", allowed))

    def test_observability_separates_hosted_llm_usage_from_local_estimates(self):
        traces = [
            {
                "request": "compare",
                "status": "ok",
                "duration_ms": 8000,
                "spans": [],
                "telemetry": {"model": "deterministic-delta-v2", "input_tokens": 0, "output_tokens": 0},
            },
            {
                "request": "chat",
                "status": "ok",
                "duration_ms": 100,
                "spans": [{"name": "retrieval", "attributes": {"hits": 3}}],
                "telemetry": {
                    "model": "local-grounded-synthesis-v3",
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "estimated_cost_usd": 0,
                },
            },
            {
                "request": "chat",
                "status": "ok",
                "duration_ms": 400,
                "spans": [{"name": "retrieval", "attributes": {"hits": 2}}],
                "telemetry": {
                    "model": "fireworks:accounts/fireworks/models/gpt-oss-20b",
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "estimated_cost_usd": 0.0000145,
                },
            },
        ]
        metrics = aggregate_metrics(traces, {"modified": 1})
        self.assertEqual(metrics["requests"], 3)
        self.assertEqual(metrics["chat_requests"], 2)
        self.assertEqual(metrics["retrieval_hits"], 5)
        self.assertEqual(metrics["llm_calls"], 1)
        self.assertEqual(metrics["input_tokens"], 100)
        self.assertEqual(metrics["output_tokens"], 25)
        self.assertEqual(metrics["local_input_token_estimate"], 20)
        self.assertEqual(metrics["local_output_token_estimate"], 10)
        self.assertEqual(metrics["estimated_cost_usd"], 0.0000145)
        self.assertEqual(metrics["p95_latency_ms"], 8000)
        self.assertEqual(metrics["delta_counts"], {"modified": 1})

    def test_citation_highlight_and_markup_are_real_pdf_overlays(self):
        router = AdapterRouter()
        base = router.ingest("PID-A", self.base_path)
        revised = router.ingest("PID-B", self.revised_path)
        report = compare_documents(base, revised)
        highlighted = create_highlight_pdf(self.base_path, base.blocks[1])
        marked_up = create_markup_pdf(self.revised_path, "PID-B", report["findings"])
        with fitz.open(stream=highlighted, filetype="pdf") as document:
            self.assertGreater(len(document[0].get_drawings()), 0)
        with fitz.open(stream=marked_up, filetype="pdf") as document:
            self.assertGreater(len(document[0].get_drawings()), 0)

    def test_revision_overlay_uses_red_and_green_ink_on_white(self):
        source = fitz.Pixmap(
            fitz.csRGB,
            3,
            1,
            bytes((0, 0, 0, 128, 128, 128, 255, 255, 255)),
            False,
        )
        red = colorize_ink(source, "red")
        green = colorize_ink(source, "green")
        self.assertEqual(tuple(red.samples[:3]), (255, 0, 0))
        self.assertEqual(tuple(green.samples[:3]), (0, 165, 0))
        self.assertEqual(tuple(red.samples[-3:]), (255, 255, 255))
        self.assertEqual(tuple(green.samples[-3:]), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
