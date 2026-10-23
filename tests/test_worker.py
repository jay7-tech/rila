from sqlalchemy import select

from app.models import Item, Job
from app.services import worker
from app.services.extract import ExtractedItem, ExtractionResult
from app.services.ingest import IngestError, Ingested
from app import formatting as fmt

from conftest import HERE, NOW


def patch_pipeline(monkeypatch, ingest, items=()):
    monkeypatch.setattr(worker.ingest_svc, "ingest", ingest)
    monkeypatch.setattr(worker.extract_svc, "extract", lambda c, t, n, p: ExtractionResult(items=list(items)))
    monkeypatch.setattr(worker.geo_svc, "geocode_place", lambda s, n, w: (HERE[0], HERE[1], "confirmed"))
    monkeypatch.setattr(worker.geo_svc, "geocode_area", lambda s, w: (HERE[0], HERE[1], "approx"))


ITEM = ExtractedItem(kind="offer", category="food", title="BOGO", venue="Cafe Nine", city="Bengaluru", confidence=0.9)


def test_claim_is_one_at_a_time_and_in_order(session, user):
    a = worker.enqueue(session, user, "https://youtu.be/a")
    b = worker.enqueue(session, user, "https://youtu.be/b")
    session.commit()
    assert worker.claim_next(session).id == a.id
    assert worker.claim_next(session).id == b.id
    assert worker.claim_next(session) is None


def test_successful_job_saves_item_and_summary(session, user, monkeypatch):
    patch_pipeline(monkeypatch, lambda url: Ingested("c", "t", "@x", None), [ITEM])
    job = worker.enqueue(session, user, "https://youtu.be/a")
    session.commit()
    job = worker.claim_next(session)
    summary = worker.run_job(session, job, NOW)
    assert job.status == "done" and summary["saved"][0]["title"] == "BOGO"
    assert session.scalars(select(Item)).one().title == "BOGO"
    assert "Saved 1" in fmt.saved_summary(summary)


def test_user_facing_error_is_recorded(session, user, monkeypatch):
    def boom(url):
        raise IngestError("I couldn't open that video.")
    patch_pipeline(monkeypatch, boom)
    worker.enqueue(session, user, "https://youtu.be/a")
    session.commit()
    job = worker.claim_next(session)
    summary = worker.run_job(session, job, NOW)
    assert job.status == "failed" and job.error == "I couldn't open that video." and summary["error"] == job.error


def test_unexpected_error_does_not_crash_or_leak_details(session, user, monkeypatch):
    def boom(url):
        raise RuntimeError("secret stack detail")
    patch_pipeline(monkeypatch, boom)
    worker.enqueue(session, user, "https://youtu.be/a")
    session.commit()
    job = worker.claim_next(session)
    worker.run_job(session, job, NOW)
    saved = session.get(Job, job.id)
    assert saved.status == "failed" and "secret" not in saved.error


def test_empty_result_message():
    assert "couldn't find anything" in fmt.saved_summary({"saved": [], "duplicates": [], "signals": 0, "rejected": []})


import asyncio

from app.services import worker as worker_mod


def test_notify_retries_then_succeeds():
    calls = []

    async def flaky(job, summary):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("network")

    job = type("J", (), {"id": 1, "chat_id": 5})()
    assert asyncio.run(worker_mod.notify_with_retry(flaky, job, {}, delays=(0, 0, 0, 0))) is True
    assert len(calls) == 3


def test_notify_gives_up_without_raising():
    async def down(job, summary):
        raise RuntimeError("network")

    job = type("J", (), {"id": 1, "chat_id": 5})()
    assert asyncio.run(worker_mod.notify_with_retry(down, job, {}, delays=(0, 0))) is False
