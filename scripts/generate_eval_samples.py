from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "eval"
TARGET.mkdir(parents=True, exist_ok=True)


def save_deterministic(document: fitz.Document, path: Path):
    document.set_metadata({})
    document.save(path, garbage=4, deflate=True, no_new_id=True)
    document.close()


def native(name: str, lines: list[str]):
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((54, 52), "DELTASCOPE EVALUATION DRAWING", fontsize=16)
    for index, line in enumerate(lines):
        page.insert_text((54, 110 + index * 38), line, fontsize=14)
    save_deterministic(document, TARGET / name)


def native_positioned(name: str, entries: list[tuple[float, float, str]]):
    """Generate a page whose text order and geometry can change independently."""

    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((54, 52), "DELTASCOPE LAYOUT REGRESSION", fontsize=16)
    for x, y, line in entries:
        page.insert_text((x, y), line, fontsize=14)
    save_deterministic(document, TARGET / name)


def scanned(name: str, lines: list[str]):
    source = fitz.open()
    page = source.new_page(width=612, height=792)
    page.insert_text((54, 52), "COMPRESSOR CONTROL SETPOINTS", fontsize=18)
    for index, line in enumerate(lines):
        page.insert_text((54, 120 + index * 48), line, fontsize=16)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
    source.close()

    scanned_document = fitz.open()
    scanned_page = scanned_document.new_page(width=612, height=792)
    scanned_page.insert_image(scanned_page.rect, stream=pixmap.tobytes("png"))
    save_deterministic(scanned_document, TARGET / name)


native(
    "native-equipment-a.pdf",
    ["PUMP P-101", "DESIGN PRESSURE 10 BARG", "NOTE 1 CARBON STEEL"],
)
native(
    "native-equipment-b.pdf",
    ["PUMP P-101", "DESIGN PRESSURE 12 BARG", "NOTE 1 STAINLESS STEEL", "PSV-102 ADDED"],
)
scanned(
    "scanned-setpoint-a.pdf",
    ["COMPRESSOR K-201", "HIGH TRIP 80 BARG"],
)
scanned(
    "scanned-setpoint-b.pdf",
    ["COMPRESSOR K-201", "HIGH TRIP 85 BARG", "HIGH ALARM 75 BARG"],
)
native(
    "native-note-a.pdf",
    ["NOTE 4 VENT TO ATMOSPHERE", "NOTE 5 DRAIN TO OPEN DRAIN"],
)
native(
    "native-note-b.pdf",
    ["NOTE 4 VENT ROUTED TO SAFE LOCATION", "NOTE 5 DRAIN TO CLOSED DRAIN"],
)
native_positioned(
    "native-layout-a.pdf",
    [
        (54, 130, "PUMP P-301"),
        (54, 180, "DESIGN PRESSURE 40 BARG"),
        (54, 230, "NOTE 8 ROUTE TO FLARE"),
    ],
)
native_positioned(
    "native-layout-b.pdf",
    [
        (330, 560, "NOTE 8 ROUTE TO FLARE"),
        (330, 610, "PUMP P-301"),
        (330, 660, "DESIGN PRESSURE 45 BARG"),
    ],
)

print(f"Generated evaluation samples in {TARGET}")
