from __future__ import annotations

import re
from pathlib import Path

import fitz

from src.canonical import CanonicalBlock, CanonicalDocument, CanonicalPage, Region
from .base import FormatAdapter


def classify_text(text: str) -> str:
    upper = text.upper()
    if re.search(r"\b(?:NOTE|NOTES)\s*\d*", upper):
        return "note"
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:MM|CM|M|BAR|BARG|PSI|KW|KG/H|°C|C)\b", upper):
        return "dimension"
    if re.search(r"\b\d{2}-(?:PIT|PDIT|TIT|PSV|FV|KA|KZ|CX|FE)-?\d+\b", upper):
        return "instrument"
    if re.search(r"\b(?:FROM|TO|SUPPLY|RETURN|VENT|DRAIN)\b", upper):
        return "connection"
    return "text"


class NativePdfAdapter(FormatAdapter):
    name = "native_pdf"

    def supports(self, path: Path) -> bool:
        if path.suffix.lower() != ".pdf":
            return False
        with fitz.open(path) as document:
            sample = "".join(page.get_text() for page in document[: min(3, len(document))])
        return len(sample.strip()) >= 40

    def ingest(self, pid: str, path: Path) -> CanonicalDocument:
        pages: list[CanonicalPage] = []
        with fitz.open(path) as document:
            for page_index, page in enumerate(document):
                blocks: list[CanonicalBlock] = []
                for block_index, item in enumerate(page.get_text("blocks")):
                    x0, y0, x1, y1, text = item[:5]
                    cleaned = re.sub(r"\s+", " ", text).strip()
                    if len(cleaned) < 2:
                        continue
                    blocks.append(
                        CanonicalBlock(
                            id=f"{pid}-P{page_index + 1}-B{block_index + 1}",
                            page=page_index + 1,
                            text=cleaned,
                            region=Region(round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)),
                            kind=classify_text(cleaned),
                            confidence=1.0,
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
            metadata = {
                "page_count": len(document),
                "producer": document.metadata.get("producer", ""),
                "title": document.metadata.get("title", ""),
                "text_layer": True,
            }
        return CanonicalDocument(pid, path.name, "native_pdf", self.name, pages, metadata)

