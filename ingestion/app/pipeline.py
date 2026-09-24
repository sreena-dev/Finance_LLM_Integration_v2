"""The ingestion pipeline, start to finish.

    render -> precheck -> preprocess                       (unchanged)
           -> layout + OCR          docling reads text and locates tables;
                                    RapidOCR reads every word of every table
           -> structure + 2nd read  Gemma describes each table's structure and
                                    independently re-reads each row
           -> reconcile / validate / export
           -> identify

Where the digits come from: OCR, and only OCR. Gemma is used for structure and
as a second reader whose answer is *compared* with OCR's, never substituted for
it. A figure the two readers disagree on is flagged, not fixed.

**Concurrency.** The stages after preprocessing are not run one after another
over the whole document. Each page flows through them as soon as the stage
before has finished with it, so the wait for the vision model on page 1's
tables overlaps with docling working through page 2. Layout stays strictly
single-file (two docling conversions at once run out of memory); OCR runs on a
small pool; structure and second-read calls run on their own pools, sized to
the model endpoint's per-kind caps. Output order never depends on completion
order: tables are numbered as layout finds them, top to bottom, page by page.

Progress is reported through a callback because the whole run takes minutes on
a scanned filing and the gateway streams these events to the browser.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from . import (
    emit, export, identify as identify_mod, layout as layout_mod, ocr, orient, precheck, preprocess,
    reconcile, render, second_read, structure, validate,
)
from .config import Config
from .llm_client import CLIENT
from .models import IngestResult, PageImage, PageQuality, StageDuration, TableRecord
from .tabletypes import CellResult, TablePlan, TableRegion, Token

logger = logging.getLogger(__name__)

Progress = Callable[[str, str, float], None]
#: `doc_id`, the table records finished SO FAR (already `.as_dict()`-shaped,
#: matching `IngestResult.tables`'s own serialization).
OnPartial = Callable[[str, list], None]

#: Progress weights, summing to 1.0. The three stages that overlap
#: (convert / vlm / verify) advance by pages completed, so the bar moves as
#: real work finishes instead of sitting on an estimate.
_STAGE_WEIGHTS = {
    "render": 0.05,
    "precheck": 0.05,
    "preprocess": 0.10,
    "convert": 0.30,
    "vlm": 0.30,
    "verify": 0.15,
    "identify": 0.05,
}
_CONCURRENT_STAGES = ("convert", "vlm", "verify")
_EARLY_DONE = _STAGE_WEIGHTS["render"] + _STAGE_WEIGHTS["precheck"] + _STAGE_WEIGHTS["preprocess"]
#: Until identification starts, the bar stays below the point where the UI
#: treats the job as finished.
_PRE_IDENTIFY_CAP = 1.0 - _STAGE_WEIGHTS["identify"] - 0.001

#: 20 characters mirrors the threshold `render.has_text_layer` uses: a handful
#: of stray characters is noise, not content.
_MIN_PAGE_CHARS = 20


def _noop(stage: str, message: str, fraction: float) -> None:
    return None


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------

class _Progress:
    """Monotonic progress over stages that overlap in time.

    The reported stage is the earliest of convert/vlm/verify that still has
    unfinished pages, so the UI's steps only ever move forward; the fraction
    is the weighted share of (page, stage) units completed and is clamped so it
    never goes backwards. Also records each stage's wall span for
    `stage_durations` -- overlapping stages' spans overlap, so they do not sum
    to the run's total.
    """

    def __init__(self, report: Progress, n_pages: int) -> None:
        self._report = report
        self._n = max(1, n_pages)
        self._done = {s: 0 for s in _CONCURRENT_STAGES}
        self._last = _EARLY_DONE
        self._lock = threading.Lock()
        self.spans: dict[str, list[float]] = {}

    def start(self, stage: str) -> None:
        with self._lock:
            self.spans.setdefault(stage, [time.monotonic(), time.monotonic()])

    def page_done(self, stage: str) -> None:
        with self._lock:
            self._done[stage] += 1
            now = time.monotonic()
            self.spans.setdefault(stage, [now, now])[1] = now
            self._emit()

    def _emit(self) -> None:
        stage = next((s for s in _CONCURRENT_STAGES if self._done[s] < self._n), "identify")
        fraction = _EARLY_DONE + sum(
            _STAGE_WEIGHTS[s] * self._done[s] / self._n for s in _CONCURRENT_STAGES
        )
        fraction = min(_PRE_IDENTIFY_CAP, max(self._last, fraction))
        self._last = fraction
        n, d = self._n, self._done
        message = {
            "convert": f"Reading layout, text and figures (page {min(n, d['convert'] + 1)} of {n})",
            "vlm": f"Understanding table structure and re-reading rows ({d['vlm']} of {n} pages done)",
            "verify": f"Checking totals and assembling tables ({d['verify']} of {n} pages done)",
            "identify": "Working out the entity, year and framework",
        }[stage]
        self._report(stage, message, fraction)

    def announce(self) -> None:
        with self._lock:
            self._emit()


# ---------------------------------------------------------------------------
# per-table state
# ---------------------------------------------------------------------------

@dataclass
class _TableJob:
    table_no: int
    region: TableRegion
    tokens: list[Token] = field(default_factory=list)
    plan: TablePlan | None = None
    #: Stages this table has already been counted as finished in. Each table is
    #: only ever handled by one thread at a time, so no lock is needed.
    completed: set[str] = field(default_factory=set)


class _Extraction:
    """Everything that happens between preprocessing and identification."""

    def __init__(self, doc_id: str, filename: str, images: dict[int, "np.ndarray"],
                 vlm_available: bool, progress: _Progress, on_partial: OnPartial | None,
                 notes: list[str]) -> None:
        self.doc_id, self.filename = doc_id, filename
        self.images = images
        self.vlm = vlm_available
        self.progress = progress
        self.on_partial = on_partial
        self.notes = notes

        self.lock = threading.Lock()
        self.records: dict[int, TableRecord] = {}
        self.infos: dict[int, export.TableInfo] = {}
        self.page_markdown: dict[int, str] = {}
        self.page_images: dict[int, PageImage] = {}
        self.page_tables: dict[int, int] = {}
        self.remaining = {s: {} for s in _CONCURRENT_STAGES}
        self.outstanding = 0
        self.layout_finished = False
        self.all_done = threading.Event()
        self._last_partial = time.monotonic()
        self.rows_read = 0
        self.rows_skipped = 0
        self.rows_failed = 0

        self.ocr_pool = ThreadPoolExecutor(max(1, Config.OCR_WORKERS), thread_name_prefix="ocr")
        self.structure_pool = ThreadPoolExecutor(max(1, Config.LLM_STRUCTURE_CONCURRENCY), thread_name_prefix="structure")
        self.finish_pool = ThreadPoolExecutor(max(2, Config.LLM_VISION_CONCURRENCY), thread_name_prefix="finish")
        self.vision_pool = ThreadPoolExecutor(max(1, Config.LLM_VISION_CONCURRENCY), thread_name_prefix="vision")

    # -- driving ---------------------------------------------------------

    def run(self, page_nos: list[int]) -> None:
        """Layout each page in order, feeding tables into the rest as they appear."""
        table_no = 0
        self.progress.start("convert")
        self.progress.announce()
        try:
            for page_no in page_nos:
                image = self.images[page_no]
                try:
                    page = layout_mod.ENGINE.analyse(image, page_no)
                except Exception as exc:
                    logger.exception("layout failed on page %s", page_no)
                    self.notes.append(
                        f"Page {page_no}: layout analysis failed ({type(exc).__name__}: {exc}). Text and "
                        "tables on this page are absent from this extraction, which is not evidence "
                        "that the filing omitted them."
                    )
                    page = layout_mod.PageLayout(page_no=page_no, markdown="")
                self.notes.extend(page.notes)
                with self.lock:
                    self.page_markdown[page_no] = page.markdown
                    count = len(page.regions)
                    self.page_tables[page_no] = count
                    for stage in _CONCURRENT_STAGES:
                        self.remaining[stage][page_no] = count
                    self.outstanding += count

                if count == 0:
                    for stage in _CONCURRENT_STAGES:
                        self.progress.page_done(stage)
                    self._finish_page(page_no)
                    continue
                for region in page.regions:
                    table_no += 1
                    self._start_table(_TableJob(table_no, region), page_no)
        finally:
            with self.lock:
                self.layout_finished = True
                if self.outstanding == 0:
                    self.all_done.set()

        if not self.all_done.wait(timeout=Config.DOCUMENT_TIMEOUT):
            self.notes.append(
                "Extraction hit the time limit; tables still being processed were left out. "
                "Re-upload the missing pages on their own."
            )
        for pool in (self.ocr_pool, self.structure_pool, self.finish_pool, self.vision_pool):
            pool.shutdown(wait=False, cancel_futures=True)
        self._publish_partial(force=True)

    # -- chaining ----------------------------------------------------------

    def _chain(self, pool, fn, job: _TableJob, page_no: int, stage: str, nxt) -> None:
        future = pool.submit(fn, job, page_no)

        def done(fut) -> None:
            try:
                fut.result()
            except Exception as exc:
                self._fail(job, page_no, stage, exc)
                return
            try:
                nxt(job, page_no)
            except Exception as exc:  # a broken hand-off must never strand the job
                self._fail(job, page_no, stage, exc)

        future.add_done_callback(done)

    def _start_table(self, job: _TableJob, page_no: int) -> None:
        self._chain(self.ocr_pool, self._do_ocr, job, page_no, "convert", self._after_ocr)

    def _after_ocr(self, job: _TableJob, page_no: int) -> None:
        self._stage_done(job, page_no, "convert")
        self.progress.start("vlm")
        self._chain(self.structure_pool, self._do_structure, job, page_no, "vlm", self._after_structure)

    def _after_structure(self, job: _TableJob, page_no: int) -> None:
        self.progress.start("verify")
        self._chain(self.finish_pool, self._do_finish, job, page_no, "verify", self._after_finish)

    def _after_finish(self, job: _TableJob, page_no: int) -> None:
        self._stage_done(job, page_no, "verify")
        self._table_finished()
        self._publish_partial(force=False)

    def _fail(self, job: _TableJob, page_no: int, stage: str, exc: Exception) -> None:
        logger.exception("table %s on page %s failed in %s", job.table_no, page_no, stage)
        with self.lock:
            self.notes.append(
                f"Table {job.table_no} on page {page_no} could not be processed "
                f"({type(exc).__name__}: {exc}) and is absent from this extraction."
            )
        # Whatever this table had not yet been counted in, count it in now:
        # a failed table must not leave its page's progress waiting forever.
        for pending in _CONCURRENT_STAGES:
            self._stage_done(job, page_no, pending)
        self._table_finished()

    def _stage_done(self, job: _TableJob, page_no: int, stage: str) -> None:
        if stage in job.completed:
            return
        job.completed.add(stage)
        with self.lock:
            self.remaining[stage][page_no] -= 1
            finished = self.remaining[stage][page_no] == 0
        if finished:
            self.progress.page_done(stage)
            if stage == "verify":
                self._finish_page(page_no)

    def _table_finished(self) -> None:
        with self.lock:
            self.outstanding -= 1
            if self.outstanding == 0 and self.layout_finished:
                self.all_done.set()

    def _finish_page(self, page_no: int) -> None:
        """Encode the page image for the document pane, then let go of the bitmap."""
        image = self.images.pop(page_no, None)
        if image is not None:
            self.page_images[page_no] = emit.build_page_image(page_no, image)

    # -- the work ------------------------------------------------------------

    def _do_ocr(self, job: _TableJob, page_no: int) -> None:
        job.tokens = ocr.read_region(self.images[page_no], job.region.bbox, page_no, job.table_no)

    def _crop(self, job: _TableJob, page_no: int):
        image = self.images[page_no]
        h, w = image.shape[:2]
        x0, y0, x1, y1 = job.region.bbox
        pad = 12
        box = (max(0, int(x0 - pad)), max(0, int(y0 - pad)), min(w, int(x1 + pad)), min(h, int(y1 + pad)))
        return image[box[1]:box[3], box[0]:box[2]], box

    def _do_structure(self, job: _TableJob, page_no: int) -> None:
        crop, box = self._crop(job, page_no)
        job.plan = structure.build_plan(
            job.tokens, job.region, job.table_no, crop, tuple(float(v) for v in box),
            use_llm=self.vlm, doc_id=self.doc_id,
        )
        job.plan.notes.extend(structure.header_notes(job.plan))

    def _take_read_budget(self) -> bool:
        with self.lock:
            if self.rows_read >= Config.SECOND_READ_MAX_ROWS:
                self.rows_skipped += 1
                return False
            self.rows_read += 1
            return True

    def _do_finish(self, job: _TableJob, page_no: int) -> None:
        plan = job.plan
        assert plan is not None
        image = self.images[page_no]
        value_cols = [c.index for c in plan.value_columns]
        x0, x1 = job.region.bbox[0], job.region.bbox[2]

        reads: dict[int, second_read.RowRead | None] = {}
        if self.vlm:
            pending = {}
            for row in plan.rows:
                if not any(row.cells.get(c) for c in value_cols):
                    continue
                if not self._take_read_budget():
                    continue
                # Tight: a generous margin pulls a neighbouring line into the
                # strip and the model then reads the wrong line (seen on a real
                # "Total (B)" row, read as the row beneath it).
                pad = max(4.0, 0.15 * (row.y1 - row.y0))
                pending[row.index] = self.vision_pool.submit(
                    second_read.read_row, image, row.y0, row.y1, x0, x1, pad,
                    self.doc_id, f"t{job.table_no}_r{row.index}", row.label,
                )
            for index, future in pending.items():
                try:
                    reads[index] = future.result()
                except Exception:
                    reads[index] = None
                if reads[index] is None:
                    with self.lock:
                        self.rows_failed += 1

        self._stage_done(job, page_no, "vlm")

        results: dict[int, dict[int, CellResult]] = {}
        for row in plan.rows:
            cells, row_notes = reconcile.reconcile_row(row, value_cols, reads.get(row.index))
            results[row.index] = cells
            plan.notes.extend(row_notes)

        crop, _ = self._crop(job, page_no)
        record, info = export.build_table_record(
            plan, results, self.doc_id, self.page_markdown.get(page_no, ""),
            emit.jpeg_b64(crop), self.filename,
        )
        with self.lock:
            self.records[job.table_no] = record
            self.infos[job.table_no] = info
            self.notes.extend(plan.notes)

    def _publish_partial(self, force: bool) -> None:
        if self.on_partial is None:
            return
        with self.lock:
            now = time.monotonic()
            if not force and now - self._last_partial < Config.PARTIAL_RESULT_INTERVAL_SECONDS:
                return
            if not self.records:
                return
            self._last_partial = now
            snapshot = [self.records[n].as_dict() for n in sorted(self.records)]
        self.on_partial(self.doc_id, snapshot)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def run(
    data: bytes, filename: str, progress: Progress | None = None,
    on_partial: OnPartial | None = None,
) -> IngestResult:
    """Ingest one uploaded PDF and return everything known about it.

    `on_partial`, if given, is called periodically (at most every
    `Config.PARTIAL_RESULT_INTERVAL_SECONDS`, plus once more when the last
    table finishes) with `(doc_id, tables_so_far)`, tables only -- the
    document's identification and narrative text are computed after every
    table, so a partial snapshot genuinely cannot carry them.
    """
    report = progress or _noop
    started = time.monotonic()
    done = 0.0
    stage_durations: list[StageDuration] = []
    _stage_start = time.monotonic()
    _current_stage: str | None = None

    def advance(stage: str, message: str) -> None:
        nonlocal done, _stage_start, _current_stage
        now = time.monotonic()
        if _current_stage is not None:
            stage_durations.append(StageDuration(_current_stage, round(now - _stage_start, 3)))
        _current_stage = stage
        _stage_start = now
        report(stage, message, min(0.99, done))
        done += _STAGE_WEIGHTS.get(stage, 0.0)

    # ---- render ----------------------------------------------------------
    advance("render", "Reading the PDF")
    pages = render.render(data)
    doc_id = emit.doc_id_for(filename, data)
    notes: list[str] = []

    has_text_layer = render.document_has_text_layer(pages)
    if has_text_layer:
        notes.append(
            "This document carries an extractable text layer, but every page is "
            "rasterised and read by OCR regardless -- the same quality corrections "
            "(deskew, contrast) apply uniformly whatever the source. Every figure "
            "below was read by OCR from the page image, not taken from the file's own text."
        )
    else:
        notes.append(
            "This document has no text layer on any page: it is a scan, and every "
            "figure below was read by OCR rather than taken from the file."
        )

    # ---- precheck --------------------------------------------------------
    advance("precheck", f"Checking the quality of {len(pages)} page(s)")
    qualities = precheck.check_document(pages)

    unusable = [q for q in qualities if q.grade == "poor"]
    if unusable and len(unusable) == len(qualities):
        notes.append(
            "Every page of this document graded poor. Figures read from it need "
            "checking against the original before use."
        )

    # ---- preprocess ------------------------------------------------------
    advance("preprocess", "Straightening and normalising pages")
    images = preprocess.preprocess(pages, qualities)

    # Skip blank and duplicate pages: a duplicate re-read is not merely wasted
    # OCR, it double-counts every table on the sheet.
    keep = [
        i for i, q in enumerate(qualities)
        if not q.is_blank and q.duplicate_of is None
    ]
    if len(keep) < len(images):
        skipped = len(images) - len(keep)
        notes.append(
            f"{skipped} page(s) were blank or repeated another page and were read once only."
        )

    kept_images = [images[i] for i in keep]
    kept_qualities = [qualities[i] for i in keep]

    # Release the bitmaps nothing reads again before layout loads its models:
    # the peak is what has to fit, not the average.
    del pages
    del images

    # ---- sideways pages ------------------------------------------------------------
    # A wide schedule scanned with the paper turned arrives with its text running
    # bottom-to-top. Turn it upright now, so layout, OCR and the page shown to the
    # reader all see it the right way up.
    kept_images, turned = orient.correct_pages(kept_images)
    for index, degrees in sorted(turned.items()):
        kept_qualities[index].orientation_quadrant = degrees
        notes.append(
            f"Page {kept_qualities[index].page_no} was scanned sideways; it was turned {degrees} degrees "
            "clockwise so its text reads upright before it was read."
        )

    # ---- the second reader -------------------------------------------------
    vlm_available = CLIENT.configured() and CLIENT.perceives(doc_id)
    if not CLIENT.configured() or not vlm_available:
        if CLIENT.configured():
            notes.append(
                "The vision model accepted page images but failed a check that it can actually read "
                "them (it could not read back a number printed in a test image), so it was not used. "
                "Table structure was worked out from positions and figures are read by OCR alone, "
                "unconfirmed by a second read. This is a problem with how the vision model is being "
                "served, not with this document."
            )
        else:
            notes.append(
                "A second independent read by the vision model was not available, so table structure "
                "was worked out from positions and figures are read by OCR alone, unconfirmed by a second read."
            )

    # ---- layout, OCR, structure, second read, reconcile, export -------------
    if _current_stage is not None:
        stage_durations.append(StageDuration(_current_stage, round(time.monotonic() - _stage_start, 3)))
        _current_stage = None
    page_nos = [q.page_no for q in kept_qualities]
    images_by_page = {q.page_no: img for q, img in zip(kept_qualities, kept_images)}
    del kept_images

    tracker = _Progress(report, len(page_nos))
    extraction = _Extraction(doc_id, filename, images_by_page, vlm_available, tracker, on_partial, notes)
    extraction.run(page_nos)

    for stage, (t0, t1) in tracker.spans.items():
        stage_durations.append(StageDuration(stage, round(t1 - t0, 3)))

    if extraction.rows_skipped:
        notes.append(
            f"{extraction.rows_skipped} row(s) were not given a second read because the per-document "
            "limit was reached; their figures are read by OCR alone."
        )
    if extraction.rows_failed:
        notes.append(
            f"The second read failed for {extraction.rows_failed} row(s); their figures are read by OCR alone."
        )

    order = sorted(extraction.records)
    records = [extraction.records[n] for n in order]
    infos = [extraction.infos[n] for n in order]

    empty = [
        q.page_no for q in kept_qualities
        if not any(r.page_ocr_start == q.page_no for r in records)
        and len((extraction.page_markdown.get(q.page_no) or "").strip()) < _MIN_PAGE_CHARS
    ]
    if empty:
        notes.append(
            f"Page(s) {', '.join(str(p) for p in empty)} produced neither a table nor readable text. "
            "That is not evidence the filing omitted anything on them; check them against the original."
        )

    # ---- verify: checks that span tables --------------------------------------
    if validate.RULES.get("cross_table", {}).get("enabled", True):
        by_table: dict[str, list] = {}
        for check in validate.check_cross_table(export.cross_table_links(infos)):
            by_table.setdefault(check.table_id, []).append(check)
        for record in records:
            extra = by_table.get(record.table_id)
            if extra:
                export.add_footings(record, extra)

    # ---- identify --------------------------------------------------------
    _current_stage = "identify"
    _stage_start = time.monotonic()
    report("identify", "Working out the entity, year and framework", 1.0 - _STAGE_WEIGHTS["identify"])
    pages_text = [extraction.page_markdown[p] for p in sorted(extraction.page_markdown)]
    identification = identify_mod.identify(pages_text or [""])

    texts = emit.build_text_records(
        doc_id,
        extraction.page_markdown,
        flavour=identification.statement_flavour,
        entity_name=identification.entity_name,
        source_file=filename,
    )
    page_images = [extraction.page_images[p] for p in sorted(extraction.page_images)]

    if _current_stage is not None:
        stage_durations.append(StageDuration(_current_stage, round(time.monotonic() - _stage_start, 3)))
    stage_durations.append(StageDuration("total", round(time.monotonic() - started, 3)))

    report("done", "Finished", 1.0)
    return emit.build_result(
        doc_id=doc_id,
        filename=filename,
        data=data,
        identification=identification,
        qualities=qualities,
        tables=records,
        texts=texts,
        vlm_used=vlm_available,
        notes=notes,
        page_images=page_images,
        stage_durations=stage_durations,
    )
