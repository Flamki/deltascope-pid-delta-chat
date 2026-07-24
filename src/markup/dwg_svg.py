from __future__ import annotations

import html
import re

from src.canonical import CanonicalBlock, CanonicalDocument


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _entity_type(block: CanonicalBlock) -> str:
    return block.text.split(" ", 1)[0].upper()


def _short_label(block: CanonicalBlock) -> str:
    text = re.sub(r"\s+", " ", block.text).strip()
    return text if len(text) <= 76 else text[:73] + "..."


def _drawing_entity(block: CanonicalBlock, page_height: float, highlighted: bool) -> str:
    region = block.region
    x0, x1 = region.x0, region.x1
    y0, y1 = page_height - region.y1, page_height - region.y0
    width = max(0.35, x1 - x0)
    height = max(0.35, y1 - y0)
    center_x, center_y = x0 + width / 2, y0 + height / 2
    entity_type = _entity_type(block)
    css_class = "cad-entity is-highlighted" if highlighted else "cad-entity"
    identity = f'id="{_escape(block.id)}" data-kind="{_escape(block.kind)}"'

    if entity_type == "LINE":
        shape = (
            f'<line x1="{x0:.4f}" y1="{y1:.4f}" x2="{x1:.4f}" '
            f'y2="{y0:.4f}" fill="none" stroke="#1d4e75" stroke-width=".55" '
            f'class="{css_class} piping" {identity}/>'
        )
    elif entity_type in {"CIRCLE", "ARC", "ELLIPSE"}:
        shape = (
            f'<ellipse cx="{center_x:.4f}" cy="{center_y:.4f}" rx="{width / 2:.4f}" '
            f'ry="{height / 2:.4f}" fill="#f9fbfd" stroke="#111f2d" stroke-width=".48" '
            f'class="{css_class} equipment" {identity}/>'
        )
    elif entity_type == "BLOCK":
        shape = (
            f'<rect x="{x0:.4f}" y="{y0:.4f}" width="{width:.4f}" height="{height:.4f}" '
            f'rx="{min(width, height) * .12:.4f}" fill="#fff8e8" stroke="#8a4b12" '
            f'stroke-width=".48" class="{css_class} block" {identity}/>'
        )
    elif entity_type in {"DIMENSION", "LEADER"}:
        shape = (
            f'<path d="M {x0:.4f} {center_y:.4f} H {x1:.4f} '
            f'M {x0:.4f} {center_y - height * .18:.4f} V {center_y + height * .18:.4f} '
            f'M {x1:.4f} {center_y - height * .18:.4f} V {center_y + height * .18:.4f}" '
            f'fill="none" stroke="#596b7e" stroke-width=".26" '
            f'class="{css_class} dimension" {identity}/>'
        )
    elif entity_type in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"} or block.kind != "geometry":
        font_size = max(1.4, min(4.2, height * .72, width / max(6, len(block.text)) * 1.9))
        shape = (
            f'<text x="{x0:.4f}" y="{y1 - max(.15, height * .14):.4f}" '
            f'font-size="{font_size:.4f}" fill="#172737" font-family="Segoe UI, sans-serif" '
            f'font-weight="600" class="{css_class} annotation" {identity}>'
            f'{_escape(_short_label(block))}</text>'
        )
    else:
        shape = (
            f'<rect x="{x0:.4f}" y="{y0:.4f}" width="{width:.4f}" height="{height:.4f}" '
            f'fill="none" stroke="#687c91" stroke-width=".34" stroke-dasharray="1.2 .6" '
            f'class="{css_class} geometry" {identity}/>'
        )

    if not highlighted:
        return shape
    padding = max(1.4, min(page_height, max(width, height)) * .018)
    label_width = max(20, min(58, len(block.id) * 1.3))
    preferred_label_y = y0 - padding * 1.8
    label_y = preferred_label_y if preferred_label_y >= 0 else y0 + height + padding * 1.2
    highlight = (
        f'<rect x="{x0 - padding:.4f}" y="{y0 - padding:.4f}" '
        f'width="{width + padding * 2:.4f}" height="{height + padding * 2:.4f}" '
        f'rx="{padding * .7:.4f}" fill="#1683ff" fill-opacity=".13" stroke="#0875ff" '
        f'stroke-width=".65" stroke-dasharray="1.4 .7" class="citation-highlight"/>'
        f'<g class="citation-label" transform="translate({x0 - padding:.4f},'
        f'{label_y:.4f})">'
        f'<rect width="{label_width:.4f}" height="4.2" rx="2.1" fill="#0875ff"/>'
        f'<text x="2.2" y="2.9" fill="#ffffff" font-size="1.25" font-family="Segoe UI, sans-serif" '
        f'font-weight="700">{_escape(block.id)}</text></g>'
    )
    return highlight + shape


def create_dwg_svg(
    document: CanonicalDocument,
    highlight_block_id: str | None = None,
    page_number: int | None = None,
) -> bytes:
    """Render canonical DWG entities as a standalone, citation-aware SVG."""
    if document.format != "dwg":
        raise ValueError("DWG SVG rendering requires a DWG canonical document.")
    if not document.pages:
        raise ValueError("The DWG document has no layouts to render.")

    highlighted = next((block for block in document.blocks if block.id == highlight_block_id), None)
    selected_page = page_number or (highlighted.page if highlighted else document.pages[0].number)
    page = next((item for item in document.pages if item.number == selected_page), document.pages[0])
    width, height = max(1.0, page.width), max(1.0, page.height)
    margin = max(5.0, max(width, height) * .06)
    view_x, view_y = -margin, -margin
    view_width, view_height = width + margin * 2, height + margin * 2
    layout_names = document.metadata.get("layouts", [])
    layout = layout_names[page.number - 1] if page.number <= len(layout_names) else f"Layout {page.number}"
    layers = ", ".join(document.metadata.get("layers", [])) or "0"
    warning = " · ".join(document.warnings)
    entities = "".join(
        _drawing_entity(block, height, block.id == highlight_block_id)
        for block in page.blocks
    )
    metadata = (
        f"{document.metadata.get('entity_count', len(document.blocks))} entities · "
        f"{len(document.metadata.get('layers', [])) or 1} layers"
    )
    accessible_description = _short_label(highlighted) if highlighted else (
        f"{document.filename}, {layout}, layers {layers}"
    )
    warning_element = (
        f'<text y="2.8" fill="#8a641d" font-size="1.35" '
        f'font-family="Segoe UI, sans-serif" class="audit-note">{_escape(warning)}</text>'
        if warning
        else ""
    )

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" role="img"
     aria-labelledby="drawing-title drawing-description"
     viewBox="{view_x:.4f} {view_y:.4f} {view_width:.4f} {view_height:.4f}"
     preserveAspectRatio="xMidYMid meet">
  <title id="drawing-title">{_escape(document.filename)} — {_escape(layout)}</title>
  <desc id="drawing-description">{_escape(accessible_description)}</desc>
  <defs>
    <style>
      .cad-entity {{ fill:none; stroke:#263746; stroke-width:.34; vector-effect:non-scaling-stroke;
        stroke-linecap:round; stroke-linejoin:round; }}
      .piping {{ stroke:#1d4e75; stroke-width:.55; }}
      .equipment {{ stroke:#111f2d; stroke-width:.48; fill:#f9fbfd; }}
      .block {{ stroke:#8a4b12; fill:#fff8e8; }}
      .dimension {{ stroke:#596b7e; stroke-width:.26; }}
      .annotation {{ fill:#172737; stroke:none; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        font-weight:600; letter-spacing:.015em; }}
      .geometry {{ stroke:#687c91; stroke-dasharray:1.2 .6; }}
      .is-highlighted {{ stroke:#096eff !important; fill:#d9eaff !important; stroke-width:1 !important; }}
      .citation-highlight {{ fill:#1683ff; fill-opacity:.13; stroke:#0875ff; stroke-width:.65;
        stroke-dasharray:1.4 .7; vector-effect:non-scaling-stroke; }}
      .citation-label rect {{ fill:#0875ff; }}
      .citation-label text {{ fill:white; font:700 1.25px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
      .sheet-meta {{ fill:#60748a; font:500 1.6px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
      .sheet-title {{ fill:#13263a; font:700 2.25px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
      .audit-note {{ fill:#8a641d; font:500 1.35px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    </style>
  </defs>
  <rect x="{view_x + margin * .13:.4f}" y="{view_y + margin * .13:.4f}"
        width="{view_width - margin * .26:.4f}" height="{view_height - margin * .26:.4f}"
        rx="{margin * .16:.4f}" fill="#eef3f8" stroke="#cbd6e2" stroke-width=".18"/>
  <rect x="0" y="0" width="{width:.4f}" height="{height:.4f}" fill="#ffffff"/>
  <g id="cad-entities">{entities}</g>
  <g transform="translate(0,{height + margin * .37:.4f})">
    <text y="0" fill="#13263a" font-size="2.25" font-family="Segoe UI, sans-serif"
          font-weight="700" class="sheet-title">{_escape(layout)} · {_escape(document.filename)}</text>
    <text x="{width:.4f}" y="0" text-anchor="end" fill="#60748a" font-size="1.6"
          font-family="Segoe UI, sans-serif" class="sheet-meta">{_escape(metadata)} · {_escape(layers)}</text>
    {warning_element}
  </g>
</svg>"""
    return svg.encode("utf-8")
