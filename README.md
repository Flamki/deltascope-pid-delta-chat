# DeltaScope

DeltaScope is an upload-first platform for comparing engineering document revisions and asking grounded questions about the result.

The UI intentionally has no authentication. The assignment does not ask for accounts, and adding them would distract from ingestion, alignment, evaluation, and grounding.

## Run

```powershell
uv sync
python app.py
```

Open `http://127.0.0.1:8000`.

For the rubric's single-command reproducible run, use:

```powershell
make demo
```

This ingests the bundled compressor pair, generates JSON, Markdown, HTML, and
redline PDF reports, and serves the chat-ready workspace at
`http://127.0.0.1:8000`. The equivalent command is `python app.py --demo`.
`DELTASCOPE_DEMO_BASE` and `DELTASCOPE_DEMO_REVISED` can point the same command
at another pair.

Upload File A (base) and File B (revised), then choose **Compare documents**. The application creates a comparison workspace with:

- grounded chat on the left;
- File A, File B, and Delta tabs on the right;
- clickable page/block citations;
- citation clicks that open the exact highlighted bounding region;
- JSON, Markdown, and HTML report exports;
- redline PDF exports for File A and File B;
- an observability panel with traces, latency, token counts, cost, and failures.

No model key is required for the deterministic fallback. The production chat path supports Fireworks AI and a generic OpenAI-compatible provider without changing retrieval or citation validation.

## Supported inputs

| Format | Status | Adapter |
|---|---|---|
| Native PDF | End-to-end | PyMuPDF text blocks and exact bounding boxes |
| Scanned PDF | End-to-end | 2x page rendering plus local RapidOCR/ONNX |
| DWG | Accepted and indexed | Header, hash, metadata, and conservative recoverable-string extraction |

DWG uploads work through the same canonical seam and can be compared and searched. Precise DWG entities, layers, blocks, and geometry need a LibreDWG or ODA converter. This limitation is surfaced in the document metadata and UI rather than hidden. The assignment requires two formats end-to-end; native and scanned PDF meet that core requirement. Full DWG entity support is the documented third-format extension.

## Architecture

```text
Upload File A/B
      |
      v
AdapterRouter
  |-- NativePdfAdapter
  |-- ScannedPdfAdapter (OCR)
  `-- DwgAdapter
      |
      v
CanonicalDocument
  pages -> blocks -> text + kind + confidence + bounding region
      |
      +--> deterministic alignment and structured delta
      |       added / removed / modified + severity + citations
      |
      +--> retrieval index: PID-A + PID-B + delta report
              |
              v
        local grounded answer provider
        answer + page/block/region citations
```

The public Pathnovo documentation influenced the product lifecycle: upload, automatic classification/adapter routing, typed extraction, inspectable progress, and structured JSON. DeltaScope adds revision alignment and grounded delta chat on top of that lifecycle.

## Canonical model

All adapters return the same types from `src/canonical/model.py`:

- `CanonicalDocument`: PID, source format, adapter, metadata, warnings;
- `CanonicalPage`: page dimensions and ordered blocks;
- `CanonicalBlock`: text, semantic kind, confidence, page, and bounding region.

The delta and chat layers never branch on PDF versus DWG.

## Delta engine

The deterministic engine:

1. normalizes text without destroying source text;
2. aligns exact blocks first;
3. greedily aligns remaining blocks using sequence and token similarity;
4. classifies unmatched content as added or removed;
5. classifies aligned-but-different content as modified;
6. assigns item type, severity, location, and confidence.

The engine deliberately does not use an LLM. Structural results are reproducible, cheap, and regression-testable.
`DELTA_MIN_TEXT_LENGTH`, `DELTA_SIMILARITY_THRESHOLD`, and
`DELTA_ALIGNMENT_THRESHOLD` make the main matching decisions configurable
without changing code. Every JSON report records the effective values.

## Grounded chat

Retrieval spans:

- every canonical block from PID-A;
- every canonical block from PID-B;
- every structured delta finding.

The local provider returns extractive statements with page/block/region citations. Unsupported questions produce a refusal instead of an invented answer. `src/chat/providers.py` implements Fireworks AI and generic OpenAI-compatible LLM clients. Hosted output is accepted only when each factual line includes a retrieved citation ID; otherwise the system uses the deterministic fallback and records the provider failure.

To enable Fireworks, create an ignored `.env`:

```text
ANSWER_PROVIDER=fireworks
FIREWORKS_API_KEY=your_rotated_key
FIREWORKS_MODEL=accounts/fireworks/models/gpt-oss-20b
FIREWORKS_INPUT_COST_PER_MILLION=0.07
FIREWORKS_OUTPUT_COST_PER_MILLION=0.30
```

The integration uses Fireworks' official OpenAI-compatible Chat Completions endpoint, `https://api.fireworks.ai/inference/v1/chat/completions`, and records the returned token usage. See the [Fireworks Chat Completions reference](https://docs.fireworks.ai/api-reference/post-chatcompletions) and [GPT-OSS 20B model page](https://fireworks.ai/models/fireworks/gpt-oss-20b).

For another OpenAI-compatible endpoint:

```text
ANSWER_PROVIDER=openai-compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=...
LLM_MODEL=...
```

Credentials are read only from environment variables.

## Observability

`artifacts/traces.jsonl` stores structured traces keyed by comparison session:

- format detection;
- PID-A and PID-B ingestion;
- alignment and delta;
- report generation;
- retrieval;
- answer-provider call;
- grounded answer assembly.

Each chat trace links its originating compare trace and carries forward the
format-detection, ingestion, delta, and report timings. One inspectable trace
therefore shows ingest → delta → retrieval → LLM → answer. Chat telemetry
records the prompt, response, provider name, input/output token estimates,
citations, latency, and cost. Errors are retained with the same request and
session identifiers.

## Evaluation

Generate samples and print the scorecard:

```powershell
python scripts/generate_eval_samples.py
python -m eval.run_eval
```

Or:

```bash
make eval
```

The labeled dataset includes three independent pairs:

- native PDF equipment revision;
- scanned PDF set-point revision;
- native PDF note revision.

The scorecard reports delta precision/recall/F1, answer correctness, citation accuracy, per-pair formats, and known failures. The latest result is written to `artifacts/eval-scorecard.json`.

Run unit tests:

```powershell
python -m unittest discover -s tests -v
```

## Requirement matrix

| Assignment requirement | Implementation |
|---|---|
| At least two formats | Native PDF and scanned PDF work end-to-end |
| One adapter interface | `FormatAdapter` and `AdapterRouter` |
| Canonical representation | Located page/block model shared by all adapters |
| Structured delta | Added, removed, modified, typed, located, described, confidence |
| Human-readable report | Workspace plus exported HTML/Markdown |
| Machine-readable report | Exported JSON |
| Grounded chat | PID-A, PID-B, and Delta retrieval |
| Citations | Source PID, page, block ID, region, and excerpt |
| One command | `make demo` ingests the bundled pair, writes reports, and serves chat |
| Tracing and JSON logs | `artifacts/traces.jsonl` and structured access logs |
| Token and cost telemetry | Full prompt/response/provider/tokens/cost per chat |
| Metrics | Session latency, errors, LLM calls, retrieval hits, tokens, cost, and delta counts |
| Runnable eval | `python -m eval.run_eval` or `make eval` |
| Failure reporting | README, scorecard, adapter warnings |
| Samples | Three generated pairs with provenance in code |
| Secrets | ignored local `.env`, placeholder-only `.env.example`, and deterministic no-key fallback |
| Walkthrough | `DEMO.md` |

## Honest limitations

- Geometry-only changes and symbol topology are not detected.
- OCR is slower and its bounding boxes are approximate.
- Very noisy scans can split or confuse engineering labels.
- Full DWG entity/geometry extraction needs a configured converter.
- In-memory comparison sessions do not survive a process restart; source uploads, exported reports, and traces do.
- The local answer provider is grounded and deterministic but less fluent than a generative LLM.

## What I would do next

1. Add LibreDWG conversion and parse layers, blocks, dimensions, and entity coordinates.
2. Register pages visually and generate a redline/markup PDF.
3. Add engineering-symbol detection and graph matching for P&IDs.
4. Persist canonical documents in SQLite/Postgres and indexes in a vector store.
5. Add an optional generative answer provider while retaining citation validation and deterministic fallback.
