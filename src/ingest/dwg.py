from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

try:
    import ezdxf
    from ezdxf import bbox, recover

    EZDXF_AVAILABLE = True
    DXFError = ezdxf.DXFError
except ImportError:
    ezdxf = None
    bbox = None
    recover = None
    EZDXF_AVAILABLE = False
    DXFError = RuntimeError

from src.canonical import CanonicalBlock, CanonicalDocument, CanonicalPage, Region
from .base import FormatAdapter
from .pdf_native import classify_text

ROOT = Path(__file__).resolve().parents[2]
GEOMETRY_TYPES = {
    "3DFACE",
    "ARC",
    "CIRCLE",
    "ELLIPSE",
    "HATCH",
    "HELIX",
    "LEADER",
    "LINE",
    "LWPOLYLINE",
    "MESH",
    "MLINE",
    "POINT",
    "POLYLINE",
    "RAY",
    "REGION",
    "SOLID",
    "SPLINE",
    "TRACE",
    "WIPEOUT",
    "XLINE",
}
TEXT_TYPES = {"ATTRIB", "ATTDEF", "MTEXT", "TEXT"}


@dataclass
class EntityRecord:
    entity_type: str
    layer: str
    text: str
    kind: str
    bounds: tuple[float, float, float, float]
    confidence: float = 0.98


def _number(value) -> str:
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _point(value) -> str:
    return f"({_number(value.x)},{_number(value.y)})"


def _entity_bounds(entity, cache: bbox.Cache) -> tuple[float, float, float, float]:
    try:
        extents = bbox.extents([entity], fast=True, cache=cache)
        if extents.has_data:
            return (
                float(extents.extmin.x),
                float(extents.extmin.y),
                float(extents.extmax.x),
                float(extents.extmax.y),
            )
    except (AttributeError, TypeError, ValueError, DXFError):
        pass
    insert = entity.dxf.get("insert")
    if insert is not None:
        x, y = float(insert.x), float(insert.y)
        return x, y, x + 1.0, y + 1.0
    return 0.0, 0.0, 1.0, 1.0


def _entity_description(entity) -> tuple[str, str]:
    entity_type = entity.dxftype()
    layer = str(entity.dxf.get("layer", "0"))
    if entity_type in {"TEXT", "ATTRIB", "ATTDEF"}:
        text = str(entity.dxf.get("text", "")).strip()
        return text or f"{entity_type} on layer {layer}", classify_text(text)
    if entity_type == "MTEXT":
        text = entity.plain_text().strip()
        return text or f"MTEXT on layer {layer}", classify_text(text)
    if entity_type == "DIMENSION":
        try:
            measurement = entity.get_measurement()
            if isinstance(measurement, tuple):
                measurement_text = " x ".join(_number(item) for item in measurement)
            else:
                measurement_text = _number(measurement)
        except (AttributeError, TypeError, ValueError, DXFError):
            measurement_text = str(entity.dxf.get("text", "")).strip() or "unknown"
        return f"DIMENSION layer={layer} measurement={measurement_text}", "dimension"
    if entity_type == "INSERT":
        name = str(entity.dxf.get("name", "unnamed"))
        attributes = [
            f"{attribute.dxf.get('tag', 'ATTR')}={attribute.dxf.get('text', '')}"
            for attribute in getattr(entity, "attribs", [])
        ]
        suffix = f" {' '.join(attributes)}" if attributes else ""
        text = f"BLOCK layer={layer} name={name} insert={_point(entity.dxf.insert)}{suffix}"
        return text, "instrument" if re.search(r"\b(?:P|T|F|L|K)[A-Z]?\d", name.upper()) else "geometry"
    if entity_type == "LINE":
        return (
            f"LINE layer={layer} start={_point(entity.dxf.start)} end={_point(entity.dxf.end)}",
            "geometry",
        )
    if entity_type == "CIRCLE":
        return (
            f"CIRCLE layer={layer} center={_point(entity.dxf.center)} radius={_number(entity.dxf.radius)}",
            "geometry",
        )
    if entity_type == "ARC":
        return (
            f"ARC layer={layer} center={_point(entity.dxf.center)} radius={_number(entity.dxf.radius)} "
            f"angles={_number(entity.dxf.start_angle)}..{_number(entity.dxf.end_angle)}",
            "geometry",
        )
    if entity_type == "LWPOLYLINE":
        points = list(entity.get_points("xy"))
        preview = ",".join(f"({_number(x)},{_number(y)})" for x, y in points[:6])
        return f"LWPOLYLINE layer={layer} vertices={len(points)} points={preview}", "geometry"
    if entity_type == "POLYLINE":
        points = [vertex.dxf.location for vertex in entity.vertices]
        preview = ",".join(_point(point) for point in points[:6])
        return f"POLYLINE layer={layer} vertices={len(points)} points={preview}", "geometry"
    if entity_type == "POINT":
        return f"POINT layer={layer} location={_point(entity.dxf.location)}", "geometry"
    if entity_type == "ELLIPSE":
        return (
            f"ELLIPSE layer={layer} center={_point(entity.dxf.center)} major_axis={_point(entity.dxf.major_axis)} "
            f"ratio={_number(entity.dxf.ratio)}",
            "geometry",
        )
    if entity_type == "SPLINE":
        return (
            f"SPLINE layer={layer} degree={entity.dxf.get('degree', 0)} "
            f"control_points={len(entity.control_points)}",
            "geometry",
        )
    return f"{entity_type} layer={layer} handle={entity.dxf.get('handle', '')}", "geometry"


def parse_dxf(pid: str, dxf_path: Path) -> tuple[list[CanonicalPage], dict, list[str]]:
    if not EZDXF_AVAILABLE:
        raise RuntimeError("The optional ezdxf dependency is not installed.")
    ezdxf_logger = logging.getLogger("ezdxf")
    previous_level = ezdxf_logger.level
    ezdxf_logger.setLevel(logging.CRITICAL)
    try:
        document, auditor = recover.readfile(dxf_path)
    finally:
        ezdxf_logger.setLevel(previous_level)
    pages: list[CanonicalPage] = []
    entity_counts: Counter[str] = Counter()
    layers: set[str] = set()
    layout_names: list[str] = []
    cache = bbox.Cache()

    for layout in document.layouts:
        records: list[EntityRecord] = []
        for entity in layout:
            entity_type = entity.dxftype()
            if entity_type not in TEXT_TYPES | GEOMETRY_TYPES | {"DIMENSION", "INSERT"}:
                continue
            layer = str(entity.dxf.get("layer", "0"))
            description, kind = _entity_description(entity)
            if not description:
                continue
            records.append(
                EntityRecord(
                    entity_type=entity_type,
                    layer=layer,
                    text=description,
                    kind=kind,
                    bounds=_entity_bounds(entity, cache),
                )
            )
            entity_counts[entity_type] += 1
            layers.add(layer)
        if not records:
            continue

        minimum_x = min(record.bounds[0] for record in records)
        minimum_y = min(record.bounds[1] for record in records)
        maximum_x = max(record.bounds[2] for record in records)
        maximum_y = max(record.bounds[3] for record in records)
        page_number = len(pages) + 1
        blocks = []
        for index, record in enumerate(records, 1):
            x0, y0, x1, y1 = record.bounds
            blocks.append(
                CanonicalBlock(
                    id=f"{pid}-DWG-P{page_number}-E{index}",
                    page=page_number,
                    text=record.text,
                    region=Region(
                        round(x0 - minimum_x, 4),
                        round(y0 - minimum_y, 4),
                        round(max(x1 - minimum_x, x0 - minimum_x + 0.001), 4),
                        round(max(y1 - minimum_y, y0 - minimum_y + 0.001), 4),
                    ),
                    kind=record.kind,
                    confidence=record.confidence,
                )
            )
        pages.append(
            CanonicalPage(
                number=page_number,
                width=round(max(1.0, maximum_x - minimum_x), 4),
                height=round(max(1.0, maximum_y - minimum_y), 4),
                blocks=blocks,
            )
        )
        layout_names.append(layout.name)

    warnings = []
    audit_count = len(auditor.errors) + len(auditor.fixes)
    if audit_count:
        warnings.append(f"DXF recovery reported {audit_count} audit issue(s); inspect affected entities.")
    metadata = {
        "dxf_version": document.dxfversion,
        "geometry_available": True,
        "entity_count": sum(entity_counts.values()),
        "entity_counts": dict(sorted(entity_counts.items())),
        "layers": sorted(layers),
        "layouts": layout_names,
        "audit_issues": audit_count,
    }
    return pages or [CanonicalPage(1, 1, 1, [])], metadata, warnings


class DwgAdapter(FormatAdapter):
    """DWG adapter using LibreDWG conversion plus ezdxf entity extraction."""

    name = "dwg_libredwg_ezdxf"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".dwg"

    @staticmethod
    def converter_path() -> Path | None:
        configured = os.getenv("DWG_CONVERTER_PATH", "").strip()
        candidates = []
        if configured:
            configured_path = Path(configured)
            candidates.append(
                configured_path / ("dwg2dxf.exe" if os.name == "nt" else "dwg2dxf")
                if configured_path.is_dir()
                else configured_path
            )
        discovered = shutil.which("dwg2dxf")
        if discovered:
            candidates.append(Path(discovered))
        candidates.extend(
            [
                ROOT / ".tools" / "libredwg" / "dwg2dxf.exe",
                ROOT / ".tools" / "libredwg" / "dwg2dxf",
            ]
        )
        return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)

    @classmethod
    def converter_available(cls) -> bool:
        return EZDXF_AVAILABLE and cls.converter_path() is not None

    def ingest(self, pid: str, path: Path) -> CanonicalDocument:
        raw = path.read_bytes()
        signature = raw[:6].decode("ascii", errors="replace")
        converter = self.converter_path()
        if converter and EZDXF_AVAILABLE:
            try:
                with tempfile.TemporaryDirectory(prefix="deltascope-dwg-") as temporary:
                    dxf_path = Path(temporary) / f"{path.stem}.dxf"
                    result = subprocess.run(
                        [str(converter), "-y", "-o", str(dxf_path), str(path.resolve())],
                        capture_output=True,
                        text=True,
                        timeout=int(os.getenv("DWG_CONVERTER_TIMEOUT_SECONDS", "60")),
                        check=False,
                    )
                    if result.returncode != 0 or not dxf_path.is_file():
                        message = (result.stderr or result.stdout or "conversion failed").strip()[:500]
                        raise ValueError(message)
                    pages, geometry_metadata, warnings = parse_dxf(pid, dxf_path)
                return CanonicalDocument(
                    pid=pid,
                    filename=path.name,
                    format="dwg",
                    adapter=self.name,
                    pages=pages,
                    metadata={
                        "signature": signature,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "size_bytes": len(raw),
                        "converter": str(converter),
                        **geometry_metadata,
                    },
                    warnings=warnings,
                )
            except (OSError, subprocess.SubprocessError, ValueError, DXFError) as exc:
                return self._fallback(pid, path, raw, signature, f"LibreDWG conversion failed: {exc}")
        if converter and not EZDXF_AVAILABLE:
            return self._fallback(
                pid,
                path,
                raw,
                signature,
                "Install the optional DWG parser with `uv sync --extra dwg` for full geometry.",
            )
        return self._fallback(
            pid,
            path,
            raw,
            signature,
            "Install LibreDWG with `make dwg-setup` or configure DWG_CONVERTER_PATH for full geometry.",
        )

    def _fallback(
        self,
        pid: str,
        path: Path,
        raw: bytes,
        signature: str,
        warning: str,
    ) -> CanonicalDocument:
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
        return CanonicalDocument(
            pid=pid,
            filename=path.name,
            format="dwg",
            adapter="dwg_binary_fallback",
            pages=[CanonicalPage(1, 1000, max(1000, len(blocks) * 12), blocks)],
            metadata={
                "signature": signature,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "geometry_available": False,
            },
            warnings=[warning],
        )
