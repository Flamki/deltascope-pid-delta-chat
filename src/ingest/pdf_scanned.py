from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import fitz

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

    def _recognize(self, pixmap) -> tuple[list, str]:
        service_url = os.getenv("OCR_SERVICE_URL", "").strip().rstrip("/")
        if service_url:
            token = os.getenv("OCR_SERVICE_TOKEN", "").strip()
            request = Request(
                f"{service_url}/api/ocr",
                data=pixmap.tobytes("png"),
                method="POST",
                headers={
                    "Content-Type": "image/png",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "DeltaScope-OCR-Client/1.0",
                },
            )
            try:
                with urlopen(
                    request,
                    timeout=int(os.getenv("OCR_SERVICE_TIMEOUT_SECONDS", "120")),
                ) as response:
                    payload = json.loads(response.read())
                return payload.get("results", []), "RapidOCR/ONNX remote"
            except HTTPError as exc:
                raise RuntimeError(f"OCR service returned HTTP {exc.code}") from exc
            except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"OCR service request failed: {exc}") from exc

        try:
            import cv2
            import numpy as np
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise RuntimeError(
                "Scanned PDF OCR requires `uv sync --extra ocr` locally or OCR_SERVICE_URL in production."
            ) from exc
        if self.ocr is None:
            self.ocr = RapidOCR()
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )
        if pixmap.n == 4:
            image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
        result, _ = self.ocr(image)
        return result or [], "RapidOCR/ONNX local"

    def supports(self, path: Path) -> bool:
        if path.suffix.lower() != ".pdf":
            return False
        with fitz.open(path) as document:
            sample = "".join(page.get_text() for page in document[: min(3, len(document))])
        return len(sample.strip()) < 40

    def ingest(self, pid: str, path: Path) -> CanonicalDocument:
        pages: list[CanonicalPage] = []
        engine = "RapidOCR/ONNX"
        with fitz.open(path) as document:
            for page_index, page in enumerate(document):
                matrix = fitz.Matrix(2, 2)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                result, engine = self._recognize(pixmap)
                blocks: list[CanonicalBlock] = []
                for block_index, row in enumerate(result):
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
            {"page_count": len(pages), "text_layer": False, "ocr_engine": engine},
            ["OCR coordinates are approximate and should be visually reviewed."],
        )
