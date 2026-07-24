from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Region:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class CanonicalBlock:
    id: str
    page: int
    text: str
    region: Region
    kind: str = "text"
    confidence: float = 1.0


@dataclass
class CanonicalPage:
    number: int
    width: float
    height: float
    blocks: list[CanonicalBlock] = field(default_factory=list)


@dataclass
class CanonicalDocument:
    pid: str
    filename: str
    format: str
    adapter: str
    pages: list[CanonicalPage]
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def blocks(self) -> list[CanonicalBlock]:
        return [block for page in self.pages for block in page.blocks]

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks)

    def to_dict(self, include_blocks: bool = True) -> dict:
        data = asdict(self)
        if not include_blocks:
            data["pages"] = [
                {
                    "number": page.number,
                    "width": page.width,
                    "height": page.height,
                    "block_count": len(page.blocks),
                }
                for page in self.pages
            ]
        data["block_count"] = len(self.blocks)
        return data

