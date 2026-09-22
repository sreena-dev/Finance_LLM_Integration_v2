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
render -> precheck -> preprocess -> convert -> vlm second read -> verify -> identify -> emit
```

Each stage is one module; `pipeline.py` is the only thing that knows the order.
Two of them are the reason this service exists at all:

**`preprocess.py` — the stage docling does not have.** Docling performs no
deskew, no resolution normalisation and no contrast correction, and will not
tell you when they were needed. Every financial statement in `data/` is a
200 DPI scan with no text layer, and `MH 2022-23 SFS` page 3 is skewed by 1.7°
— enough to displace a row by more than a text line's height, which makes a
table extractor bind a label to the wrong numbers and report no error.

**`verify.py` — the arithmetic self-audit.** Nothing here trusts the extractor.
Subtotals are discovered arithmetically (real statements print unlabelled ones),
and a cell that cannot be established is **withheld** and replaced by an
`[unreadable: page N, table T, row "...", col "..."]` marker, so no unverified
figure can reach a prompt. Three signals decide a cell: docling's OCR
confidence, agreement with the vision model's independent re-read, and whether
the column it sits in adds up.

## Running the tests

```bash
ingestion/venv/Scripts/python -m pytest ingestion/tests -q
```

These cover cell parsing, footing discovery, the withholding rules and
identification, and need none of the heavy dependencies — they run against
figures hand-read from the sample scans.

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

The vision model's second read is optional. Without it the pipeline still runs
and figures are still checked against their own arithmetic, but each one then
rests on a single reader — and the quality report says so rather than leaving
the reader to assume otherwise.
