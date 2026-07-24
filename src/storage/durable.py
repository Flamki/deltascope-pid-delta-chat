from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any


class DurableStore:
    """Private Vercel Blob persistence with an explicit disabled local mode."""

    def __init__(
        self,
        *,
        token: str | None = None,
        prefix: str = "deltascope/v1",
        client_factory: Callable[..., Any] | None = None,
    ):
        self.token = (token if token is not None else os.getenv("BLOB_READ_WRITE_TOKEN", "")).strip()
        self.prefix = prefix.strip("/")
        self._client_factory = client_factory

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory(token=self.token)
        from vercel.blob import BlobClient

        return BlobClient(token=self.token)

    def _path(self, relative_path: str) -> str:
        relative = relative_path.strip("/")
        if not relative or ".." in relative.split("/"):
            raise ValueError("Invalid durable storage path.")
        return f"{self.prefix}/{relative}"

    def put_bytes(self, relative_path: str, content: bytes, content_type: str) -> None:
        if not self.enabled:
            return
        client = self._client()
        try:
            client.put(
                self._path(relative_path),
                content,
                access="private",
                content_type=content_type,
                overwrite=True,
                cache_control_max_age=60,
                token=self.token,
            )
        finally:
            client.close()

    def get_bytes(self, relative_path: str) -> bytes | None:
        if not self.enabled:
            return None
        client = self._client()
        try:
            result = client.get(
                self._path(relative_path),
                access="private",
                use_cache=False,
                token=self.token,
            )
            if result is None or result.status_code != 200:
                return None
            return bytes(result.content)
        except Exception as exc:
            if exc.__class__.__name__ == "BlobNotFoundError":
                return None
            raise
        finally:
            client.close()

    def put_json(self, relative_path: str, payload: dict) -> None:
        self.put_bytes(
            relative_path,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            "application/json",
        )

    def get_json(self, relative_path: str) -> dict | None:
        content = self.get_bytes(relative_path)
        if content is None:
            return None
        return json.loads(content.decode("utf-8"))

    def list_paths(self, relative_prefix: str, limit: int = 1000) -> list[str]:
        if not self.enabled:
            return []
        client = self._client()
        full_prefix = self._path(relative_prefix)
        paths: list[str] = []
        cursor = None
        try:
            while len(paths) < limit:
                result = client.list_objects(
                    prefix=full_prefix,
                    cursor=cursor,
                    limit=min(1000, limit - len(paths)),
                    token=self.token,
                )
                paths.extend(item.pathname.removeprefix(f"{self.prefix}/") for item in result.blobs)
                if not result.has_more or not result.cursor:
                    break
                cursor = result.cursor
        finally:
            client.close()
        return paths

    def save_session(
        self,
        session_id: str,
        state: dict,
        base_content: bytes,
        revised_content: bytes,
        base_extension: str,
        revised_extension: str,
    ) -> None:
        if not self.enabled:
            return
        base_key = f"sessions/{session_id}/source-a{base_extension.lower()}"
        revised_key = f"sessions/{session_id}/source-b{revised_extension.lower()}"
        self.put_bytes(base_key, base_content, _source_content_type(base_extension))
        self.put_bytes(revised_key, revised_content, _source_content_type(revised_extension))
        durable_state = {
            **state,
            "base_blob": base_key,
            "revised_blob": revised_key,
            "storage_version": 1,
        }
        # The state object is the commit marker and is intentionally written last.
        self.put_json(f"sessions/{session_id}/session.json", durable_state)

    def load_session(self, session_id: str) -> tuple[dict, bytes, bytes] | None:
        state = self.get_json(f"sessions/{session_id}/session.json")
        if not state:
            return None
        base_content = self.get_bytes(state["base_blob"])
        revised_content = self.get_bytes(state["revised_blob"])
        if base_content is None or revised_content is None:
            raise RuntimeError(f"Durable session {session_id} is incomplete.")
        return state, base_content, revised_content

    def save_trace(self, trace: dict) -> None:
        session_id = trace.get("session_id")
        if not self.enabled or not session_id:
            return
        timestamp = int(float(trace.get("started_at", 0)) * 1_000_000)
        key = f"sessions/{session_id}/traces/{timestamp:020d}-{trace['trace_id']}.json"
        self.put_json(key, trace)

    def list_traces(self, session_id: str, limit: int = 50) -> list[dict]:
        if not self.enabled:
            return []
        prefix = f"sessions/{session_id}/traces/"
        paths = sorted(self.list_paths(prefix, limit=1000))[-limit:]
        traces = [self.get_json(path) for path in paths]
        return [trace for trace in traces if trace is not None]


def _source_content_type(extension: str) -> str:
    return "application/pdf" if extension.lower() == ".pdf" else "application/acad"
