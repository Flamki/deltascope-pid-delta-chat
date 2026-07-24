from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".tools" / "libredwg"
API = "https://api.github.com/repos/LibreDWG/libredwg/releases/latest"
USER_AGENT = "DeltaScope-LibreDWG-Setup"


def request(url: str, accept: str = "application/vnd.github+json"):
    return urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def download(url: str, destination: Path, expected_size: int):
    last_error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request(url, "application/octet-stream"), timeout=90) as response:
                with destination.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
            if destination.stat().st_size != expected_size:
                raise RuntimeError(
                    f"downloaded {destination.stat().st_size} bytes; expected {expected_size}"
                )
            return
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError(f"LibreDWG download failed: {last_error}")


def main():
    existing = TARGET / ("dwg2dxf.exe" if os.name == "nt" else "dwg2dxf")
    if existing.is_file():
        print(f"LibreDWG already available at {existing}")
        return
    discovered = shutil.which("dwg2dxf")
    if discovered:
        print(f"LibreDWG already available on PATH at {discovered}")
        return
    if platform.system() != "Windows":
        raise SystemExit(
            "Automatic setup currently targets the official Windows release. "
            "Install GNU LibreDWG with your package manager and ensure dwg2dxf is on PATH."
        )

    with urllib.request.urlopen(request(API), timeout=30) as response:
        release = json.loads(response.read())
    architecture = "win64" if platform.machine().endswith("64") else "win32"
    asset = next(
        (
            item
            for item in release.get("assets", [])
            if item["name"].endswith(f"-{architecture}.zip")
            and not item["name"].endswith(".sig")
        ),
        None,
    )
    if not asset:
        raise SystemExit(f"Release {release.get('tag_name')} has no {architecture} zip asset.")

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deltascope-libredwg-") as temporary:
        archive = Path(temporary) / asset["name"]
        download(asset["url"], archive, int(asset["size"]))
        with zipfile.ZipFile(archive) as source:
            target_root = TARGET.resolve()
            for member in source.infolist():
                destination = (TARGET / member.filename).resolve()
                if not destination.is_relative_to(target_root):
                    raise RuntimeError(f"Unsafe path in LibreDWG archive: {member.filename}")
            source.extractall(TARGET)
    result = subprocess.run(
        [str(existing), "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit("LibreDWG was extracted but dwg2dxf did not start.")
    print(f"Installed LibreDWG {release['tag_name']} at {TARGET}")
    print((result.stdout or result.stderr).strip())


if __name__ == "__main__":
    main()
