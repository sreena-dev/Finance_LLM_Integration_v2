"""The ingestion pipeline, start to finish.

    render -> precheck -> preprocess -> convert -> vlm second read
           -> verify -> identify -> emit

Each stage is a separate module and none of them import each other; this is the
only place that knows the order. That matters because the order is a design
decision rather than an accident -- preprocessing has to happen before docling
sees the page, and verification has to happen before anything is emitted -- and
keeping it in one readable function is what stops it drifting.

Progress is reported through a callback rather than returned, because the whole
run takes minutes on a scanned filing and the gateway streams these events to
the browser as they happen.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import convert as convert_mod
from . import (
    emit, identify as identify_mod, precheck, preprocess, render, structure_repair,
    verify, vlm_read,
)
from .config import Config
from .models import IngestResult, PageQuality, RecoveryStats, StageDuration
from .tables import parse_markdown_tables

logger = logging.getLogger(__name__)

Progress = Callable[[str, str, float], None]
#: `doc_id`, the table records verified SO FAR (already `.as_dict()`-shaped,
#: matching `IngestResult.tables`'s own serialization) -- see `run()`'s
#: `on_partial` parameter and `Config.PARTIAL_RESULT_INTERVAL_SECONDS`.
OnPartial = Callable[[str, list], None]

#: Stage weights for the progress bar, summing to 1.0. Taken from measured
#: proportions on a 25-page scan: OCR and table structure dominate everything
#: else by an order of magnitude, and a bar that gives each stage equal width
#: sits at 60% for four minutes and reads as a hang.
_STAGE_WEIGHTS = {
    "render": 0.05,
    "precheck": 0.05,
    "preprocess": 0.10,
    "convert": 0.55,
    "vlm": 0.15,
    "verify": 0.05,
    "identify": 0.05,
}


def _noop(stage: str, message: str, fraction: float) -> None:
    return None


#: Seconds/page used ONLY to ESTIMATE progress display during the single,
#: unavoidably blocking `convert_mod.convert()` call below -- the same
#: measurement `Config.DOCUMENT_TIMEOUT`'s own comment cites ("~8s/page with
#: full-page OCR"). This is a display estimate, not a promise, and never
#: drives real behaviour (the timeout itself is `Config.DOCUMENT_TIMEOUT`,
#: untouched). Checked directly against the installed docling API before
#: writing this, not assumed: `DocumentConverter.convert()` takes no
#: progress callback and exposes no per-page hook, so real per-page
#: confirmation genuinely is not available from outside that one call --
#: this is the honest alternative to leaving the bar frozen on one message
#: for the 10-20+ minutes `convert()` can take on a 100-200 page filing.
_CONVERT_SECONDS_PER_PAGE_ESTIMATE = 8.0


def _convert_with_progress(
    kept_images: list, kept_qualities: list["PageQuality"], report_within_stage: Callable,
) -> "convert_mod.Converted":
    """Run `convert_mod.convert()` while ticking an ESTIMATED progress signal.

    A background thread reports elapsed-time-based progress against
    `_CONVERT_SECONDS_PER_PAGE_ESTIMATE` every 2 seconds while the main
    thread blocks on the one real `convert()` call; stopped the moment that
    call returns (or raises), whichever comes first, so it can never keep
    reporting past the stage's actual end. Every message this emits says
    "estimated" -- it is not a claim about which page docling is actually
    on, only a clock converted into words so the connection doesn't look
    dead.
    """
    page_count = max(1, len(kept_images))
    estimated_total_seconds = page_count * _CONVERT_SECONDS_PER_PAGE_ESTIMATE
    stop = threading.Event()
    start = time.monotonic()

    def _tick() -> None:
        while not stop.wait(2.0):
            elapsed = time.monotonic() - start
            fraction = (elapsed / estimated_total_seconds) if estimated_total_seconds else 0.0
            fraction = max(0.0, min(0.97, fraction))
            estimated_page = min(page_count, int(fraction * page_count) + 1)
            report_within_stage(
                "convert",
                f"Detecting layout and reading tables (page ~{estimated_page} of "
                f"{page_count}, estimated)",
                fraction,
            )

    ticker = threading.Thread(target=_tick, daemon=True)
    ticker.start()
    try:
        return convert_mod.convert(kept_images, kept_qualities)
    finally:
        stop.set()
        ticker.join(timeout=1.0)


#: 20 characters mirrors the threshold `render.has_text_layer` uses for the
#: SOURCE pdf's own text layer -- not literally the same measurement (this one
#: is post-OCR markdown, not embedded PDF text), but the same intent: a
#: handful of stray characters is noise, not content.
_MIN_PAGE_CHARS = 20


def _empty_page_note(kept_qualities: list[PageQuality], converted: "convert_mod.Converted") -> str | None:
    """Name any KEPT page that produced neither a table nor readable text.

    This is exactly the silent-loss shape Phase 1 exists to catch: a document
    that hit it used to report a clean conversion with 0 tables, 0 chunks and
    no note anywhere to explain why. Checked against `kept_qualities`, not the
    full page list -- a blank or duplicate page is SUPPOSED to contribute
    nothing, so only a page that survived that filter and still came back
    empty is worth naming.
    """
    tabled_pages = {t.page_no for t in converted.tables}
    empty_pages = [
        q.page_no for q in kept_qualities
        if q.page_no not in tabled_pages
        and len((converted.page_markdown.get(q.page_no) or "").strip()) < _MIN_PAGE_CHARS
    ]
    if not empty_pages:
        return None
    return (
        f"Page(s) {', '.join(str(p) for p in empty_pages)} produced no table and "
        "no readable text at all. This is an extraction failure worth checking "
        "against the original scan -- conversion may have failed outright on "
        "that page, or its content may have merged into a neighbouring page's read."
    )


def _vlm_cap_note(prepared: list[dict], vlm_available: bool) -> str | None:
    """Say so when Config.VLM_MAX_TABLES silently left tables unverified.

    Verified concretely: a 78-page annual report exceeds the cap (60) with
    nothing in the quality report to say so. Table INDEX is assembly order,
    not importance order -- a schedule note near the end of a filing is not
    less material than the balance sheet -- so this must not silently degrade
    whichever tables happen to be read last. Prioritising by actual
    importance is real future work (the escalation ladder); naming the
    truncation here is the honest minimum until then.
    """
    capped = [
        p for p in prepared
        if p["crop"] is not None and vlm_available and p["index"] > Config.VLM_MAX_TABLES
    ]
    if not capped:
        return None
    return (
        f"{len(capped)} table(s) past the {Config.VLM_MAX_TABLES}-table cap "
        "(INGEST_VLM_MAX_TABLES) did not get a second read from the vision "
        "model, so their figures rest on their own arithmetic only. Table "
        "order in a filing is not importance order, so this may include "
        "tables that mattered."
    )


def _place_confirmed_gap_fills(
    prepared: list[dict], vlm_available: bool, image_by_number: dict, notes: list[str],
) -> RecoveryStats:
    """Place proposed gap fills -- but ONLY those a second reader confirms.

    One bounded, DOCUMENT-WIDE wave (one shared budget, one bounded pool), for
    the same cross-table batching reason as the escape hatch after it: a
    synchronous per-table callback would serialise every network call inside
    the table loop.

    **With no second reader reachable, nothing is placed** -- there is
    deliberately no fallback to position alone. Mutates each prepared entry's
    table and re-binds it, so ``binding`` reflects the table as it now stands
    (without that a filled cell would still read as unbound/leftover and be
    both re-read and reported as unaccounted-for).
    """
    all_fills = [
        (p_index, r, c, tok)
        for p_index, p in enumerate(prepared)
        for (r, c, tok) in p.get("gap_fills", [])
    ]
    fill_budget = all_fills[: Config.VLM_MAX_GAP_FILL_CONFIRMS]
    if len(all_fills) > len(fill_budget):
        notes.append(
            f"{len(all_fills) - len(fill_budget)} figure(s) OCR read inside a table "
            "but the extracted grid left blank were NOT placed for BUDGET reasons "
            f"(the {Config.VLM_MAX_GAP_FILL_CONFIRMS}-confirmation-per-document cap, "
            "INGEST_VLM_MAX_GAP_FILL_CONFIRMS) rather than for lack of evidence."
        )

    def _run_fill_confirm(p_index: int, row: int, col: int, tok):
        p = prepared[p_index]
        page_image = image_by_number.get(p["converted_table"].page_no)
        ok = vlm_read.confirm_gap_fill(
            p["table"], row, col, tok, page_image, p["converted_table"],
        )
        return p_index, row, col, tok, ok

    confirmed_by_table: dict[int, list] = {}
    if fill_budget and vlm_available:
        with ThreadPoolExecutor(max_workers=Config.VLM_RESCUE_CONCURRENCY) as pool:
            futures = [pool.submit(_run_fill_confirm, *req) for req in fill_budget]
            for future in futures:
                try:
                    p_index, row, col, tok, ok = future.result()
                except Exception:
                    logger.exception("VLM gap-fill confirmation failed")
                    continue
                if ok:
                    confirmed_by_table.setdefault(p_index, []).append((row, col, tok))
    elif fill_budget:
        notes.append(
            f"{len(fill_budget)} figure(s) OCR read inside a table but the "
            "extracted grid left blank were NOT placed: placing a figure "
            "requires a second reader to agree, and the vision model was "
            "unavailable."
        )

    for p_index, fills in confirmed_by_table.items():
        p = prepared[p_index]
        converted_table = p["converted_table"]
        if not structure_repair.apply_gap_fills(p["table"], fills):
            continue
        header_count = max(
            [len(p["table"].header)] + [len(r) for r in p["table"].rows]
        )
        col_bands = structure_repair._column_ranges(converted_table.cells, header_count)
        header_bottom = structure_repair._header_bottom(converted_table.cells)
        ledger = structure_repair.build_number_ledger(
            converted_table.ocr_lines, header_bottom,
        )
        p["binding"] = structure_repair.bind_table(
            p["table"], ledger, col_bands, converted_table.ocr_lines, header_bottom,
        )

    return RecoveryStats(
        requested=len(all_fills),
        attempted=len(fill_budget) if vlm_available else 0,
        succeeded=sum(len(fills) for fills in confirmed_by_table.values()),
    )


def _stage_progress(done: float, stage: str, stage_fraction: float) -> float:
    """The overall fraction to report for a tick WITHIN `stage`, given
    `done` -- the running total `run()`'s `advance()` closure maintains --
    and `stage_fraction` (0..1, how much of THIS stage's own work is done,
    not a fraction of the whole document). Pure function so the arithmetic
    is directly unit-testable; `run()`'s `report_within_stage` closure below
    is this plus the actual `report()` call.

    `done` already includes THIS stage's own weight by the time any caller
    can reach here: `advance(stage, ...)` reports the stage-start message
    and THEN immediately adds the stage's weight to `done`, in preparation
    for the NEXT `advance()` call -- so `done` is really "cumulative weight
    through and including the current stage", not "weight of everything
    before it". Subtracting the current stage's own weight back out
    recovers its true starting baseline. Verified empirically, not just
    derived: an earlier version without this subtraction reported 76.9% at
    the very first tick inside `convert` (a stage that starts at 20% and is
    capped at 75%) -- see `tests/test_pipeline_notes.py`.
    """
    weight = _STAGE_WEIGHTS.get(stage, 0.0)
    stage_start = done - weight
    fraction = max(0.0, min(1.0, stage_fraction))
    return min(0.99, stage_start + weight * fraction)


def run(
    data: bytes, filename: str, progress: Progress | None = None,
    on_partial: OnPartial | None = None,
) -> IngestResult:
    """Ingest one uploaded PDF and return everything known about it.

    `on_partial`, if given, is called periodically (at most every
    `Config.PARTIAL_RESULT_INTERVAL_SECONDS`, plus always once more for the
    final table) with `(doc_id, tables_so_far)` while table records are
    being verified -- `tables_so_far` is already `.as_dict()`-shaped,
    matching `IngestResult.as_dict()["tables"]`. Deliberately TABLES ONLY:
    a document's identification (entity, financial year, framework) and its
    narrative text chunks are computed later, in the `identify` stage,
    which runs AFTER every table -- there is no cheap way to know "what
    company is this" any earlier, so a partial snapshot genuinely cannot
    carry it. See `jobs.PartialResult`'s own docstring for the consumer
    side of this.
    """
    report = progress or _noop
    done = 0.0
    # Structured observability (Config-free, always on -- pure bookkeeping,
    # never gates anything): how long each stage actually took, timed from
    # the SAME `advance()` transitions already driving the SSE progress bar,
    # so this can never drift out of sync with what a caller was shown. See
    # models.StageDuration.
    stage_durations: list["StageDuration"] = []
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

    def report_within_stage(stage: str, message: str, stage_fraction: float) -> None:
        """A progress tick WITHIN the stage `advance(stage, ...)` most recently
        entered -- see `_stage_progress` for the arithmetic.

        `advance()` alone gives one tick per stage, called at the stage's
        START -- fine for the five short stages, dishonest for the two long
        ones: `convert` (55% of the bar) and the whole-table VLM pass inside
        the `vlm` stage below, each multi-minute, each otherwise a SINGLE
        call from this function's point of view. Left as `advance()` alone,
        the bar sits frozen at one message for the entire multi-minute
        duration of either -- not wrong, but indistinguishable from a hang
        to whoever is watching it, with nothing but a 15s SSE heartbeat
        (`main.py`) to prove otherwise. This adds ticks WITHOUT moving
        `done` past the current stage's own start -- `advance()` to the
        NEXT stage still adds the FULL stage weight once, exactly as
        before; this only fills in what happens between one `advance()`
        call and the next.
        """
        report(stage, message, _stage_progress(done, stage, stage_fraction))

    # ---- render ----------------------------------------------------------
    advance("render", "Reading the PDF")
    pages = render.render(data)
    doc_id = emit.doc_id_for(filename, data)
    notes: list[str] = []

    # Purely descriptive now -- see convert._pipeline_options for why this no
    # longer gates whether OCR runs. `pages_to_pdf` rasterises every page
    # before docling ever sees it, so OCR is not optional regardless of what
    # the source PDF had; this stays only to word the note honestly and to
    # flag a source text layer for a later phase that could read it directly.
    has_text_layer = render.document_has_text_layer(pages)
    if has_text_layer:
        notes.append(
            "This document carries an extractable text layer, but every page is "
            "rasterised and re-OCR'd before conversion regardless -- the same "
            "quality corrections (deskew, contrast) apply uniformly whatever the "
            "source, and OCR of a clean, computer-set page is reliable. Every "
            "figure below was still read by OCR, not taken from the file's own text."
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

    # Release the bitmaps nothing reads again, BEFORE docling loads its models
    # and allocates its own copies -- the peak is what has to fit, not the
    # average.
    #
    # `pages` holds one raw greyscale render per page and is dead from here:
    # its last reader was `preprocess` above. At 300 DPI an A4 page is ~8.7 MB,
    # so a 22-page filing is ~190 MB of arrays kept alive for the rest of the
    # run purely by the name still being bound. `images` likewise still
    # references the blank and duplicate pages that `keep` just excluded; the
    # kept ones survive in `kept_images`, which is what everything downstream
    # actually uses.
    del pages
    del images

    # ---- convert ---------------------------------------------------------
    advance("convert", "Detecting layout and reading tables")
    converted = _convert_with_progress(kept_images, kept_qualities, report_within_stage)
    notes.extend(converted.errors)
    notes.extend(converted.notes)

    empty_note = _empty_page_note(kept_qualities, converted)
    if empty_note:
        notes.append(empty_note)

    # ---- VLM second read -------------------------------------------------
    vlm_available = Config.vlm_configured() and vlm_read.probe()
    if vlm_available and not vlm_read.perceives():
        # The endpoint ACCEPTS images but the model does not perceive them.
        # Measured on the live deployment: it read an image printing "HELLO
        # 12345 TOTAL" as "text", said "no image was provided" for a full
        # balance sheet, and invented a complete income statement when pushed.
        # probe() cannot see this -- it only checks the request is accepted --
        # and a second reader that cannot see is worse than none, because what
        # it invents looks like evidence.
        vlm_available = False
        notes.append(
            "The vision model accepted page images but failed a check that it "
            "can actually read them (it could not read back a number printed "
            "in a test image), so its second read was not used. Figures here "
            "are corroborated by their own arithmetic only. This is a problem "
            "with how the vision model is being served, not with this document."
        )
    elif not vlm_available:
        notes.append(
            "A second independent read by the vision model was not available, so "
            "figures here are corroborated by their own arithmetic only."
        )
    advance("vlm", "Re-reading tables with the vision model" if vlm_available else "Skipping the second read")

    # `advance("verify", ...)` is called LATER, after the whole-table VLM
    # pass below (Pass 2) actually runs -- it used to fire here, before any
    # of that ran, which meant the single biggest remaining chunk of real
    # wall-clock time in the whole pipeline (measured: 5-10 minutes on a
    # filing with 40+ tables) executed silently under the "verify" stage's
    # label and 5% budget while still SHOWING "Re-reading tables with the
    # vision model" from the `advance("vlm", ...)` above -- the progress bar
    # would sit still through the vlm stage's own share, jump to ~verify's
    # starting point, then sit still AGAIN for the whole VLM wave under the
    # wrong label. Moved so the stage a user sees matches the work actually
    # running underneath it.
    records = []
    page_by_number = {q.page_no: q for q in kept_qualities}
    image_by_number = {q.page_no: img for q, img in zip(kept_qualities, kept_images)}

    # Titles, recovered from the page rather than from the table.
    #
    # docling supplies a caption only where the document marks one up, and a
    # scanned filing marks up nothing -- every table came back with title=None
    # on the real corpus. That matters more than it looks: `financial_stmt_type`
    # is derived from the title, and without it `_find_statement_tables` matches
    # nothing and every downstream tool reports the document as having no
    # financial statements at all.
    #
    # So each page's own markdown is parsed too, where `parse_markdown_tables`
    # captures the prose line immediately above each table as its title, and the
    # titles are matched to docling's tables by their order on the page.
    titles_by_page: dict[int, list[str | None]] = {}
    for page_no, page_md in converted.page_markdown.items():
        titles_by_page[page_no] = [t.title for t in parse_markdown_tables(page_md, page_no)]
    seen_on_page: dict[int, int] = {}

    # Pass 1: parse every table and prepare its crop/snippet. Cheap, local,
    # CPU-only work -- collected rather than acted on immediately, because the
    # one genuinely slow step below, the VLM round trip, is network I/O and
    # completely independent from one table to the next. Running it serially
    # here used to mean the total wait was (table count) x (one VLM call);
    # measured on a real filing, ~15-20s per call, and a document with 40+
    # tables spent most of a 5-10 minute conversion in this loop alone, while
    # docling's own layout+OCR pass over the same pages took a fraction of
    # that. See Pass 2.
    prepared = []
    for index, converted_table in enumerate(converted.tables, 1):
        parsed = parse_markdown_tables(
            converted_table.markdown, converted_table.page_no, prefix=f"t{index}_"
        )
        if not parsed:
            continue
        table = parsed[0]

        nth = seen_on_page.get(converted_table.page_no, 0)
        seen_on_page[converted_table.page_no] = nth + 1
        from_page = titles_by_page.get(converted_table.page_no) or []
        table.title = (
            converted_table.title
            or table.title
            or (from_page[nth] if nth < len(from_page) else None)
        )

        snippet = None
        crop = None
        page_image = image_by_number.get(converted_table.page_no)
        if page_image is not None:
            # No page height passed: crop() derives it from the image and
            # RENDER_DPI, which is the only correct answer here. It used to be
            # passed as None against a required parameter, which silently
            # cropped in the wrong coordinate space -- see crop()'s docstring.
            crop = vlm_read.crop(page_image, converted_table.bbox)
            # The citation snippet is produced whether or not the vision model
            # is reachable: showing the reader the scan a figure came from is
            # not contingent on a second model having read it.
            snippet = vlm_read.snippet(crop)

        prepared.append({
            "index": index,
            "converted_table": converted_table,
            "table": table,
            "crop": crop,
            "snippet": snippet,
            "needs_vlm": crop is not None and vlm_available and index <= Config.VLM_MAX_TABLES,
            # Whether THIS table's own row/column grid looks unreliable --
            # computed now, independent of whether a VLM call is even
            # available, so the note it produces (see Pass 3a) is honest
            # about the grid even when structure re-read itself is disabled.
            "structural_risk": vlm_read.assess_structural_risk(table, converted_table),
        })

    cap_note = _vlm_cap_note(prepared, vlm_available)
    if cap_note:
        notes.append(cap_note)

    # Pass 2: fire every table's second read at once, bounded by
    # Config.VLM_CONCURRENCY rather than left unbounded, because this hits the
    # SAME vLLM endpoint the live chat agent generates against -- a large
    # ingestion job must not be able to flood it and starve every other user's
    # chat latency while it runs.
    vlm_results: dict[int, str | None] = {}
    to_transcribe = [p for p in prepared if p["needs_vlm"]]
    if to_transcribe:
        with ThreadPoolExecutor(max_workers=Config.VLM_CONCURRENCY) as pool:
            futures = {
                pool.submit(
                    vlm_read.transcribe,
                    p["crop"],
                    # Blind by default (Config.VLM_BLIND_READ): no seed, no
                    # column pinning to docling's grid, so the read is
                    # independent evidence rather than a corrected echo.
                    None if Config.VLM_BLIND_READ else p["table"].to_markdown(),
                ): p["index"]
                for p in to_transcribe
            }
            # REAL progress here, not estimated -- unlike `convert()`, this
            # loop is this function's own code, so each `future.result()`
            # returning is a genuine completion event, not a guess. `.items()`
            # order is submission order, not completion order, so a slow
            # early table can hold this loop at N-1/N for a while when a
            # later one already finished -- the count is still exact at every
            # point it's reported, just not evenly paced against wall time.
            for completed, (future, index) in enumerate(futures.items(), 1):
                try:
                    vlm_results[index] = future.result()
                except Exception:
                    # transcribe() already catches its own request/parse
                    # errors and returns None -- this is a second net, not the
                    # expected path, so it is logged rather than silently
                    # swallowed. Either way one table's read failing must not
                    # take the rest of the document down with it.
                    logger.exception("VLM transcription failed for table %d", index)
                    vlm_results[index] = None
                report_within_stage(
                    "vlm",
                    f"Re-reading tables with the vision model ({completed} of "
                    f"{len(to_transcribe)})",
                    completed / len(to_transcribe),
                )

    # ---- verify ------------------------------------------------------
    # Real work starts here, not at the earlier `advance("vlm", ...)` --
    # see that call site's own comment for why the boundary moved.
    advance("verify", "Checking that the figures add up")

    # Pass 2a: rejoin captions that wrapped onto a second printed line.
    # Deterministic and geometry-only -- it needs neither the vision model
    # nor number binding -- so it runs unconditionally, and BEFORE anything
    # below holds a row index: `alignment`, `disagreements`, `unmatched`,
    # `vlm_only` and `binding` are all built later, which makes this the one
    # insertion point where the row-index remap obligation `merge_wrapped_
    # labels` and `insert_unclaimed_rows` both carry is a no-op. Running it
    # before `select_structure` also lets docling's table compete in its
    # repaired form. See `structure_repair.join_wrapped_labels` for the five
    # guards, and why a joined row is never one whose figures moved.
    if Config.WRAPPED_LABEL_JOIN:
        for p in prepared:
            converted_table = p["converted_table"]
            joined, _ = structure_repair.join_wrapped_labels(
                p["table"], converted_table.ocr_lines,
                structure_repair._header_bottom(converted_table.cells),
            )
            if joined:
                notes.append(
                    f"Table {p['index']} on page {converted_table.page_no}: "
                    f"{len(joined)} row(s) whose printed label wrapped onto a "
                    "second line were rejoined with the row carrying their "
                    "figures, confirmed by the label's own indentation in the "
                    "page's OCR text. No figure changed."
                )

    # Pass 2b: OCR number binding -- "the VLM proposes, OCR disposes" (see
    # structure_repair.py's module docstring for the full architecture).
    # For every table, bind BOTH docling's own reading and the vision
    # model's independent read (already fetched in Pass 2 above, at zero
    # extra API cost when Config.VLM_BLIND_READ is on, its default) against
    # the SAME ledger of numbers OCR actually read off the page, and keep
    # whichever structure accounts for more of them. Replaces an earlier
    # design that compared the two structures by footing strength
    # (`vlm_read.select_structure`'s own docstring records the real-document
    # measurement that broke it: footing rewards fragmentation).
    #
    # Runs even when the vision model is unreachable: `select_structure`
    # degrades to binding docling's OWN table against the ledger with no
    # candidate to compare against, which still powers gap filling and the
    # half-read report from OCR geometry alone.
    gap_fill_stats = RecoveryStats()
    if Config.NUMBER_BINDING_ENABLED:
        for p in prepared:
            index = p["index"]
            converted_table = p["converted_table"]
            blind_markdown = (
                vlm_results.get(index) if Config.VLM_BLIND_READ
                else (vlm_read.transcribe(p["crop"], None) if p["crop"] is not None else None)
            )
            chosen, binding, replaced, reason = vlm_read.select_structure(
                blind_markdown, converted_table, p["table"], prefix=f"t{index}_",
            )
            if chosen is not p["table"]:
                # select_structure's candidate is parsed fresh from the
                # VLM's own markdown, which carries no caption -- without
                # this the table loses its title (and so its searchability
                # by title in get_schedule_note/review_account_area/etc.)
                # the moment its structure is replaced.
                chosen.title = p["table"].title
            p["table"] = chosen
            p["structure_replaced"] = replaced

            if binding.refused:
                notes.append(
                    f"Table {index} on page {converted_table.page_no}: its figures "
                    "could not be checked against the page's own OCR numbers "
                    f"({binding.refused})."
                )
                p["binding"] = None
                continue

            if replaced:
                notes.append(
                    f"Table {index} on page {converted_table.page_no}: TableFormer's "
                    "own row/column grid accounted for fewer of the figures actually "
                    f"printed in this table than an independent read of the same "
                    f"region did ({reason}), so the independent read was used. "
                    f"{len(binding.bound)} of its figures were matched to a number "
                    "the OCR engine independently read at the same position on the page."
                )

            p["binding"] = binding
            # A gap fill is only ever PROPOSED here. It is placed later, and
            # only if a second reader independently reads the same figure --
            # see the confirmation wave below. Position alone never places
            # a number.
            p["gap_fills"] = (
                structure_repair.propose_gap_fills(p["table"], binding)
                if Config.NUMBER_BINDING_GAP_FILL else []
            )

        gap_fill_stats = _place_confirmed_gap_fills(prepared, vlm_available, image_by_number, notes)

        # The half-read report, computed AFTER any fills so it describes the
        # table as it now stands.
        for p in prepared:
            binding = p.get("binding")
            if binding is None:
                continue
            index = p["index"]
            converted_table = p["converted_table"]
            # The half-read report: a figure printed inside this table's
            # region that no row or column of the CHOSEN structure accounts
            # for. Emitted regardless of whether the table was replaced -- a
            # docling table that "won" the coverage comparison and still
            # leaves figures on the floor is exactly as half-read as one
            # that lost it.
            value_bands = set(binding.col_map.values())
            unaccounted = [
                t for t in binding.leftover
                if t.column in value_bands and not t.interpolated
                and (t.confidence is None or t.confidence >= 0.60)
            ]
            if unaccounted:
                sample = ", ".join(t.text for t in unaccounted[:3])
                more = f", and {len(unaccounted) - 3} more" if len(unaccounted) > 3 else ""
                notes.append(
                    f"Table {index} on page {converted_table.page_no}: "
                    f"{len(unaccounted)} figure(s) printed inside this table's region "
                    f"({sample}{more}) are not accounted for by any row or column of "
                    "the extracted table. This table may be only partly read -- check "
                    "it against the original scan."
                )
            if binding.unanchored_rows:
                notes.append(
                    f"Table {index} on page {converted_table.page_no}: "
                    f"{len(binding.unanchored_rows)} row(s) could not be located in "
                    "the page's own OCR text, so their figures had no independent "
                    "position to check against; each is re-read individually and "
                    "shown only where that re-read agreed."
                )

        # One bounded, DOCUMENT-WIDE wave of targeted re-reads for every
        # UNBOUND cell that claims a number -- the escape hatch for a figure
        # the chosen structure states but no OCR token backs at that
        # position. Same cross-table batching reasoning as Pass 3b below:
        # one shared budget and one bounded pool for the whole document.
        all_unbound = [
            (p_index, r, c)
            for p_index, p in enumerate(prepared)
            if p.get("binding") is not None
            for (r, c) in p["binding"].unbound
            if p["table"].cell(r, c).value is not None
        ]
        binding_budget = all_unbound[: Config.VLM_MAX_BINDING_REREADS]
        if len(all_unbound) > len(binding_budget):
            notes.append(
                f"{len(all_unbound) - len(binding_budget)} cell(s) with a figure the "
                "page's own OCR text did not confirm were left withheld for BUDGET "
                f"reasons (the {Config.VLM_MAX_BINDING_REREADS}-re-read-per-document "
                "cap, INGEST_VLM_MAX_BINDING_REREADS) rather than for lack of evidence."
            )

        def _run_binding_reread(p_index: int, row: int, col: int):
            p = prepared[p_index]
            page_image = image_by_number.get(p["converted_table"].page_no)
            confirmed = vlm_read.resolve_unbound_cell(
                p["table"], row, col, page_image, p["converted_table"],
            )
            return p_index, row, col, confirmed

        for p in prepared:
            p["unsupported"] = set()
        if binding_budget and vlm_available:
            with ThreadPoolExecutor(max_workers=Config.VLM_RESCUE_CONCURRENCY) as pool:
                futures = [pool.submit(_run_binding_reread, *req) for req in binding_budget]
                for future in futures:
                    try:
                        p_index, row, col, confirmed = future.result()
                    except Exception:
                        logger.exception("VLM number-binding escape-hatch read failed")
                        continue
                    if not confirmed:
                        prepared[p_index]["unsupported"].add((row, col))
        elif binding_budget:
            # The VLM is unreachable, so the escape hatch cannot run at all
            # -- every unbound cell it would have tried stays withheld for
            # lack of a second opinion, same degradation as everywhere else
            # a missing VLM narrows what this pipeline will vouch for.
            for p_index, row, col in binding_budget:
                prepared[p_index]["unsupported"].add((row, col))
    else:
        for p in prepared:
            p["structure_replaced"] = False
            p["unsupported"] = set()

    # Pass 3a: draft every table's verification -- baseline footings plus
    # every doubtful cell classified, but NOTHING emitted yet (see
    # verify.draft_table). Kept separate from resolving so a targeted rescue
    # call for a cell nothing else can recover (Pass 3b, next) can still run
    # BEFORE any finding or marker is built, exactly like the whole-table VLM
    # pass above is separated from parsing for the same reason: the slow,
    # independent network calls should not force the document to be processed
    # one table at a time.
    drafts: list[dict] = []
    # Shared per-DOCUMENT budget for band rescues (Config.VLM_MAX_BAND_RESCUES)
    # -- a different, more expensive call than the per-cell rescue below, so
    # it gets its own running total across every table in this document.
    band_rescue_budget = Config.VLM_MAX_BAND_RESCUES if Config.VLM_BAND_RESCUE_ENABLED else 0
    for p in prepared:
        index = p["index"]
        converted_table = p["converted_table"]
        table = p["table"]

        agreement = None
        alignment_grounded = False
        disagreements: set[tuple[int, int]] = set()
        unmatched: set[int] = set()
        vlm_only: set[int] = set()
        # The VLM's own text for each disagreeing cell, keyed exactly as
        # verify.py's readers_disagree tier needs it -- this is what lets
        # that tier try the VLM's candidate in the footing arithmetic
        # instead of only withholding docling's disagreeing figure.
        vlm_cell_text: dict[tuple[int, int], str] = {}

        # Whether Pass 2b (above) already replaced this table's grid with an
        # independently-bound structure. If so, every later step in this
        # loop (compare/merge/insert-unclaimed/band-rescue) must be skipped
        # for it -- they all reason about docling's grid, which binding has
        # just discarded in favour of one bound against the page's own OCR
        # numbers. Deliberately NOT marked `vlm_only`: unlike an ordinary
        # whole-table VLM splice, every figure in a bound table is either
        # bound to an OCR token at a consistent position, deterministically
        # gap-filled from OCR geometry, or confirmed by an agreeing targeted
        # re-read (Pass 2b's escape hatch) -- strictly stronger evidence
        # than `vlm_only_row` implies, and marking it that way made
        # arithmetic promotion structurally impossible (see
        # verify.py's Phase C: the overlay candidate for a vlm_only cell IS
        # its own text, so the overlay footing signature always equals the
        # baseline one and is skipped). Anything in this table that is NOT
        # one of those three is in `unsupported_cells` instead.
        structure_replaced = p.get("structure_replaced", False)

        if p["needs_vlm"] and not structure_replaced:
            second = vlm_results.get(index)
            if second:
                alignment = vlm_read.compare(table, second)
                # An unusable alignment is NOT a table full of disagreements.
                # Absent, truncated or structurally incomparable reads leave
                # both sets empty so the table is verified on arithmetic
                # alone -- the same degradation as an unreachable model.
                # Treating it as disagreement would blank a whole schedule
                # over a dropped connection.
                if alignment.usable:
                    agreement = alignment.agreement
                    alignment_grounded = alignment.grounded
                    disagreements = alignment.disagreements
                    unmatched = alignment.unmatched_rows

                    # Rejoin a wrapped label with the row carrying its
                    # figures FIRST. Both this and insert_unclaimed_rows
                    # below MUTATE table.rows and shift row indices, and
                    # insert_unclaimed_rows reads alignment.pairs /
                    # alignment.unmatched_rows DIRECTLY -- so those have to be
                    # remapped here too, not just the pipeline's own copies,
                    # or the second call would build its own map against rows
                    # that no longer exist.
                    merged, merge_remap = vlm_read.merge_wrapped_labels(
                        table, alignment, converted_table.ocr_lines,
                        structure_repair._header_bottom(converted_table.cells),
                    )
                    if merged:
                        alignment.pairs = {
                            merge_remap[r]: j for r, j in alignment.pairs.items()
                        }
                        alignment.unmatched_rows = {
                            merge_remap[r] for r in alignment.unmatched_rows
                        }
                        disagreements = {(merge_remap[r], c) for r, c in disagreements}
                        unmatched = {merge_remap[r] for r in unmatched}
                        notes.append(
                            f"Table {index} on page {converted_table.page_no}: "
                            f"{len(merged)} row(s) whose printed label wrapped "
                            "onto a second line were rejoined with the row "
                            "carrying their figures, confirmed against the "
                            "vision model's independent read. No figure changed."
                        )

                    # Splice in any row the vision model found that docling
                    # never emitted at all -- see insert_unclaimed_rows. Same
                    # index-shifting obligation as above.
                    inserted, remap = vlm_read.insert_unclaimed_rows(table, alignment)
                    if inserted:
                        disagreements = {(remap[r], c) for r, c in disagreements}
                        unmatched = {remap[r] for r in unmatched}
                        vlm_only = set(inserted)
                        notes.append(
                            f"Table {index} on page {converted_table.page_no}: "
                            f"{len(inserted)} row(s) found only by the vision "
                            "model's independent read were added; their figures "
                            "show only where the column's own arithmetic "
                            "confirms them."
                        )

                    for (r, c) in disagreements:
                        j = alignment.pairs.get(r)
                        vc = alignment.col_map.get(c)
                        if (j is not None and vc is not None
                                and j < len(alignment.vlm_body) and vc < len(alignment.vlm_body[j])):
                            vlm_cell_text[(r, c)] = alignment.vlm_body[j][vc]
                else:
                    notes.append(
                        f"Table {index} on page {converted_table.page_no}: the "
                        f"second read could not be compared ({alignment.reason}), "
                        "so its figures rest on their own arithmetic only."
                    )

        # Split any row that looks like SEVERAL real line items TableFormer
        # merged into one (see vlm_read._looks_merged) -- a different defect
        # from a single unreadable cell, and one a whole-table Alignment
        # cannot fix even when it succeeded above (a garbled multi-item
        # label practically never pairs cleanly in compare() anyway).
        # Independent of whether the whole-table read above ran or
        # succeeded: this is its own targeted call, on its own budget.
        # Deliberately LAST of the three structural repairs -- like
        # insert_unclaimed_rows above, it shifts row indices, so every set
        # built against `table` before this point must be remapped through
        # it or dropped.
        if (
            Config.VLM_BAND_RESCUE_ENABLED
            and vlm_available
            and band_rescue_budget > 0
            and not structure_replaced
        ):
            page_image_for_band = image_by_number.get(converted_table.page_no)
            split_rows, split_remap, split_notes, attempted = vlm_read.split_merged_rows(
                table, converted_table, page_image_for_band, max_attempts=band_rescue_budget,
            )
            band_rescue_budget -= attempted
            if split_rows:
                disagreements = {
                    (split_remap[r], c) for r, c in disagreements if r in split_remap
                }
                unmatched = {split_remap[r] for r in unmatched if r in split_remap}
                vlm_only = {split_remap[r] for r in vlm_only if r in split_remap} | split_rows
                vlm_cell_text = {
                    (split_remap[r], c): text
                    for (r, c), text in vlm_cell_text.items() if r in split_remap
                }
                notes.extend(split_notes)

        # Empty cells the page proves held a figure. Computed HERE, on the
        # FINAL table, because everything above (the wrapped-label joins,
        # inserted VLM rows, band splits) shifts row indices -- the
        # classifier reads the table as it now stands, so its cell
        # coordinates are valid for `draft_table` with no remap.
        lost_cells: set[tuple[int, int]] = set()
        if Config.LABEL_ONLY_ROW_REPORTING:
            _, lost_cells = structure_repair.classify_label_only_rows(table, converted_table)
            if lost_cells:
                notes.append(
                    f"Table {index} on page {converted_table.page_no}: "
                    f"{len(lost_cells)} cell(s) whose figure the page's own OCR "
                    "text shows but the extracted grid left empty. They are "
                    "withheld and re-read rather than shown as blank -- a "
                    "blank reads as a nil balance."
                )

        page_quality = page_by_number.get(converted_table.page_no)
        draft = verify.draft_table(
            table,
            converted_table.page_no,
            vlm_disagreements=disagreements,
            ocr_score=page_quality.ocr_score if page_quality else None,
            unmatched_rows=unmatched,
            vlm_only_rows=vlm_only,
            vlm_cell_text=vlm_cell_text,
            unsupported_cells=p.get("unsupported", set()),
            lost_figure_cells=lost_cells,
        )
        drafts.append({
            "index": index,
            "converted_table": converted_table,
            "table": table,
            "agreement": agreement,
            "alignment_grounded": alignment_grounded,
            "vlm_cell_text": vlm_cell_text,
            "draft": draft,
        })

    # Pass 3b: one bounded, DOCUMENT-WIDE batch of targeted rescue calls for
    # every cell Pass 3a could not already resolve (unreadable_text,
    # low_ocr_confidence, no_second_read_for_row -- see verify.draft_table's
    # rescue_requests). Batched across every table, not per table, for the
    # same reason the whole-table pass above is: one shared budget and one
    # bounded pool for the whole document, rather than N independent pools
    # each able to spike the shared endpoint.
    rescued_by_draft: dict[int, dict[tuple[int, int], "vlm_read.RescueResult"]] = {}
    if vlm_available and Config.VLM_RESCUE_ENABLED:
        all_requests: list[tuple[int, int, int, str, str]] = [
            (d_index, row, col, row_label, column_name)
            for d_index, d in enumerate(drafts)
            for (row, col, row_label, column_name) in d["draft"].rescue_requests
        ]
        budget = all_requests[: Config.VLM_MAX_RESCUES]
        if len(all_requests) > len(budget):
            notes.append(
                f"{len(all_requests) - len(budget)} cell(s) needing a targeted "
                f"second-chance read were left withheld for BUDGET reasons (the "
                f"{Config.VLM_MAX_RESCUES}-rescue-per-document cap, "
                "INGEST_VLM_MAX_RESCUES) rather than for lack of evidence."
            )

        def _run_rescue(d_index: int, row: int, col: int, row_label: str, column_name: str):
            d = drafts[d_index]
            converted_table = d["converted_table"]
            table = d["table"]
            page_image = image_by_number.get(converted_table.page_no)
            no_candidate = vlm_read.RescueResult(text=None, anchored=False)
            if page_image is None:
                return d_index, row, col, no_candidate

            image = vlm_read.row_band(page_image, converted_table, row_label)
            if image is None:
                # Fall back to the same whole-table crop the table-level pass
                # already prepared, with the same targeted instruction naming
                # the row/column -- weaker (more for the model to search
                # within) but better than no rescue attempt at all.
                image = next(
                    (p["crop"] for p in prepared if p["index"] == d["index"]), None,
                )
            if image is None:
                return d_index, row, col, no_candidate

            reply = vlm_read.transcribe_row(image, row_label, column_name)
            if not reply:
                return d_index, row, col, no_candidate
            cells = vlm_read.parse_rescue_row(reply)
            if not cells:
                return d_index, row, col, no_candidate

            # ANCHOR the read against the row's OTHER already-trusted figures
            # -- never shown to the model -- before accepting anything it
            # said about the cell actually asked about. Not accepted at all
            # (text stays None) when anchoring fails, INCLUDING when there
            # was no anchor to check against: honest degradation, not blind
            # trust. See check_rescue_anchors's docstring.
            anchored = vlm_read.check_rescue_anchors(table, row, col, cells)
            if not anchored:
                return d_index, row, col, no_candidate
            text = vlm_read.rescue_cell_text(table, row, col, cells)
            return d_index, row, col, vlm_read.RescueResult(text=text, anchored=True)

        if budget:
            with ThreadPoolExecutor(max_workers=Config.VLM_RESCUE_CONCURRENCY) as pool:
                futures = [pool.submit(_run_rescue, *request) for request in budget]
                for future in futures:
                    try:
                        d_index, row, col, result = future.result()
                    except Exception:
                        logger.exception("VLM targeted rescue read failed")
                        continue
                    rescued_by_draft.setdefault(d_index, {})[(row, col)] = result

    rescue_stats = RecoveryStats(
        requested=len(all_requests) if vlm_available and Config.VLM_RESCUE_ENABLED else 0,
        attempted=len(budget) if vlm_available and Config.VLM_RESCUE_ENABLED else 0,
        succeeded=sum(
            1 for by_cell in rescued_by_draft.values()
            for result in by_cell.values() if result.text is not None
        ),
    )

    # Pass 3b-2: closed-world resolution for every `readers_disagree` cell
    # that already has two candidates (see verify.draft_table's
    # disagreement_requests) -- force a choice between EXACTLY those two
    # readings rather than trusting the VLM's reading directly. Same
    # document-wide batching reasoning as Pass 3b: one shared budget, one
    # bounded pool, not N independent pools each hitting the shared endpoint.
    closed_world_by_draft: dict[int, dict[tuple[int, int], str | None]] = {}
    if vlm_available and Config.VLM_DISAGREEMENT_RESOLUTION_ENABLED:
        all_disagreements: list[tuple[int, int, int, str, str, str, str]] = [
            (d_index, row, col, row_label, column_name, candidate_a, candidate_b)
            for d_index, d in enumerate(drafts)
            for (row, col, row_label, column_name, candidate_a, candidate_b)
            in d["draft"].disagreement_requests
        ]
        disagreement_budget = all_disagreements[:Config.VLM_MAX_DISAGREEMENT_RESOLUTIONS]
        if len(all_disagreements) > len(disagreement_budget):
            notes.append(
                f"{len(all_disagreements) - len(disagreement_budget)} cell(s) where two "
                "readers disagreed were left with the vision model's own reading, "
                "unconfirmed, for BUDGET reasons (the "
                f"{Config.VLM_MAX_DISAGREEMENT_RESOLUTIONS}-per-document cap, "
                "INGEST_VLM_MAX_DISAGREEMENT_RESOLUTIONS)."
            )

        def _run_disagreement(
            d_index: int, row: int, col: int, row_label: str, column_name: str,
            candidate_a: str, candidate_b: str,
        ):
            d = drafts[d_index]
            image = next((p["crop"] for p in prepared if p["index"] == d["index"]), None)
            if image is None:
                return d_index, row, col, None
            choice = vlm_read.resolve_disagreement(
                image, row_label, column_name, [candidate_a, candidate_b],
            )
            return d_index, row, col, choice

        if disagreement_budget:
            with ThreadPoolExecutor(max_workers=Config.VLM_RESCUE_CONCURRENCY) as pool:
                futures = [pool.submit(_run_disagreement, *request) for request in disagreement_budget]
                for future in futures:
                    try:
                        d_index, row, col, choice = future.result()
                    except Exception:
                        logger.exception("VLM closed-world disagreement resolution failed")
                        continue
                    closed_world_by_draft.setdefault(d_index, {})[(row, col)] = choice

    disagreement_stats = RecoveryStats(
        requested=(
            len(all_disagreements)
            if vlm_available and Config.VLM_DISAGREEMENT_RESOLUTION_ENABLED else 0
        ),
        attempted=(
            len(disagreement_budget)
            if vlm_available and Config.VLM_DISAGREEMENT_RESOLUTION_ENABLED else 0
        ),
        succeeded=sum(
            1 for by_cell in closed_world_by_draft.values()
            for choice in by_cell.values() if choice is not None
        ),
    )

    # Pass 3c: resolve every table's recovery (arithmetic promotion first,
    # then whatever stays display-only), apply what got promoted, redact
    # what did not, and assemble records -- in ORIGINAL TABLE ORDER, not in
    # whatever order Pass 3b's futures happened to finish, since notes and
    # the eventual table list both need to read top-to-bottom the way the
    # document does.
    last_partial_at = time.monotonic()
    for d_index, d in enumerate(drafts):
        index = d["index"]
        converted_table = d["converted_table"]
        table = d["table"]

        checks, findings, recovered = verify.resolve_recoveries(
            d["draft"],
            vlm_cell_text=d["vlm_cell_text"],
            alignment_grounded=d["alignment_grounded"],
            alignment_agreement=d["agreement"],
            rescued=rescued_by_draft.get(d_index, {}),
            closed_world_choice=closed_world_by_draft.get(d_index, {}),
        )
        # Promoted figures' own text must be in the table BEFORE redact,
        # which only ever touches cells named in `findings` -- a promoted
        # cell is never in that list, by construction.
        verify.apply_recoveries(table, recovered)
        # Redact BEFORE building the record: the record carries the markdown the
        # model will eventually read, and an unverified figure must not be in it.
        verify.redact(table, findings)

        if recovered:
            notes.append(
                f"Table {index} on page {converted_table.page_no}: "
                f"{len(recovered)} figure(s) recovered from a second read and "
                "CONFIRMED by the column's own arithmetic -- shown as plain "
                "numbers, exactly like a cleanly-read figure."
            )

        # Current/Previous-year column-order check -- a SIGNAL, never a fix
        # (see structure_repair.detect_year_column_order's own docstring for
        # why this refuses to reorder anything itself). Run after redact, not
        # before: the check reads the table's own header text as it stands in
        # the FINAL emitted record, not an intermediate draft that redaction
        # might still change the shape of.
        year_order_warning = structure_repair.detect_year_column_order(table)
        if year_order_warning:
            notes.append(
                f"Table {index} on page {converted_table.page_no}: {year_order_warning}. "
                "Figures are shown as extracted; verify the year columns against the "
                "original scan before relying on this table's current/previous-year split."
            )

        p = next((p for p in prepared if p["index"] == index), None)
        records.append(emit.build_table_record(
            table=table,
            doc_id=doc_id,
            page_no=converted_table.page_no,
            surrounding_text=converted.page_markdown.get(converted_table.page_no, ""),
            footings=checks,
            findings=findings,
            bbox=converted_table.bbox,
            vlm_agreement=d["agreement"],
            snippet_jpeg_b64=p["snippet"] if p else None,
            source_file=filename,
            recovered=recovered,
        ))

        # Throttled, not per-table: on a 60+ table filing this loop does no
        # network I/O (the VLM/rescue calls that do already ran earlier), so
        # WITHOUT a throttle this would fire an SSE frame per table -- needless
        # traffic for a consumer that only wants "roughly how much is ready".
        # The final table always fires regardless of the interval (`is_last`),
        # so a caller never waits past the loop's own end for the complete set.
        if on_partial is not None:
            now = time.monotonic()
            is_last = d_index == len(drafts) - 1
            if is_last or (now - last_partial_at) >= Config.PARTIAL_RESULT_INTERVAL_SECONDS:
                on_partial(doc_id, [r.as_dict() for r in records])
                last_partial_at = now

    # Cross-page continuation check -- a SIGNAL, never a merge (see
    # structure_repair.detect_cross_page_continuation's own docstring). Run
    # over `drafts` in document order, after every table's own redact/apply
    # above, so it reads each table's FINAL shape. Adjacent in `drafts`
    # means adjacent in reading order; the function itself refuses unless
    # the two tables' page numbers are exactly consecutive.
    for prev_d, next_d in zip(drafts, drafts[1:]):
        continuation_warning = structure_repair.detect_cross_page_continuation(
            prev_d["table"], next_d["table"],
        )
        if continuation_warning:
            notes.append(
                f"Table {prev_d['index']} on page {prev_d['converted_table'].page_no} / "
                f"Table {next_d['index']} on page {next_d['converted_table'].page_no}: "
                f"{continuation_warning}. Shown as two separate tables; verify any closing "
                "total against both before relying on either one alone."
            )

    # ---- page images -------------------------------------------------------
    #
    # One JPEG per kept page (blank/duplicate pages are already excluded from
    # `kept_qualities`), so the document pane can show the reader exactly what
    # OCR/docling saw. Built from `image_by_number` -- the already-corrected
    # `kept_images` bitmaps, in hand above at zero extra rendering cost --
    # rather than asking docling for its own `document.pages[n].image` (enabled
    # via `generate_page_images=True` in convert.py but read nowhere today).
    # `image_by_number` is exactly what OCR/docling actually looked at and is
    # keyed by the same `page_no` tables and texts already use; docling's own
    # page images would be a second, redundant re-rasterisation of that same
    # bitmap and would reintroduce the page-numbering ambiguity
    # `_absorb_confidence` above already has to work around.
    page_images = [
        emit.build_page_image(q.page_no, image_by_number.get(q.page_no))
        for q in kept_qualities
        if image_by_number.get(q.page_no) is not None
    ]

    # ---- identify --------------------------------------------------------
    #
    # Runs BEFORE the narrative chunks are built, not after: the chunk builder
    # needs the statement flavour to root each breadcrumb (standalone vs
    # consolidated is a sort key on every narrative query) and the entity name
    # to recognise the running page header and not mistake it for a section.
    advance("identify", "Working out the entity, year and framework")
    pages_text = [converted.page_markdown[p] for p in sorted(converted.page_markdown)]
    if not pages_text:
        pages_text = [converted.markdown]
    identification = identify_mod.identify(pages_text)

    texts = emit.build_text_records(
        doc_id,
        converted.page_markdown,
        flavour=identification.statement_flavour,
        entity_name=identification.entity_name,
        source_file=filename,
    )

    # Close out the LAST stage's own duration -- `advance()` only records
    # the PREVIOUS stage's duration when the NEXT one starts, so "identify"
    # (the final stage) never gets one from `advance()` alone.
    if _current_stage is not None:
        stage_durations.append(
            StageDuration(_current_stage, round(time.monotonic() - _stage_start, 3))
        )

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
        rescue_stats=rescue_stats,
        disagreement_stats=disagreement_stats,
        gap_fill_stats=gap_fill_stats,
    )
