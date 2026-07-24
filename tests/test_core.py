from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from src.chat import answer_question
from src.chat.providers import _line_has_valid_citation
from src.delta import compare_documents
from src.ingest.router import AdapterRouter
from src.markup import create_highlight_pdf, create_markup_pdf


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

    def test_dwg_upload_uses_same_canonical_model(self):
        path = self.root / "drawing.dwg"
        path.write_bytes(b"AC1027\x00PUMP P-101\x00DESIGN PRESSURE 10 BARG\x00")
        document = AdapterRouter().ingest("PID-A", path)
        self.assertEqual(document.format, "dwg")
        self.assertTrue(document.metadata["signature"].startswith("AC"))
        self.assertTrue(document.warnings)

    def test_hosted_answer_requires_allowed_citation_per_line(self):
        allowed = {"D-0001", "PID-A-P1-B2"}
        self.assertTrue(_line_has_valid_citation("Pressure changed. [D-0001]", allowed))
        self.assertFalse(_line_has_valid_citation("Pressure changed.", allowed))
        self.assertFalse(_line_has_valid_citation("Pressure changed. [D-9999]", allowed))

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


if __name__ == "__main__":
    unittest.main()
