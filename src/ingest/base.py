from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.canonical import CanonicalDocument


class FormatAdapter(ABC):
    name = "base"

    @abstractmethod
    def supports(self, path: Path) -> bool:
        raise NotImplementedError

    @abstractmethod
    def ingest(self, pid: str, path: Path) -> CanonicalDocument:
        raise NotImplementedError

