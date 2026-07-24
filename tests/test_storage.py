from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from src.observability import TraceStore
from src.storage import DurableStore


@dataclass
class FakeBlob:
    pathname: str


@dataclass
class FakeGetResult:
    content: bytes
    status_code: int = 200


@dataclass
class FakeListResult:
    blobs: list[FakeBlob]
    cursor: str | None = None
    has_more: bool = False


class FakeBlobClient:
    def __init__(self, bucket: dict[str, bytes]):
        self.bucket = bucket

    def put(self, path, body, **_kwargs):
        self.bucket[path] = bytes(body)

    def get(self, path, **_kwargs):
        content = self.bucket.get(path)
        return FakeGetResult(content) if content is not None else None

    def list_objects(self, *, prefix, **_kwargs):
        return FakeListResult([FakeBlob(path) for path in sorted(self.bucket) if path.startswith(prefix)])

    def close(self):
        return None


class DurableStorageTests(unittest.TestCase):
    def setUp(self):
        self.bucket: dict[str, bytes] = {}
        self.store = DurableStore(
            token="test-token",
            client_factory=lambda **_kwargs: FakeBlobClient(self.bucket),
        )

    def test_session_survives_a_fresh_store_instance(self):
        state = {
            "id": "CMP-ABC123",
            "created_at": 1.0,
            "base": {"filename": "base.pdf"},
            "revised": {"filename": "revised.pdf"},
            "report": {"counts": {"added": 1, "removed": 0, "modified": 0}},
        }
        self.store.save_session("CMP-ABC123", state, b"%PDF-base", b"%PDF-revised", ".pdf", ".pdf")
        fresh = DurableStore(
            token="test-token",
            client_factory=lambda **_kwargs: FakeBlobClient(self.bucket),
        )

        restored_state, base_content, revised_content = fresh.load_session("CMP-ABC123")

        self.assertEqual(restored_state["report"], state["report"])
        self.assertEqual(base_content, b"%PDF-base")
        self.assertEqual(revised_content, b"%PDF-revised")
        self.assertEqual(restored_state["storage_version"], 1)

    def test_trace_store_reads_remote_traces_in_newest_first_order(self):
        first = {
            "trace_id": "trace-1",
            "session_id": "CMP-ABC123",
            "request": "compare",
            "started_at": 1.0,
            "spans": [],
            "status": "ok",
            "duration_ms": 10,
            "telemetry": {},
        }
        second = {**first, "trace_id": "trace-2", "request": "chat", "started_at": 2.0}
        self.store.save_trace(first)
        self.store.save_trace(second)

        with tempfile.TemporaryDirectory() as temp:
            trace_store = TraceStore(Path(temp) / "traces.jsonl", durable_store=self.store)
            traces = trace_store.list("CMP-ABC123")

        self.assertEqual([trace["trace_id"] for trace in traces], ["trace-2", "trace-1"])


if __name__ == "__main__":
    unittest.main()
