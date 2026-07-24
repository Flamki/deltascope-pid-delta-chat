from __future__ import annotations

from pathlib import Path

from .base import FormatAdapter
from .dwg import DwgAdapter
from .pdf_native import NativePdfAdapter
from .pdf_scanned import ScannedPdfAdapter


class AdapterRouter:
    def __init__(self):
        self.native = NativePdfAdapter()
        self.scanned = ScannedPdfAdapter()
        self.dwg = DwgAdapter()

    def resolve(self, path: Path) -> FormatAdapter:
        suffix = path.suffix.lower()
        if suffix == ".dwg":
            return self.dwg
        if suffix != ".pdf":
            raise ValueError("Unsupported format. Upload a native PDF, scanned PDF, or DWG.")
        return self.native if self.native.supports(path) else self.scanned

    def ingest(self, pid: str, path: Path):
        adapter = self.resolve(path)
        return adapter.ingest(pid, path)

