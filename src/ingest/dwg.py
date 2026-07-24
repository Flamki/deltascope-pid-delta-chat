from __future__ import annotations

import hashlib
import re
from pathlib import Path

from src.canonical import CanonicalBlock, CanonicalDocument, CanonicalPage, Region
from .base import FormatAdapter
from .pdf_native import classify_text


class DwgAdapter(FormatAdapter):
    """Safe DWG adapter with metadata and conservative recoverable-string extraction.

    Full entity geometry requires an external LibreDWG/ODA converter. The adapter is
    intentionally honest about that capability while still accepting and indexing a
    DWG upload end-to-end.
    """

    name = "dwg_binary"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".dwg"

    def ingest(self, pid: str, path: Path) -> CanonicalDocument:
        raw = path.read_bytes()
        signature = raw[:6].decode("ascii", errors="replace")
        ascii_strings = re.findall(rb"[\x20-\x7e]{6,}", raw)
        recovered: list[str] = []
        seen: set[str] = set()
        for value in ascii_strings:
            text = value.decode("ascii", errors="ignore").strip()
            if text and text not in seen:
                seen.add(text)
                recovered.append(text)
            if len(recovered) >= 400:
                break
        blocks = [
            CanonicalBlock(
                id=f"{pid}-DWG-S{index + 1}",
                page=1,
                text=text,
                region=Region(0, float(index * 12), 1000, float(index * 12 + 10)),
                kind=classify_text(text),
                confidence=0.45,
            )
            for index, text in enumerate(recovered)
        ]
        warning = (
            "DWG accepted and indexed through recoverable strings. Geometry, layers, "
            "blocks, and precise coordinates require a configured LibreDWG/ODA converter."
        )
        return CanonicalDocument(
            pid,
            path.name,
            "dwg",
            self.name,
            [CanonicalPage(1, 1000, max(1000, len(blocks) * 12), blocks)],
            {
                "signature": signature,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "geometry_available": False,
            },
            [warning],
        )

