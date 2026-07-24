# DeltaScope

DeltaScope is an upload-first platform for comparing engineering document revisions and asking grounded questions about the result.

The UI intentionally has no authentication. The assignment does not ask for accounts, and adding them would distract from ingestion, alignment, evaluation, and grounding.

## Run

```powershell
uv sync
uv run python app.py
```

Open `http://127.0.0.1:8000`.

For the rubric's single-command reproducible run, use:

```powershell
make demo
```

This ingests the bundled compressor pair, generates JSON, Markdown, HTML, and
redline PDF reports, and serves the chat-ready workspace at
`http://127.0.0.1:8000`. The equivalent command is
`uv run python app.py --demo`.
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
| DWG | End-to-end with LibreDWG | Real layers, layouts, entities, blocks, dimensions, coordinates, and SVG citation view |

Run `make dwg-setup` once to install the official GNU LibreDWG Windows converter
into the ignored `.tools/` directory. DWG uploads then pass through `dwg2dxf`
and ezdxf, preserving layout, layer, entity type, block insertion, dimension
measurement, and drawing coordinates. The workspace renders a local SVG
drawing; clicking a finding or chat citation highlights its exact entity bounds.
If the converter is not present, the adapter degrades explicitly to conservative
recoverable-string indexing and reports that limitation in metadata and the UI.

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

Candidate blocking avoids comparing every block with every other block, and
RapidFuzz performs the remaining similarity calculations. On the supplied
compressor pair this reduced the measured delta stage from roughly 2.04 seconds
to 0.10 seconds while preserving the labeled evaluation score.

## Grounded chat

Retrieval spans:

- every canonical block from PID-A;
- every canonical block from PID-B;
- every structured delta finding.

Evidence is ranked with deterministic Okapi BM25. The local provider returns
extractive statements with page/block/region citations. Unsupported questions
produce a refusal instead of an invented answer. `src/chat/providers.py`
implements Fireworks AI and generic OpenAI-compatible LLM clients. Hosted output
is accepted only when each factual line includes a retrieved citation ID;
otherwise the system uses the deterministic fallback and records the provider
failure.

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

Completed canonical documents, reports, and source paths are persisted under the
ignored artifacts directory. The server restores the 20 most recent sessions on
startup without repeating OCR or LLM calls.

## Evaluation

Generate samples and print the scorecard:

```powershell
uv run python scripts/generate_eval_samples.py
uv run python -m eval.run_eval
```

Or:

```bash
make eval
```

The labeled dataset includes three independent pairs:

- native PDF equipment revision;
- scanned PDF set-point revision;
- native PDF note revision.

The fixture generator uses built-in PDF fonts and deterministic document output,
so repeated `make eval` runs do not dirty the repository.

The scorecard reports delta precision/recall/F1, answer correctness,
evidence-backed groundedness/citation accuracy, BM25 recall@k and mean
reciprocal rank, per-pair formats, and known failures. Citation accuracy requires
the cited excerpt to contain the labeled supporting evidence; checking only for
the presence of a citation ID is intentionally insufficient. The latest result
is written to `artifacts/eval-scorecard.json`.

Run the independent real-DWG regression:

```powershell
make dwg-eval
```

The committed pair contains actual AC1015 DWG files with text, a pump, piping,
a control-valve block, a dimension, and an added PSV. The scorecard verifies
geometry extraction, layers, six labeled changes, BM25 retrieval, grounded chat,
and citations. It is written to `artifacts/dwg-eval-scorecard.json`.

Run unit tests:

```powershell
uv run python -m unittest discover -s tests -v
```

## Open-source research and design choices

- [Docling](https://github.com/docling-project/docling) validates the value of a
  unified document representation and local OCR. DeltaScope keeps a smaller
  P&ID-specific model because exact page regions and a lightweight install matter
  more here than broad office-format support.
- [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) provides the optimized
  fuzzy similarity primitive used after deterministic candidate blocking.
- [BM25S](https://github.com/xhluca/bm25s) informed the BM25 retrieval baseline.
  This corpus is only hundreds of blocks, so DeltaScope uses a small transparent
  implementation rather than adding SciPy-backed indexing infrastructure.
- [RapidOCR](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/usage/)
  supports offline scanned-PDF extraction, while
  [PyMuPDF](https://pymupdf.readthedocs.io/) supplies native text coordinates and
  PDF annotations.
- [OpenTelemetry's tracing model](https://opentelemetry.io/docs/languages/python/instrumentation/)
  informed the trace/span structure. A dependency-light JSONL exporter is used
  for the take-home, with trace IDs and parent linkage kept compatible with a
  future OTLP exporter.
- [GNU LibreDWG](https://www.gnu.org/software/libredwg/manual/LibreDWG.pdf)
  provides the open-source `dwg2dxf` conversion stage. `make dwg-setup` downloads
  the official release asset rather than committing platform binaries.
- [ezdxf](https://ezdxf.readthedocs.io/en/stable/tutorials/getting_data.html)
  supplies audited DXF recovery, layout iteration, entity queries, and bounding
  boxes after conversion.

## Requirement matrix

| Assignment requirement | Implementation |
|---|---|
| At least two formats | Native PDF, scanned PDF, and DWG work end-to-end |
| One adapter interface | `FormatAdapter` and `AdapterRouter` |
| Canonical representation | Located page/block model shared by all adapters |
| Structured delta | Added, removed, modified, typed, located, described, confidence |
| Human-readable report | Workspace plus exported HTML/Markdown |
| Machine-readable report | Exported JSON |
| Grounded chat | PID-A, PID-B, and Delta retrieval |
| Retrieval quality | BM25 plus labeled recall@k and mean reciprocal rank |
| Citations | Source PID, page, block ID, region, and excerpt |
| One command | `make demo` ingests the bundled pair, writes reports, and serves chat |
| Tracing and JSON logs | `artifacts/traces.jsonl` and structured access logs |
| Token and cost telemetry | Full prompt/response/provider/tokens/cost per chat |
| Metrics | Session latency, errors, LLM calls, retrieval hits, tokens, cost, and delta counts |
| Runnable eval | `uv run python -m eval.run_eval` or `make eval` |
| Failure reporting | README, scorecard, adapter warnings |
| Samples | Three PDF pairs plus a real-DWG geometry pair with provenance in code |
| Secrets | ignored local `.env`, placeholder-only `.env.example`, and deterministic no-key fallback |
| Walkthrough | `DEMO.md` |

## Honest limitations

- Common 2D DWG entity and symbol-bound changes are detected, but semantic
  connectivity/topology and proprietary proxy objects are not reconstructed.
- OCR is slower and its bounding boxes are approximate.
- Very noisy scans can split or confuse engineering labels.
- DWG 3D solids, advanced hatches, and unsupported/proxy entities may be reduced
  to their type and bounding region; LibreDWG audit issues remain visible.
- Full DWG geometry requires the open-source LibreDWG converter; without it the
  application deliberately uses the lower-confidence string fallback.
- Session persistence is local-disk/single-instance; horizontal deployment would move state to object storage plus a database.
- The local answer provider is grounded and deterministic but less fluent than a generative LLM.

## What I would do next

1. Register revision layouts visually before geometry/symbol comparison.
2. Add engineering-symbol detection and graph matching for P&ID connectivity.
3. Expand the renderer for 3D solids, hatches, and proprietary proxy objects.
4. Move persisted sessions to object storage plus Postgres for horizontal scale.
5. Add a calibrated hybrid dense/sparse retriever after expanding the labeled retrieval set.
