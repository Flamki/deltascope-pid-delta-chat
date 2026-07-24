from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "eval"
CONVERTER = ROOT / ".tools" / "libredwg" / "dxf2dwg.exe"


def create_dxf(path: Path, pressure: int, pipe_end: int, valve_x: int, add_psv: bool):
    document = ezdxf.new("R2000")
    for name, color in (
        ("EQUIPMENT", 3),
        ("PIPING", 5),
        ("INSTRUMENT", 1),
        ("ANNOTATION", 7),
    ):
        document.layers.add(name, color=color)
    valve = document.blocks.new("CONTROL_VALVE")
    valve.add_line((-3, -3), (3, 3), dxfattribs={"layer": "INSTRUMENT"})
    valve.add_line((-3, 3), (3, -3), dxfattribs={"layer": "INSTRUMENT"})
    valve.add_circle((0, 0), 4, dxfattribs={"layer": "INSTRUMENT"})

    modelspace = document.modelspace()
    modelspace.add_line((0, 0), (pipe_end, 0), dxfattribs={"layer": "PIPING"})
    modelspace.add_circle((30, 0), 6, dxfattribs={"layer": "EQUIPMENT"})
    modelspace.add_text(
        "PUMP P-101",
        height=3,
        dxfattribs={"layer": "ANNOTATION"},
    ).set_placement((23, 9))
    modelspace.add_text(
        f"DESIGN PRESSURE {pressure} BARG",
        height=3,
        dxfattribs={"layer": "ANNOTATION"},
    ).set_placement((0, 20))
    modelspace.add_blockref(
        "CONTROL_VALVE",
        (valve_x, 0),
        dxfattribs={"layer": "INSTRUMENT"},
    )
    modelspace.add_text(
        "FV-101",
        height=2.5,
        dxfattribs={"layer": "ANNOTATION"},
    ).set_placement((valve_x - 4, 7))
    dimension = modelspace.add_linear_dim(
        base=(0, -12),
        p1=(0, 0),
        p2=(pipe_end, 0),
        angle=0,
        dimstyle="EZDXF",
        dxfattribs={"layer": "ANNOTATION"},
    )
    dimension.render()
    if add_psv:
        modelspace.add_circle((90, 8), 4, dxfattribs={"layer": "INSTRUMENT"})
        modelspace.add_text(
            "PSV-102 ADDED",
            height=2.5,
            dxfattribs={"layer": "ANNOTATION"},
        ).set_placement((84, 14))
    document.saveas(path)


def convert(source: Path, target: Path):
    if not CONVERTER.is_file():
        raise SystemExit("LibreDWG is missing. Run `make dwg-setup` first.")
    with tempfile.TemporaryDirectory(prefix="deltascope-dwg-fixture-") as temporary:
        output = Path(temporary) / target.name
        result = subprocess.run(
            [str(CONVERTER), "--as", "r2000", "-o", str(output), str(source)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError((result.stderr or result.stdout or "dxf2dwg failed").strip())
        target.write_bytes(output.read_bytes())


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deltascope-dxf-source-") as temporary:
        folder = Path(temporary)
        pairs = [
            ("dwg-geometry-a", 10, 100, 60, False),
            ("dwg-geometry-b", 12, 110, 70, True),
        ]
        for name, pressure, pipe_end, valve_x, add_psv in pairs:
            dxf_path = folder / f"{name}.dxf"
            dwg_path = TARGET / f"{name}.dwg"
            create_dxf(dxf_path, pressure, pipe_end, valve_x, add_psv)
            convert(dxf_path, dwg_path)
            print(f"Generated {dwg_path}")


if __name__ == "__main__":
    main()
