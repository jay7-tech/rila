"""Background worker: takes queued reels one at a time and processes them."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Job, User, utcnow
from . import embeddings as emb_svc
from . import extract as extract_svc
from . import geocode as geo_svc
from . import ingest as ingest_svc
from .process import ProcessResult, process_url

log = logging.getLogger(__name__)

Notifier = Callable[[Job, dict], Awaitable[None]]


def enqueue(session: Session, user: User, url: str, chat_id: Optional[int] = None) -> Job:
    job = Job(user_id=user.id, url=url, chat_id=chat_id, status="queued")
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session) -> Optional[Job]:
    """Atomically move the oldest queued job to running."""
    job_id = session.scalar(select(Job.id).where(Job.status == "queued").order_by(Job.id).limit(1))
    if job_id is None:
        return None
    res = session.execute(update(Job).where(Job.id == job_id, Job.status == "queued").values(status="running"))
    session.commit()
    if res.rowcount != 1:
        return None  # another worker took it
    return session.get(Job, job_id)


def summarize(result: ProcessResult) -> dict:
    return {
        "saved": [{"id": i.id, "title": i.title, "category": i.category, "kind": i.kind} for i in result.items],
        "duplicates": [{"id": i.id, "title": i.title} for i in result.duplicates],
        "signals": len(result.signals),
        "rejected": result.rejected,
        "expired": result.expired,
        "creator": result.creator,
    }


def run_job(session: Session, job: Job, now: Optional[datetime] = None) -> dict:
    """Process one claimed job and record the outcome on it."""
    now = now or utcnow()
    user = session.get(User, job.user_id)
    try:
        result = process_url(
            session, user, job.url, now,
            ingest_fn=ingest_svc.ingest,
            extract_fn=extract_svc.extract,
            geocode_place_fn=geo_svc.geocode_place,
            geocode_area_fn=geo_svc.geocode_area,
            embed_fn=lambda texts: emb_svc.embed_texts(texts),
        )
        summary = summarize(result)
        job.status, job.result, job.error = "done", summary, None
    except ingest_svc.IngestError as e:
        session.rollback()
        job = session.get(Job, job.id)
        job.status, job.error, summary = "failed", str(e), {"error": str(e)}
    except Exception as e:  # keep the worker alive on any bug or outage
        log.exception("job %s failed", job.id)
        session.rollback()
        job = session.get(Job, job.id)
        job.status, job.error = "failed", "Something went wrong while reading that reel. Please try again."
        summary = {"error": job.error}
    job.finished_at = utcnow()
    session.commit()
    return summary


NOTIFY_DELAYS = (0.0, 3.0, 10.0, 30.0)


async def notify_with_retry(notify: Notifier, job: Job, summary: dict, delays=NOTIFY_DELAYS) -> bool:
    """Telegram or the network can blip. Retry a few times; the result is already saved either way."""
    for attempt, delay in enumerate(delays, 1):
        if delay:
            await asyncio.sleep(delay)
        try:
            await notify(job, summary)
            return True
        except Exception as e:
            log.warning("notify for job %s failed (attempt %d/%d): %s", job.id, attempt, len(delays), type(e).__name__)
    log.error("could not notify chat %s about job %s; the items are saved (/saved)", job.chat_id, job.id)
    return False


async def worker_loop(notify: Optional[Notifier] = None, poll_seconds: float = 2.0) -> None:
    """Run until cancelled. Jobs left 'running' by a crash are re-queued first."""
    with SessionLocal() as s:
        s.execute(update(Job).where(Job.status == "running").values(status="queued"))
        s.commit()
    log.info("worker started")
    while True:
        try:
            with SessionLocal() as s:
                job = claim_next(s)
                if job is None:
                    await asyncio.sleep(poll_seconds)
                    continue
                summary = await asyncio.to_thread(run_job, s, job)
                if notify and job.chat_id:
                    await notify_with_retry(notify, job, summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker loop error")
            await asyncio.sleep(poll_seconds)
