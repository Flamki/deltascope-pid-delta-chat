from __future__ import annotations

from pathlib import Path

import fitz

from src.canonical import CanonicalBlock

COLORS = {
    "added": (0.08, 0.62, 0.38),
    "removed": (0.86, 0.22, 0.22),
    "modified": (0.92, 0.55, 0.08),
    "citation": (0.08, 0.42, 0.95),
}


def _safe_rect(page: fitz.Page, region: dict, padding: float = 3.0) -> fitz.Rect:
    rect = fitz.Rect(region["x0"], region["y0"], region["x1"], region["y1"])
    rect = fitz.Rect(rect.x0 - padding, rect.y0 - padding, rect.x1 + padding, rect.y1 + padding)
    return rect & page.rect


def _draw_region(
    page: fitz.Page,
    region: dict,
    color: tuple[float, float, float],
    label: str,
    fill_opacity: float = 0.16,
):
    rect = _safe_rect(page, region)
    if rect.is_empty or rect.is_infinite:
        return
    page.draw_rect(
        rect,
        color=color,
        fill=color,
        width=2.2,
        fill_opacity=fill_opacity,
        overlay=True,
    )
    annotation = page.add_rect_annot(rect)
    annotation.set_colors(stroke=color)
    annotation.set_border(width=1.2)
    annotation.set_opacity(0.7)
    annotation.set_info(title="DeltaScope", content=label)
    annotation.update()


def create_highlight_pdf(source: Path, block: CanonicalBlock, label: str | None = None) -> bytes:
    with fitz.open(source) as document:
        page = document[block.page - 1]
        _draw_region(
            page,
            vars(block.region),
            COLORS["citation"],
            label or f"Citation {block.id}: {block.text[:240]}",
            fill_opacity=0.22,
        )
        return document.tobytes(garbage=4, deflate=True)


def create_markup_pdf(source: Path, pid: str, findings: list[dict]) -> bytes:
    with fitz.open(source) as document:
        for finding in findings:
            reference = finding.get("before") if pid == "PID-A" else finding.get("after")
            if not reference or reference.get("source") != pid:
                continue
            page_number = int(reference.get("page") or 0)
            region = reference.get("region")
            if not region or page_number < 1 or page_number > len(document):
                continue
            _draw_region(
                document[page_number - 1],
                region,
                COLORS.get(finding["change_type"], COLORS["citation"]),
                f"{finding['id']} - {finding['change_type']}: {finding['description'][:300]}",
            )
        return document.tobytes(garbage=4, deflate=True)

