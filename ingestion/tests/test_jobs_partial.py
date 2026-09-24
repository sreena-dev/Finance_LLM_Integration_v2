"""`jobs.PartialResult` and the (kind, payload) shape `Job`'s subscriber
queue now carries -- the plumbing Phase 2's progressive-availability feature
needs before a document is fully converted.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_jobs_partial.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.jobs import Event, Job, PartialResult              # noqa: E402


def _table(table_id: str = "t1") -> dict:
    return {"table_id": table_id, "table_md": "| a |"}


# --------------------------------------------------------------------------
# PartialResult.as_dict()
# --------------------------------------------------------------------------

def test_partial_result_carries_doc_id_and_tables():
    partial = PartialResult(doc_id="up_abc", tables=[_table()])

    assert partial.as_dict() == {
        "doc_id": "up_abc", "tables": [_table()], "complete": False,
    }


def test_partial_result_never_claims_complete():
    # `complete: False` is not a caller-settable field -- a partial snapshot
    # must never be mistaken for the terminal result by anything reading
    # the SSE stream literally.
    partial = PartialResult(doc_id="up_abc", tables=[])
    assert partial.as_dict()["complete"] is False


# --------------------------------------------------------------------------
# Job.publish / publish_partial -- what reaches a live subscriber
# --------------------------------------------------------------------------

def test_publish_queues_a_progress_tagged_item():
    job = Job(job_id="j1", filename="a.pdf")
    subscriber = job.subscribe()

    job.publish(Event("convert", "Detecting layout", 0.3))

    kind, payload = subscriber.get_nowait()
    assert kind == "progress"
    assert payload.stage == "convert"


def test_publish_partial_queues_a_partial_tagged_item():
    job = Job(job_id="j1", filename="a.pdf")
    subscriber = job.subscribe()

    job.publish_partial(PartialResult("up_abc", [_table()]))

    kind, payload = subscriber.get_nowait()
    assert kind == "partial"
    assert payload.tables == [_table()]


def test_publish_partial_updates_the_jobs_latest_snapshot():
    job = Job(job_id="j1", filename="a.pdf")

    job.publish_partial(PartialResult("up_abc", [_table("t1")]))
    job.publish_partial(PartialResult("up_abc", [_table("t1"), _table("t2")]))

    assert job.partial is not None
    assert [t["table_id"] for t in job.partial.tables] == ["t1", "t2"]


# --------------------------------------------------------------------------
# Job.subscribe() replay -- a late subscriber sees history + latest partial
# --------------------------------------------------------------------------

def test_a_late_subscriber_replays_every_progress_event():
    job = Job(job_id="j1", filename="a.pdf")
    job.publish(Event("render", "Reading the PDF", 0.0))
    job.publish(Event("precheck", "Checking quality", 0.05))

    subscriber = job.subscribe()

    first = subscriber.get_nowait()
    second = subscriber.get_nowait()
    assert first == ("progress", job.events[0])
    assert second == ("progress", job.events[1])


def test_a_late_subscriber_replays_only_the_latest_partial_not_every_one():
    job = Job(job_id="j1", filename="a.pdf")
    job.publish_partial(PartialResult("up_abc", [_table("t1")]))
    job.publish_partial(PartialResult("up_abc", [_table("t1"), _table("t2")]))

    subscriber = job.subscribe()

    kind, payload = subscriber.get_nowait()
    assert kind == "partial"
    assert [t["table_id"] for t in payload.tables] == ["t1", "t2"]
    assert subscriber.empty()  # not a second, older partial behind it


def test_a_subscriber_to_a_finished_job_gets_the_end_marker_immediately():
    job = Job(job_id="j1", filename="a.pdf")
    job.status = "done"

    subscriber = job.subscribe()

    assert subscriber.get_nowait() is None
    # And is NOT registered to receive further pushes -- a finished job's
    # publish() calls (there should be none, but defensively) must not
    # raise trying to reach it.
    assert subscriber not in job.subscribers


# --------------------------------------------------------------------------
# Job.as_dict() -- the non-streaming poll() shape
# --------------------------------------------------------------------------

def test_as_dict_has_no_partial_key_before_one_is_published():
    job = Job(job_id="j1", filename="a.pdf")
    assert "partial" not in job.as_dict()


def test_as_dict_carries_the_latest_partial_once_published():
    job = Job(job_id="j1", filename="a.pdf")
    job.publish_partial(PartialResult("up_abc", [_table()]))

    payload = job.as_dict()
    assert payload["partial"] == {"doc_id": "up_abc", "tables": [_table()], "complete": False}


def test_as_dict_progress_is_unaffected_by_a_partial_publish():
    # `progress` must keep reading the latest PROGRESS event, not whatever
    # was queued most recently overall -- `events` and `partial` are
    # separate lists precisely so a partial publish cannot shadow it.
    job = Job(job_id="j1", filename="a.pdf")
    job.publish(Event("verify", "Checking figures", 0.9))
    job.publish_partial(PartialResult("up_abc", [_table()]))

    assert job.as_dict()["progress"]["stage"] == "verify"
