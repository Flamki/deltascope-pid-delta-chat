# DeltaScope walkthrough

## 1. Start

```powershell
make demo
```

Open `http://127.0.0.1:8000`.

## 2. Review the real pair

`make demo` has already ingested the supplied compressor P&IDs and generated
their reports:

- `data/samples/lift-gas-compressor.pdf`
- `data/samples/export-gas-compressor.pdf`

To demonstrate arbitrary uploads instead, select **New**, drag an older revision
into **File A** and a newer revision into **File B**, then select
**Compare documents**. Native PDF, scanned PDF, and DWG uploads are accepted.

## 3. Inspect the result

The workspace opens with grounded chat on the left and source review on the right.

1. Select **Delta** and filter added, modified, or removed findings.
2. Select a finding to open the cited source page with its exact region highlighted.
3. Switch between **File A** and **File B**.
4. Export the report as HTML, Markdown, JSON, or redline PDFs for both files.

## 4. Ask grounded questions

Try:

- “Summarize what changed.”
- “What are the most critical changes?”
- “What appears only in File B?”
- “Which pressure values changed?”

Each result contains clickable citations to a PID, page, and block.

## 5. Inspect evidence

Open the chart icon in the top-right. The observability panel shows:

- compare and chat traces;
- per-stage pipeline names and latency;
- token estimates and cost;
- request status and failure visibility.

## 6. Run evaluation

```powershell
make eval
```

The command prints the regression scorecard and writes
`artifacts/eval-scorecard.json`. The verified baseline is:

- delta precision / recall / F1: `1.0 / 1.0 / 1.0`;
- chat answer correctness: `1.0`;
- citation accuracy: `1.0`;
- three labeled document pairs, including a scanned-PDF/OCR pair.
