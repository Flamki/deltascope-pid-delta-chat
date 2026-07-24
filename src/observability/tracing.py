from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path


class TraceStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def start(self, request: str, session_id: str | None = None) -> dict:
        return {
            "trace_id": str(uuid.uuid4()),
            "session_id": session_id,
            "request": request,
            "started_at": time.time(),
            "spans": [],
            "status": "running",
            "telemetry": {},
        }

    @staticmethod
    def span(trace: dict, name: str, started: float, **attributes):
        trace["spans"].append(
            {
                "name": name,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "attributes": attributes,
            }
        )

    def finish(self, trace: dict, status: str = "ok", error: str | None = None):
        trace["status"] = status
        trace["duration_ms"] = round((time.time() - trace["started_at"]) * 1000, 2)
        if error:
            trace["error"] = error
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(trace, ensure_ascii=False) + "\n")

    def list(self, session_id: str | None = None, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if not session_id or row.get("session_id") == session_id:
                    rows.append(row)
            except json.JSONDecodeError:
                continue
        return list(reversed(rows[-limit:]))

