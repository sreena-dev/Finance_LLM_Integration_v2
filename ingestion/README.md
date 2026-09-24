# Ingestion service

Turns an uploaded financial-statement PDF into verified tables. Runs as its own
container (`docker compose up -d ingest`, port 12102) because it carries
docling, torch and an OCR engine, and because a conversion saturates a CPU for
minutes — neither belongs in the read-only gateway that also serves chat.

Only the Financial Statement mode talks to it, over `ARTHA_INGEST_URL`. It has
no authentication of its own and must not be reachable from a browser.

```
POST /ingest                  multipart PDF -> { job_id }
GET  /ingest/{job}            poll one job
GET  /ingest/{job}/events     SSE progress, ending with the full result
GET  /health                  readiness, per component
```

## The pipeline

```
render -> precheck -> preprocess -> layout + OCR -> structure + second read
       -> reconcile / validate / export -> identify
```

Each stage is one module; `pipeline.py` is the only thing that knows the order.

**Preprocessing is the stage docling does not have.** Docling performs no
deskew, no resolution normalisation and no contrast correction, and will not
tell you when they were needed. Every financial statement in `data/` is a
200 DPI scan with no text layer, and `MH 2022-23 SFS` page 3 is skewed by 1.7°
— enough to displace a row by more than a text line's height, which makes a
table extractor bind a label to the wrong numbers and report no error.
(`render.py`, `precheck.py`, `preprocess.py`.)

**Where the digits come from: OCR, and only OCR.**

| Stage | Module | What it does |
|---|---|---|
| layout | `layout.py` | Docling reads the *text* (headings, paragraphs, lists) and *locates* tables. TableFormer is off; docling never reads table cells. |
| OCR | `ocr.py` | RapidOCR reads every word inside each table region, with a box. Words are regrouped into cell-sized tokens. |
| structure | `structure.py` | Geometry proposes rows and columns from the token positions. Gemma classifies them (column roles, row kinds, wrapped labels) and returns only ids and enums, never a number. The reply is validated against the page; a reply that fails is retried once, then the geometry-only grid is used and the table is marked unconfirmed. |
| second read | `second_read.py` | Gemma reads each cropped row independently and lists the amounts it sees. |
| reconcile | `reconcile.py` | Compares the two readers per cell: `verified_dual_read`, `ocr_only`, `flagged`, `unreadable`, `handwritten`, `struck`. A doubtful figure is **flagged, never fixed** — OCR's text is kept verbatim and the second reader's is kept beside it. |
| validate | `validate.py`, `rules.yaml` | Footing, Balance Sheet identity, and note-total-vs-statement-line checks. They report failures and change nothing. |
| export | `export.py` | Builds the records the gateway and UI already consume (`table_md` with `[unreadable: ...]` / `[recovered ...]` markers, `CellFinding`s addressed by row/column index). |

`llm_client.py` is the one Gemma client: structure calls and second-read calls
have separate concurrency caps (a burst of long structure calls must not queue
every short row read behind it), and every request and response is logged to
`out/<doc_id>/llm_logs/`.

**Pages run concurrently.** After preprocessing, each page flows through the
stages as soon as the one before is done with it, so the wait on the model for
page 1's tables overlaps with docling working through page 2. Layout stays
single-file (two docling conversions at once run out of memory); OCR and model
calls run on their own pools. Output order never depends on completion order.

## Running the tests

```bash
ingestion/venv/Scripts/python -m pytest ingestion/tests -q
```

These need none of the heavy dependencies: the model endpoint, docling and
RapidOCR are faked, and the tests cover number parsing, structure validation,
reconciliation, the validation rules, the export contract and pipeline
concurrency. The accuracy harness (`tests/accuracy/`, real filings) needs the
deployed venv: `python -m tests.accuracy.runner --mode inproc score-all`.

## Installing

```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu
```

Three things that will waste an afternoon if you do not know them:

1. **`docling-slim`, not `docling`.** On PyPI `docling` is a meta-package whose
   only dependency is `docling-slim[standard]`; it declares no extras of its
   own. `pip install "docling[format-pdf,models-local]"` succeeds, prints
   `WARNING: docling 2.123.1 does not provide the extra 'format-pdf'`, and
   quietly gives you `standard` instead. The importable `docling` module comes
   from docling-slim either way.

2. **`onnxruntime` is not pulled in by `feat-ocr-rapidocr`.** That extra
   installs the `rapidocr` wrapper and nothing to run it on. Without
   onnxruntime docling builds its pipeline happily and dies on the first page
   with `ImportError: onnxruntime is not installed` — at the first upload, not
   at startup. It is listed explicitly in `requirements.txt`.

3. **Windows: torch will not install under a long path.** torch ships license
   files nested ~12 directories deep, and under a `OneDrive/Desktop/...` project
   path the install fails with
   `OSError: [WinError 206] The filename or extension is too long`. Either
   enable Win32 long paths, or create the venv somewhere short (`C:\ingv`) for
   local docling work. The container is unaffected.

The Dockerfile bakes the layout, TableFormer and RapidOCR weights into the image
and points `INGEST_DOCLING_ARTIFACTS` at them, so nothing reaches Hugging Face
at request time — a request-time download is a multi-minute stall on an
otherwise healthy upload, and an outright failure air-gapped.

## Configuration

Everything is `INGEST_*` prefixed except the VLM endpoint, which deliberately
reuses the gateway's own `LLM_BASE_URL` / `LLM_MODEL_NAME` rather than giving
the same server a second name to drift from. See `app/config.py`; the
deployment-facing subset is documented in `.env.example`.

The vision model is optional. Without it table structure is worked out from
positions alone and figures are read by OCR alone, but each one then rests on
a single reader — and the quality report says so rather than leaving
the reader to assume otherwise.
