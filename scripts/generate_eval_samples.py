from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "eval"
TARGET.mkdir(parents=True, exist_ok=True)


def native(name: str, lines: list[str]):
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((54, 52), "DELTASCOPE EVALUATION DRAWING", fontsize=16)
    for index, line in enumerate(lines):
        page.insert_text((54, 110 + index * 38), line, fontsize=14)
    document.save(TARGET / name)
    document.close()


def scanned(name: str, lines: list[str]):
    image = Image.new("RGB", (1500, 1900), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 44)
    heading = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 52)
    draw.text((110, 100), "COMPRESSOR CONTROL SETPOINTS", fill="black", font=heading)
    for index, line in enumerate(lines):
        draw.text((110, 280 + index * 120), line, fill="black", font=font)
    image.save(TARGET / name, "PDF", resolution=150)


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

print(f"Generated evaluation samples in {TARGET}")

