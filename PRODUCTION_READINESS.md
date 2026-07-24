# Production readiness

DeltaScope is a serious, runnable evaluation system. It is not yet an
enterprise production system for safety-critical engineering decisions.

## What is real today

- Native-PDF, scanned-PDF/OCR, and DWG adapters share one canonical document
  model with page or drawing coordinates.
- Alignment is deterministic and produces typed added, removed, and modified
  findings with source locations, confidence, and severity.
- Retrieval indexes both source revisions and the delta report. Answers either
  carry validated source citations or fall back to a grounded extractive
  response.
- Citation clicks open the exact source region, while report exports include
  JSON, Markdown, HTML, and visual redlines.
- Structured request traces expose ingestion, alignment, delta, retrieval,
  provider, and answer latency plus token and cost telemetry.
- Private Vercel Blob storage durably retains uploaded sources, canonical
  session state, and per-request traces across cold starts and serverless
  instances. Local runs retain the filesystem implementation.
- The labeled evaluation gate covers native PDF, scanned PDF/OCR, moved layout,
  grounded chat, citation evidence, retrieval, and real DWG geometry.

## Why this is not yet enterprise-ready

- P&ID connectivity is not reconstructed as an equipment, piping, instrument,
  and relationship graph. Text and entity changes are useful evidence, but they
  cannot prove process-topology equivalence.
- The labeled corpus is intentionally small and mostly deterministic. It does
  not establish performance across different EPCs, owner/operator standards,
  scan quality, symbol libraries, languages, or revision conventions.
- The hosted demonstration uses request-size limits and private durable object
  storage, but comparisons still run synchronously. Enterprise operation still
  requires resumable asynchronous jobs, retention/deletion controls,
  authenticated ownership, a transactional review database, and an audit
  policy.
- Confidence values are heuristic rather than calibrated on an independently
  labeled production corpus.
- Human review remains mandatory for low-alignment comparisons and
  safety-critical findings.

## Research-backed upgrade path

### 1. Durable execution

Keep the implemented private object storage for originals, canonical blocks,
and traces; add Postgres for comparison, job, retention, and review state.
Process pages through a durable queue with idempotent content hashes. Export
traces through the OpenTelemetry SDK and an OTLP collector instead of relying
on JSONL/private objects.

Reference: [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/).

### 2. Stronger document understanding

Keep the current adapter seam, but add an optional layout/OCR backend based on
Docling or PP-StructureV3. Both expose structured blocks and coordinates;
PP-StructureV3 also exposes orientation correction, unwarping, reading order,
and service-oriented deployment. LayoutLMv3 is a relevant multimodal baseline
for learning text, image, and 2D layout jointly.

References:

- [Docling](https://github.com/docling-project/docling)
- [PP-StructureV3](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-StructureV3/PP-StructureV3.html)
- [LayoutLMv3](https://arxiv.org/abs/2204.08387)

### 3. P&ID graph extraction

Add symbol detection, line and junction extraction, tag-to-symbol association,
and graph reconstruction. PID2Graph reports that a relation-aware transformer
outperformed a modular baseline by more than 25% on real-world edge detection,
which is directly relevant to topology-aware revision comparison. Normalize
recognized equipment, piping, and instrumentation toward the DEXPI information
model so changes have engineering semantics rather than only visual labels.

References:

- [PID2Graph / Relationformer P&ID research](https://arxiv.org/abs/2411.13929)
- [Digitize-PID](https://arxiv.org/abs/2109.03794)
- [DEXPI P&ID Specification 1.4](https://dexpi.org/wp-content/uploads/2024/12/DEXPI_PID_Specification_1.4.pdf)

### 4. Production evaluation

Build owner/operator holdouts with double-reviewed labels for symbols,
connections, tags, attributes, moves, and modifications. Report metrics by
document source and failure slice, calibrate confidence, test abstention, and
gate releases on both quality and latency/cost budgets. Retain the current
candid failure table and add reviewer disagreement plus post-deployment drift
monitoring.

## Product boundary

Until those stages are complete, DeltaScope should be positioned as a
review-assistance system that finds and grounds candidate changes. It should not
be positioned as an autonomous source of record or a replacement for an
engineer's approval.
