from __future__ import annotations

import re
from pathlib import Path

import cv2
import fitz
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from src.canonical import CanonicalBlock, CanonicalDocument, CanonicalPage, Region
from .base import FormatAdapter
from .pdf_native import classify_text


def clean_ocr_text(value: str) -> str:
    text = " ".join(str(value).strip().split())
    upper = text.upper()
    # Engineering scans often collapse labels and confuse zero with capital O.
    for token in ("HIGH", "LOW", "TRIP", "ALARM", "PRESSURE", "TEMPERATURE", "DESIGN", "NOTE"):
        upper = upper.replace(token, f" {token} ")
    upper = " ".join(upper.split())
    upper = re.sub(r"(?<=\d)O(?=\s*(?:BAR|BARG|PSI|KPA))", "0", upper)
    upper = re.sub(r"(\d)(BARG|BAR|PSI|KPA|MM|KW)\b", r"\1 \2", upper)
    return " ".join(upper.split())


class ScannedPdfAdapter(FormatAdapter):
    name = "scanned_pdf_ocr"

    def __init__(self):
        self.ocr = None

    def supports(self, path: Path) -> bool:
        if path.suffix.lower() != ".pdf":
            return False
        with fitz.open(path) as document:
            sample = "".join(page.get_text() for page in document[: min(3, len(document))])
        return len(sample.strip()) < 40

    def ingest(self, pid: str, path: Path) -> CanonicalDocument:
        if self.ocr is None:
            self.ocr = RapidOCR()
        pages: list[CanonicalPage] = []
        with fitz.open(path) as document:
            for page_index, page in enumerate(document):
                matrix = fitz.Matrix(2, 2)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                    pixmap.height, pixmap.width, pixmap.n
                )
                if pixmap.n == 4:
                    image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
                result, _ = self.ocr(image)
                blocks: list[CanonicalBlock] = []
                for block_index, row in enumerate(result or []):
                    points, text, confidence = row
                    cleaned = clean_ocr_text(str(text))
                    xs = [point[0] / 2 for point in points]
                    ys = [point[1] / 2 for point in points]
                    blocks.append(
                        CanonicalBlock(
                            id=f"{pid}-P{page_index + 1}-OCR{block_index + 1}",
                            page=page_index + 1,
                            text=cleaned,
                            region=Region(round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)),
                            kind=classify_text(cleaned),
                            confidence=round(float(confidence), 3),
                        )
                    )
                pages.append(
                    CanonicalPage(
                        number=page_index + 1,
                        width=round(page.rect.width, 2),
                        height=round(page.rect.height, 2),
                        blocks=blocks,
                    )
                )
        return CanonicalDocument(
            pid,
            path.name,
            "scanned_pdf",
            self.name,
            pages,
            {"page_count": len(pages), "text_layer": False, "ocr_engine": "RapidOCR"},
            ["OCR coordinates are approximate and should be visually reviewed."],
        )
