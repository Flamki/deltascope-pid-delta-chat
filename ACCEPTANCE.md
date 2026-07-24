# Assignment acceptance audit

This file maps every candidate-facing requirement to concrete implementation
evidence. Bonus items are listed separately.

## Core capabilities

| Requirement | Status | Evidence |
|---|---|---|
| Resolve two PIDs to bytes and metadata | Pass | Uploads become `PID-A` and `PID-B`; file metadata is returned by the session API |
| One format-adapter interface | Pass | `src/ingest/base.py`, `src/ingest/router.py` |
| At least two formats end-to-end | Pass | Native PDF and scanned PDF/OCR adapters; both are exercised by `eval/run_eval.py` |
| Third-format seam | Pass | DWG adapter accepts, fingerprints, indexes recoverable strings, and reports the missing geometry converter |
| Canonical representation | Pass | `src/canonical/model.py`: document, page, block, region, kind, and confidence |
| Deterministic structural delta | Pass | `src/delta/engine.py` exact alignment, similarity alignment, additions, removals, and modifications |
| Typed and located changes | Pass | Every finding includes item type, page, source block, bounding region, description, severity, and confidence |
| Human-readable report | Pass | Workspace Delta view plus exported HTML and Markdown |
| Machine-readable report | Pass | Exported JSON |
| Grounded chat over both PIDs and delta | Pass | `src/chat/answer.py` retrieves canonical blocks from both documents and report findings |
| Source citations | Pass | PID/report source, page, block/finding ID, region, excerpt, and clickable visual highlight |
| Unsupported-question handling | Pass | Grounded refusal when retrieval finds no evidence |
| Swappable LLM | Pass | Fireworks, generic OpenAI-compatible, and deterministic fallback providers |
| One documented run command | Pass | `make demo` ingests the bundled pair, writes all reports, and serves the chat-ready workspace |

## Observability

| Requirement | Status | Evidence |
|---|---|---|
| Ingest and delta traces | Pass | format detection, PID-A ingest, PID-B ingest, alignment/delta, and report spans |
| Retrieval, LLM, and answer traces | Pass | each chat trace links the compare trace and exposes the full ingest → delta → retrieval → LLM → answer chain |
| Prompt and response capture | Pass | Full system/user prompt and final response in chat trace telemetry |
| Model, token, and cost capture | Pass | Provider/model, input/output tokens, response ID, finish reason, and estimated cost |
| Structured logs and correlation ID | Pass | JSON access logs with request ID; trace JSONL with trace and comparison session IDs |
| Inspectable metrics | Pass | Session metrics API and observability dialog |
| Failure visibility | Pass | Provider/OCR/ingestion errors are retained in traces; hosted-model failure safely falls back |

## Evaluation

| Requirement | Status | Evidence |
|---|---|---|
| Labeled document pairs | Pass | `eval/datasets/ground_truth.json` |
| At least 2-3 sample pairs | Pass | Three generated pairs under `data/eval/` |
| Sample provenance | Pass | `data/PROVENANCE.md` and deterministic generator |
| Delta precision/recall/F1 | Pass | `eval/run_eval.py` |
| Chat correctness | Pass | Expected-answer token assertions |
| Groundedness/citation accuracy | Pass | Cited excerpts must contain the labeled supporting evidence |
| Retrieval quality | Pass | Labeled BM25 recall@k and mean reciprocal rank |
| Runnable scorecard | Pass | `uv run python -m eval.run_eval` or `make eval` |
| Regression-friendly output | Pass | Stable JSON scorecard at `artifacts/eval-scorecard.json` |
| Honest failure reporting | Pass | Scorecard, README limitations, and adapter warnings |

## Engineering and submission

| Requirement | Status | Evidence |
|---|---|---|
| Config over hardcoding | Pass | Environment-selected provider/model/costs, delta thresholds, demo paths, and trace path |
| No committed secrets | Pass | `.env` and `.env.*` ignored; `.env.example` contains placeholders |
| Reproducible dependency set | Pass | Pinned project constraints plus committed `uv.lock` |
| Git submission | Pass | Initialized `main` repository with a clean tracked-secret scan |
| Tests | Pass | `uv run python -m unittest discover -s tests -v` |
| README trade-offs and next steps | Pass | `README.md` |
| Walkthrough | Pass | `DEMO.md` |

## Bonus status

- Served upload-first UI and observability dashboard: implemented.
- All three uploads accepted: implemented; DWG entity geometry remains limited.
- Retrieval-quality evaluation: implemented with labeled recall@k and mean reciprocal rank.
- Cost and latency accounting: implemented.
- Visual redline/markup PDF: implemented for PDF sources with added, removed, and modified color overlays.
