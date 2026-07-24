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

    @classmethod
    def from_dict(cls, data: dict) -> "CanonicalDocument":
        pages = []
        for page_data in data.get("pages", []):
            blocks = [
                CanonicalBlock(
                    id=block["id"],
                    page=int(block["page"]),
                    text=block["text"],
                    region=Region(**block["region"]),
                    kind=block.get("kind", "text"),
                    confidence=float(block.get("confidence", 1.0)),
                )
                for block in page_data.get("blocks", [])
            ]
            pages.append(
                CanonicalPage(
                    number=int(page_data["number"]),
                    width=float(page_data["width"]),
                    height=float(page_data["height"]),
                    blocks=blocks,
                )
            )
        return cls(
            pid=data["pid"],
            filename=data["filename"],
            format=data["format"],
            adapter=data["adapter"],
            pages=pages,
            metadata=data.get("metadata", {}),
            warnings=data.get("warnings", []),
        )
