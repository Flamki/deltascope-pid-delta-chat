from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.canonical import CanonicalDocument
from src.chat import answer_question
from src.delta import compare_documents
from src.ingest.router import AdapterRouter
from src.markup import create_dwg_svg, create_highlight_pdf, create_markup_pdf
from src.observability import TraceStore

SOURCE_ROOT = Path(__file__).parent.resolve()
IS_VERCEL = bool(os.getenv("VERCEL"))
STORAGE_ROOT = Path(
    os.getenv(
        "DELTASCOPE_STORAGE_ROOT",
        str(Path(tempfile.gettempdir()) / "deltascope") if IS_VERCEL else str(SOURCE_ROOT),
    )
).resolve()


def load_local_environment(path: Path):
    """Load an ignored local .env file without adding a runtime dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_local_environment(SOURCE_ROOT / ".env")
WEB = SOURCE_ROOT / "web"
UPLOADS = STORAGE_ROOT / "artifacts" / "uploads"
REPORTS = STORAGE_ROOT / "artifacts" / "reports"
UPLOADS.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)
trace_path = Path(os.getenv("TRACE_PATH", "artifacts/traces.jsonl"))
if not trace_path.is_absolute():
    trace_path = STORAGE_ROOT / trace_path
TRACE_STORE = TraceStore(trace_path)
ROUTER = AdapterRouter()
SESSIONS: dict[str, "ComparisonSession"] = {}
SESSION_LOCK = threading.Lock()
MAX_FILE_BYTES = int(
    os.getenv(
        "MAX_FILE_BYTES",
        str(2 * 1024 * 1024 if IS_VERCEL else 75 * 1024 * 1024),
    )
)
MAX_COMPARISON_BYTES = int(
    os.getenv(
        "MAX_COMPARISON_BYTES",
        str(4 * 1024 * 1024 if IS_VERCEL else MAX_FILE_BYTES * 2 + 2 * 1024 * 1024),
    )
)
ALLOWED_EXTENSIONS = {".pdf", ".dwg"}


@dataclass
class ComparisonSession:
    id: str
    created_at: float
    base_path: Path
    revised_path: Path
    base: object
    revised: object
    report: dict

    def summary(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "documents": {
                "PID-A": self.base.to_dict(include_blocks=False),
                "PID-B": self.revised.to_dict(include_blocks=False),
            },
            "report": self.report,
            "links": {
                "base": f"/api/sessions/{self.id}/documents/PID-A",
                "revised": f"/api/sessions/{self.id}/documents/PID-B",
                "report_json": f"/api/sessions/{self.id}/report.json",
                "report_markdown": f"/api/sessions/{self.id}/report.md",
                "report_html": f"/api/sessions/{self.id}/report.html",
                "markup_base": f"/api/sessions/{self.id}/markup/PID-A.pdf" if self.base_path.suffix.lower() == ".pdf" else None,
                "markup_revised": f"/api/sessions/{self.id}/markup/PID-B.pdf" if self.revised_path.suffix.lower() == ".pdf" else None,
            },
        }


def safe_filename(value: str, fallback: str) -> str:
    name = Path(value or fallback).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return cleaned or fallback


def write_report_files(session: ComparisonSession):
    target = REPORTS / session.id
    target.mkdir(parents=True, exist_ok=True)
    payload = session.summary()
    (target / "report.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    state = {
        "id": session.id,
        "created_at": session.created_at,
        "base_path": str(session.base_path.relative_to(STORAGE_ROOT)),
        "revised_path": str(session.revised_path.relative_to(STORAGE_ROOT)),
        "base": session.base.to_dict(),
        "revised": session.revised.to_dict(),
        "report": session.report,
    }
    (target / "session.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    counts = session.report["counts"]
    lines = [
        f"# Delta report: {session.base.filename} vs {session.revised.filename}",
        "",
        f"- Comparison ID: `{session.id}`",
        f"- Alignment: {session.report['alignment']['score']:.1%} ({session.report['alignment']['status']})",
        f"- Added: {counts['added']}",
        f"- Removed: {counts['removed']}",
        f"- Modified: {counts['modified']}",
        "",
        f"> {session.report['alignment']['message']}",
        "",
        "## Findings",
        "",
    ]
    for finding in session.report["findings"]:
        reference = finding.get("after") or finding.get("before") or {}
        lines.extend(
            [
                f"### {finding['id']} - {finding['change_type'].title()} {finding['item_type']}",
                "",
                finding["description"],
                "",
                f"- Severity: {finding['severity']}",
                f"- Confidence: {finding['confidence']:.1%}",
                f"- Citation: {reference.get('source')} / page {reference.get('page')} / block `{reference.get('block_id')}`",
                "",
            ]
        )
    markdown = "\n".join(lines)
    (target / "report.md").write_text(markdown, encoding="utf-8")
    findings_html = "".join(
        f"<article><small>{html.escape(f['id'])} · {html.escape(f['change_type'])} · {html.escape(f['severity'])}</small>"
        f"<h2>{html.escape(f['item_type'].replace('_', ' ').title())}</h2>"
        f"<p>{html.escape(f['description'])}</p><strong>{f['confidence']:.1%} confidence</strong></article>"
        for f in session.report["findings"]
    )
    report_html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Delta report</title>
<style>body{{font:15px/1.6 system-ui;max-width:920px;margin:50px auto;padding:0 24px;color:#17231d}}
article{{border:1px solid #dfe5df;border-radius:16px;padding:18px;margin:14px 0}}small{{color:#657269}}
h1,h2{{letter-spacing:-.03em}}strong{{color:#176447}}</style></head><body>
<h1>{html.escape(session.base.filename)} → {html.escape(session.revised.filename)}</h1>
<p>{html.escape(session.report['alignment']['message'])}</p>{findings_html}</body></html>"""
    (target / "report.html").write_text(report_html, encoding="utf-8")
    if session.base_path.suffix.lower() == ".pdf":
        (target / "markup-pid-a.pdf").write_bytes(
            create_markup_pdf(session.base_path, "PID-A", session.report["findings"])
        )
    if session.revised_path.suffix.lower() == ".pdf":
        (target / "markup-pid-b.pdf").write_bytes(
            create_markup_pdf(session.revised_path, "PID-B", session.report["findings"])
        )


def read_upload(field: tuple[str, bytes] | None) -> tuple[str, bytes]:
    if field is None:
        raise ValueError("Both File A and File B are required.")
    original_name, content = field
    filename = safe_filename(original_name, "document.pdf")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(f"{filename}: unsupported format. Use native PDF, scanned PDF, or DWG.")
    if not content:
        raise ValueError(f"{filename}: file is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f"{filename}: file exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MB limit.")
    if extension == ".pdf" and not content.startswith(b"%PDF"):
        raise ValueError(f"{filename}: extension is PDF but the file signature is invalid.")
    if extension == ".dwg" and not content.startswith(b"AC"):
        raise ValueError(f"{filename}: extension is DWG but the file signature is invalid.")
    return filename, content


def parse_multipart(headers, stream) -> dict[str, tuple[str, bytes]]:
    length = int(headers.get("Content-Length", "0"))
    if length <= 0 or length > MAX_COMPARISON_BYTES:
        limit_mb = MAX_COMPARISON_BYTES // (1024 * 1024)
        raise ValueError(f"Upload body is empty or exceeds the {limit_mb} MB comparison limit.")
    body = stream.read(length)
    envelope = (
        f"Content-Type: {headers.get('Content-Type')}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + body
    )
    message = BytesParser(policy=email_policy).parsebytes(envelope)
    fields: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if name and filename:
            fields[name] = (filename, part.get_payload(decode=True) or b"")
    return fields


def build_session(base_upload: tuple[str, bytes], revised_upload: tuple[str, bytes]) -> tuple[ComparisonSession, dict]:
    session_id = f"CMP-{uuid.uuid4().hex[:10].upper()}"
    trace = TRACE_STORE.start("compare", session_id)
    folder = UPLOADS / session_id
    folder.mkdir(parents=True, exist_ok=True)
    base_name, base_bytes = base_upload
    revised_name, revised_bytes = revised_upload
    base_path = folder / f"A-{base_name}"
    revised_path = folder / f"B-{revised_name}"
    base_path.write_bytes(base_bytes)
    revised_path.write_bytes(revised_bytes)
    try:
        started = time.perf_counter()
        base_adapter = ROUTER.resolve(base_path)
        revised_adapter = ROUTER.resolve(revised_path)
        TRACE_STORE.span(
            trace,
            "format_detection",
            started,
            base_adapter=base_adapter.name,
            revised_adapter=revised_adapter.name,
        )

        started = time.perf_counter()
        base_document = base_adapter.ingest("PID-A", base_path)
        base_document.filename = base_name
        TRACE_STORE.span(
            trace,
            "ingest_pid_a",
            started,
            format=base_document.format,
            pages=len(base_document.pages),
            blocks=len(base_document.blocks),
        )

        started = time.perf_counter()
        revised_document = revised_adapter.ingest("PID-B", revised_path)
        revised_document.filename = revised_name
        TRACE_STORE.span(
            trace,
            "ingest_pid_b",
            started,
            format=revised_document.format,
            pages=len(revised_document.pages),
            blocks=len(revised_document.blocks),
        )

        started = time.perf_counter()
        delta = compare_documents(base_document, revised_document)
        TRACE_STORE.span(trace, "alignment_and_delta", started, **delta["counts"])

        report = {
            "comparison_id": session_id,
            "generated_at": time.time(),
            **delta,
        }
        session = ComparisonSession(
            session_id,
            time.time(),
            base_path,
            revised_path,
            base_document,
            revised_document,
            report,
        )
        started = time.perf_counter()
        write_report_files(session)
        TRACE_STORE.span(trace, "report", started, formats=["json", "markdown", "html", "markup_pdf"])
        trace["telemetry"] = {
            "model": "deterministic-delta-v2",
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0,
        }
        TRACE_STORE.finish(trace)
        with SESSION_LOCK:
            SESSIONS[session_id] = session
        return session, trace
    except Exception as exc:
        TRACE_STORE.finish(trace, "error", str(exc))
        raise


def attach_comparison_lineage(trace: dict, session_id: str):
    """Carry compare-stage timings into a causally linked chat trace."""

    comparison = next(
        (
            candidate
            for candidate in TRACE_STORE.list(session_id)
            if candidate.get("request") == "compare" and candidate.get("status") == "ok"
        ),
        None,
    )
    if not comparison:
        return
    trace["parent_trace_id"] = comparison["trace_id"]
    for span in comparison.get("spans", []):
        trace["spans"].append(
            {
                "name": span["name"],
                "duration_ms": span["duration_ms"],
                "attributes": {
                    **span.get("attributes", {}),
                    "linked_from_trace": comparison["trace_id"],
                    "reused": True,
                },
            }
        )


def restore_sessions(limit: int = 20) -> int:
    """Restore recent completed comparisons without re-running OCR or delta."""

    restored = 0
    states = sorted(
        REPORTS.glob("*/session.json"),
        key=lambda path: path.stat().st_mtime,
    )[-limit:]
    upload_root = UPLOADS.resolve()
    for state_path in states:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            base_path = (STORAGE_ROOT / state["base_path"]).resolve()
            revised_path = (STORAGE_ROOT / state["revised_path"]).resolve()
            base_path.relative_to(upload_root)
            revised_path.relative_to(upload_root)
            if not base_path.is_file() or not revised_path.is_file():
                continue
            session = ComparisonSession(
                id=state["id"],
                created_at=float(state["created_at"]),
                base_path=base_path,
                revised_path=revised_path,
                base=CanonicalDocument.from_dict(state["base"]),
                revised=CanonicalDocument.from_dict(state["revised"]),
                report=state["report"],
            )
            SESSIONS[session.id] = session
            restored += 1
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            continue
    return restored


class Handler(BaseHTTPRequestHandler):
    server_version = "DeltaScope/2.0"

    def request_id(self) -> str:
        if not hasattr(self, "_request_id"):
            self._request_id = str(uuid.uuid4())
        return self._request_id

    def log_message(self, message, *args):
        print(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "level": "info",
                    "event": "http_access",
                    "request_id": self.request_id(),
                    "method": self.command,
                    "path": self.path,
                    "message": message % args,
                }
            )
        )

    def send_bytes(self, content: bytes, content_type: str, status: int = 200, disposition: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Request-ID", self.request_id())
        self.send_header("X-Content-Type-Options", "nosniff")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data: dict, status: int = 200):
        self.send_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def send_api_error(self, message: str, status: int = 400):
        self.send_json({"error": message, "request_id": self.request_id()}, status)

    def get_session(self, session_id: str) -> ComparisonSession | None:
        with SESSION_LOCK:
            return SESSIONS.get(session_id)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/compare":
            return self.handle_compare()
        match = re.fullmatch(r"/api/sessions/([^/]+)/chat", path)
        if match:
            return self.handle_chat(match.group(1))
        return self.send_api_error("Not found.", 404)

    def handle_compare(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return self.send_api_error("Expected multipart file upload.")
        try:
            form = parse_multipart(self.headers, self.rfile)
            base = read_upload(form.get("file_a"))
            revised = read_upload(form.get("file_b"))
            session, trace = build_session(base, revised)
            self.send_json({**session.summary(), "trace_id": trace["trace_id"]}, 201)
        except ValueError as exc:
            self.send_api_error(str(exc), 422)
        except Exception as exc:
            self.send_api_error(f"Analysis failed: {exc}", 500)

    def handle_chat(self, session_id: str):
        session = self.get_session(session_id)
        if not session:
            return self.send_api_error("Comparison session not found.", 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            question = str(body.get("question", "")).strip()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return self.send_api_error("Invalid JSON body.")
        if not question:
            return self.send_api_error("Question is required.", 422)
        trace = TRACE_STORE.start("chat", session_id)
        attach_comparison_lineage(trace, session_id)
        try:
            started = time.perf_counter()
            result = answer_question(
                question,
                [session.base, session.revised],
                session.report,
                session_id=session_id,
            )
            TRACE_STORE.span(
                trace,
                "retrieval",
                started,
                hits=result["retrieval_hits"],
                sources=list({c["source"] for c in result["citations"]}),
                method=result.get("retrieval", {}).get("method"),
                top_scores=result.get("retrieval", {}).get("top_scores", [])[:5],
            )
            started = time.perf_counter()
            TRACE_STORE.span(
                trace,
                "llm",
                started,
                provider=result["provider"],
                fallback=bool(result.get("provider_error")),
                provider_error=result.get("provider_error"),
            )
            started = time.perf_counter()
            TRACE_STORE.span(trace, "answer", started, citations=len(result["citations"]), grounded=result["grounded"])
            trace["telemetry"] = {
                "model": result["provider"],
                "prompt": result["prompt"],
                "response": result["answer"],
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "estimated_cost_usd": result["estimated_cost_usd"],
                "response_id": result.get("response_id"),
                "finish_reason": result.get("finish_reason"),
                "provider_error": result.get("provider_error"),
            }
            TRACE_STORE.finish(trace)
            self.send_json({**result, "trace_id": trace["trace_id"]})
        except Exception as exc:
            TRACE_STORE.finish(trace, "error", str(exc))
            self.send_api_error(f"Chat failed: {exc}", 500)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            return self.serve_file(WEB / "index.html", "text/html; charset=utf-8")
        if path.startswith("/assets/"):
            candidate = (WEB / path.removeprefix("/")).resolve()
            if WEB.resolve() not in candidate.parents:
                return self.send_api_error("Not found.", 404)
            return self.serve_file(candidate)
        if path == "/api/health":
            return self.send_json(
                {
                    "status": "ok",
                    "runtime": "vercel-serverless" if IS_VERCEL else "local",
                    "storage": "ephemeral-/tmp" if IS_VERCEL else "local-disk",
                    "max_file_bytes": MAX_FILE_BYTES,
                    "max_comparison_bytes": MAX_COMPARISON_BYTES,
                    "formats": {
                        "native_pdf": "ready",
                        "scanned_pdf": (
                            "ready_remote_ocr"
                            if IS_VERCEL and os.getenv("OCR_SERVICE_URL")
                            else "ready_local_ocr"
                            if not IS_VERCEL
                            else "ocr_service_not_configured"
                        ),
                        "dwg": "ready" if ROUTER.dwg.converter_available() else "fallback_without_converter",
                    },
                    "answer_provider": os.getenv("ANSWER_PROVIDER", "local-extractive-v2"),
                }
            )
        if path == "/api/sessions/latest":
            with SESSION_LOCK:
                session = max(SESSIONS.values(), key=lambda item: item.created_at) if SESSIONS else None
            return self.send_json(session.summary()) if session else self.send_api_error("No comparison session found.", 404)
        match = re.fullmatch(r"/api/sessions/([^/]+)", path)
        if match:
            session = self.get_session(match.group(1))
            return self.send_json(session.summary()) if session else self.send_api_error("Session not found.", 404)
        match = re.fullmatch(r"/api/sessions/([^/]+)/documents/(PID-A|PID-B)", path)
        if match:
            session = self.get_session(match.group(1))
            if not session:
                return self.send_api_error("Session not found.", 404)
            source = session.base_path if match.group(2) == "PID-A" else session.revised_path
            media = "application/pdf" if source.suffix.lower() == ".pdf" else "application/acad"
            return self.send_bytes(source.read_bytes(), media, disposition=f'inline; filename="{source.name[2:]}"')
        match = re.fullmatch(r"/api/sessions/([^/]+)/documents/(PID-A|PID-B)/view\.svg", path)
        if match:
            session = self.get_session(match.group(1))
            if not session:
                return self.send_api_error("Session not found.", 404)
            pid = match.group(2)
            document = session.base if pid == "PID-A" else session.revised
            if document.format != "dwg" or not document.metadata.get("geometry_available"):
                return self.send_api_error("DWG geometry is unavailable for this document.", 422)
            query = parse_qs(parsed.query)
            block_id = (query.get("block_id") or [""])[0]
            try:
                page = int((query.get("page") or ["1"])[0])
            except ValueError:
                return self.send_api_error("Page must be an integer.", 422)
            if block_id and not any(block.id == block_id for block in document.blocks):
                return self.send_api_error("Citation block not found.", 404)
            return self.send_bytes(
                create_dwg_svg(document, block_id or None, page),
                "image/svg+xml; charset=utf-8",
                disposition=f'inline; filename="{pid.lower()}-drawing.svg"',
            )
        match = re.fullmatch(r"/api/sessions/([^/]+)/documents/(PID-A|PID-B)/highlight", path)
        if match:
            session = self.get_session(match.group(1))
            if not session:
                return self.send_api_error("Session not found.", 404)
            pid = match.group(2)
            source = session.base_path if pid == "PID-A" else session.revised_path
            document = session.base if pid == "PID-A" else session.revised
            if source.suffix.lower() != ".pdf":
                return self.send_api_error("Visual highlighting is available for PDF sources.", 422)
            block_id = (parse_qs(parsed.query).get("block_id") or [""])[0]
            block = next((item for item in document.blocks if item.id == block_id), None)
            if not block:
                return self.send_api_error("Citation block not found.", 404)
            content = create_highlight_pdf(source, block)
            return self.send_bytes(
                content,
                "application/pdf",
                disposition=f'inline; filename="highlight-{pid.lower()}.pdf"',
            )
        match = re.fullmatch(r"/api/sessions/([^/]+)/report\.(json|md|html)", path)
        if match:
            session_id, extension = match.groups()
            target = REPORTS / session_id / f"report.{extension}"
            media = {"json": "application/json", "md": "text/markdown; charset=utf-8", "html": "text/html; charset=utf-8"}[extension]
            return self.serve_file(target, media)
        match = re.fullmatch(r"/api/sessions/([^/]+)/markup/(PID-A|PID-B)\.pdf", path)
        if match:
            session_id, pid = match.groups()
            target = REPORTS / session_id / ("markup-pid-a.pdf" if pid == "PID-A" else "markup-pid-b.pdf")
            return self.serve_file(target, "application/pdf")
        match = re.fullmatch(r"/api/sessions/([^/]+)/traces", path)
        if match:
            traces = TRACE_STORE.list(match.group(1))
            return self.send_json({"traces": traces})
        match = re.fullmatch(r"/api/sessions/([^/]+)/metrics", path)
        if match:
            traces = TRACE_STORE.list(match.group(1))
            durations = [trace.get("duration_ms", 0) for trace in traces]
            return self.send_json(
                {
                    "requests": len(traces),
                    "avg_latency_ms": round(sum(durations) / len(durations), 2) if durations else 0,
                    "errors": sum(trace.get("status") == "error" for trace in traces),
                    "llm_calls": sum(trace.get("request") == "chat" and trace.get("status") == "ok" for trace in traces),
                    "retrieval_hits": sum(
                        span.get("attributes", {}).get("hits", 0)
                        for trace in traces
                        for span in trace.get("spans", [])
                        if span.get("name") == "retrieval"
                    ),
                    "input_tokens": sum(trace.get("telemetry", {}).get("input_tokens", 0) for trace in traces),
                    "output_tokens": sum(trace.get("telemetry", {}).get("output_tokens", 0) for trace in traces),
                    "estimated_cost_usd": sum(trace.get("telemetry", {}).get("estimated_cost_usd", 0) for trace in traces),
                    "models": sorted(
                        {
                            trace.get("telemetry", {}).get("model")
                            for trace in traces
                            if trace.get("telemetry", {}).get("model")
                        }
                    ),
                    "delta_counts": self.get_session(match.group(1)).report["counts"] if self.get_session(match.group(1)) else {},
                }
            )
        return self.send_api_error("Not found.", 404)

    def serve_file(self, path: Path, content_type: str | None = None):
        if not path.exists() or not path.is_file():
            return self.send_api_error("Not found.", 404)
        media = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_bytes(path.read_bytes(), media)


def preload_demo_session() -> ComparisonSession:
    base_path = Path(
        os.getenv(
            "DELTASCOPE_DEMO_BASE",
            SOURCE_ROOT / "data" / "samples" / "lift-gas-compressor.pdf",
        )
    )
    revised_path = Path(
        os.getenv(
            "DELTASCOPE_DEMO_REVISED",
            SOURCE_ROOT / "data" / "samples" / "export-gas-compressor.pdf",
        )
    )
    missing = [str(path) for path in (base_path, revised_path) if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Demo input not found: {', '.join(missing)}")
    base_upload = read_upload((base_path.name, base_path.read_bytes()))
    revised_upload = read_upload((revised_path.name, revised_path.read_bytes()))
    session, _ = build_session(base_upload, revised_upload)
    return session


def run(demo: bool = False):
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    restored = restore_sessions()
    if demo:
        session = preload_demo_session()
        print(
            f"Demo comparison {session.id} ready: "
            f"{session.base.filename} -> {session.revised.filename}"
        )
    elif restored:
        print(f"Restored {restored} comparison session(s)")
    print(f"DeltaScope ready at http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DeltaScope document delta and grounded chat")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="ingest the bundled compressor P&IDs, generate reports, and serve a chat-ready workspace",
    )
    run(demo=parser.parse_args().demo)
