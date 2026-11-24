"""HTTP API and static web app."""
from __future__ import annotations

import hmac
import os
import time
import uuid
from collections import defaultdict, deque
from typing import Literal, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Document, Event, Item, Job, User, utcnow
from .perception import media
from .recs import learner
from .services import creators as creators_svc
from .services import demo as demo_svc
from .services import places as places_svc
from .services import query as q
from .services import users as users_svc
from .services.worker import enqueue
from .sources.safeurl import UrlRejected, validate_url

MAX_QUEUED = 5
CHUNK = 1024 * 1024
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def current_user(
    db: Session = Depends(get_db),
    x_rilla_token: Optional[str] = Header(default=None),
    t: Optional[str] = Query(default=None),
) -> User:
    user = users_svc.get_by_token(db, x_rilla_token or t or "")
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in first.")
    return user


class RateLimit:
    """Allow `limit` calls per `window` seconds for each key (in memory, per process)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window, self.hits = limit, window, defaultdict(deque)

    def check(self, key: str) -> None:
        now, q = time.monotonic(), self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
        q.append(now)


def client_key(request: Request) -> str:
    # Behind the Cloudflare tunnel every request comes from the proxy, so prefer its header.
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class DemoIn(BaseModel):
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)


class FeedbackIn(BaseModel):
    action: Literal["like", "dismiss", "visit", "open"]


class SubmitIn(BaseModel):
    url: str = Field(max_length=500)


class TextIn(BaseModel):
    text: str = Field(min_length=10, max_length=20000)


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    code: str = Field(default="", max_length=100)


def _check_queue(db: Session, user: User) -> None:
    pending = db.scalar(select(Job.id).where(Job.user_id == user.id, Job.status.in_(("queued", "running"))).limit(1).offset(MAX_QUEUED - 1))
    if pending is not None:
        raise HTTPException(status_code=429, detail=f"You already have {MAX_QUEUED} items in the queue. Try again in a minute.")


def create_app(lifespan=None) -> FastAPI:
    app = FastAPI(title="Rila", version="0.2.0", lifespan=lifespan)
    signup_limit, submit_limit = RateLimit(10, 3600), RateLimit(30, 600)
    feedback_limit = RateLimit(300, 600)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/config")
    def public_config():
        st = get_settings()
        return {
            "signup": "open" if st.open_signup else "invite" if st.invite_code else "closed",
            "max_upload_mb": st.max_upload_mb, "hosts": list(st.allowed_hosts),
        }

    @app.post("/api/signup")
    def signup(body: SignupIn, request: Request, db: Session = Depends(get_db)):
        """Create a web account without Telegram. Closed unless OPEN_SIGNUP=1 or an INVITE_CODE is set."""
        signup_limit.check(client_key(request))
        st = get_settings()
        ok = st.open_signup or (bool(st.invite_code) and hmac.compare_digest(body.code.encode(), st.invite_code.encode()))
        if not ok:
            raise HTTPException(status_code=403, detail="Sign-up needs a valid invite code.")
        user = users_svc.create_user(db, None, body.name.strip())
        db.commit()
        return {"token": user.token, "name": user.name}

    @app.get("/api/me")
    def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
        count = len(q.user_items(db, user))
        return {
            "name": user.name, "interests": user.interests or {}, "saved": count,
            "location": {"lat": user.last_lat, "lon": user.last_lon} if user.last_lat is not None else None,
        }

    @app.post("/api/location")
    def set_location(body: LocationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        users_svc.set_location(user, body.lat, body.lon)
        db.merge(user)
        db.commit()
        return {"ok": True}

    @app.get("/api/near")
    def near(
        lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
        radius: float = Query(default=3000, ge=100, le=50000),
        category: Optional[str] = None, limit: int = Query(default=20, ge=1, le=50),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        return {"items": q.recommend_for_user(db, user, lat, lon, utcnow(), radius, category, limit)}

    @app.get("/api/plan")
    def plan(
        lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
        minutes: int = Query(default=180, ge=30, le=720), mode: Literal["auto", "walk", "drive"] = "auto",
        category: Optional[str] = None, user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        """An ordered outing over saved places that fits the time available, with arrival estimates."""
        return q.plan_for_user(db, user, lat, lon, utcnow(), minutes, mode, category)

    @app.get("/api/items")
    def items(
        category: Optional[str] = None, q_text: Optional[str] = Query(default=None, alias="q"),
        lat: Optional[float] = None, lon: Optional[float] = None, include_expired: bool = False,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        return {"items": q.annotate(db, user, utcnow(), q.list_saved(db, user, utcnow(), category, q_text, lat, lon, include_expired))}

    @app.get("/api/search")
    def search(
        q_text: str = Query(min_length=2, max_length=120, alias="q"),
        lat: Optional[float] = None, lon: Optional[float] = None, limit: int = Query(default=10, ge=1, le=30),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        return {"items": q.search_saved(db, user, q_text, utcnow(), lat, lon, limit)}

    @app.get("/api/places")
    def places(
        q_text: Optional[str] = Query(default=None, alias="q", max_length=120),
        lat: Optional[float] = Query(default=None, ge=-90, le=90), lon: Optional[float] = Query(default=None, ge=-180, le=180),
        limit: int = Query(default=50, ge=1, le=100),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        """Saved venues merged across reels: one record per real place, with mentions, prices, offers and proof."""
        return {"places": places_svc.list_places(q.user_items(db, user), utcnow(), lat, lon, q_text, limit)}

    @app.post("/api/demo")
    def add_demo(body: DemoIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Add sample places and offers around the given point (or the last known location) so every screen can be tried."""
        feedback_limit.check(user.token)
        centre = (body.lat, body.lon) if body.lat is not None and body.lon is not None else None
        return {"added": demo_svc.seed_demo(db, user, utcnow(), centre)}

    @app.delete("/api/demo")
    def remove_demo(user: User = Depends(current_user), db: Session = Depends(get_db)):
        return {"removed": demo_svc.clear_demo(db, user)}

    @app.get("/api/creators")
    def creators(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Who Rila trusts among the creators you save from, and why (quotes verified, dropped, your reactions)."""
        return {"creators": creators_svc.creator_stats(db, user, utcnow())}

    @app.post("/api/items/{item_id}/feedback")
    def feedback(item_id: int, body: FeedbackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Record like / dismiss / visit / open. Rila learns interests from these and hides dismissed items from 'near me'."""
        item = db.get(Item, item_id)
        if item is None or item.user_id != user.id:
            raise HTTPException(status_code=404, detail="Not found")
        feedback_limit.check(user.token)
        db.add(Event(user_id=user.id, item_id=item.id, action=body.action, category=item.category, creator=item.creator))
        if body.action == "visit":
            item.visited = True
        db.commit()
        return {"ok": True}

    @app.get("/api/me/interests")
    def my_interests(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """What Rila has learned about this user, with the evidence, so nothing is hidden."""
        obs = [(e.action, e.category, e.created_at) for e in q.user_events(db, user)]
        return {"interests": learner.explain(user.interests or {}, obs, utcnow())}

    @app.get("/api/foryou")
    def foryou(lat: Optional[float] = Query(None, ge=-90, le=90), lon: Optional[float] = Query(None, ge=-180, le=180),
               limit: int = Query(12, ge=1, le=30), user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Feed ranked by a taste model trained from the user's own actions, diversified, with reasons and a reliability score."""
        return q.for_you(db, user, utcnow(), lat, lon, limit)

    @app.delete("/api/items/{item_id}")
    def delete_item(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = db.get(Item, item_id)
        if item is None or item.user_id != user.id:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(item)
        db.commit()
        return {"ok": True}

    @app.get("/api/briefing")
    def briefing(place: str = Query(min_length=2, max_length=80), user: User = Depends(current_user), db: Session = Depends(get_db)):
        return q.briefing_for(db, place, utcnow())

    @app.post("/api/submit", status_code=202)
    def submit(body: SubmitIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        submit_limit.check(user.token)
        try:
            url = validate_url(body.url, get_settings().allowed_hosts)
        except UrlRejected as e:
            raise HTTPException(status_code=422, detail=str(e))
        _check_queue(db, user)
        job = enqueue(db, user, url)
        db.commit()
        return {"job_id": job.id, "status": job.status}

    @app.post("/api/submit/text", status_code=202)
    def submit_text(body: TextIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        submit_limit.check(user.token)
        _check_queue(db, user)
        job = enqueue(db, user, "text", kind="text", payload=body.text.strip())
        db.commit()
        return {"job_id": job.id, "status": job.status}

    @app.post("/api/submit/file", status_code=202)
    def submit_file(
        request: Request, file: UploadFile = File(...),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        """A video, audio clip or screenshot. Checked with ffprobe (not the file name), stored under a random
        name, and deleted by the worker as soon as it has been read."""
        submit_limit.check(user.token)
        st = get_settings()
        limit = st.max_upload_mb * 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit + CHUNK:
            raise HTTPException(status_code=413, detail=f"Files can be up to {st.max_upload_mb} MB.")
        _check_queue(db, user)
        os.makedirs(st.uploads_dir, exist_ok=True)
        dest = os.path.join(st.uploads_dir, uuid.uuid4().hex)  # no extension, no user-controlled name
        size = 0
        try:
            with open(dest, "wb") as out:
                while True:
                    block = file.file.read(CHUNK)
                    if not block:
                        break
                    size += len(block)
                    if size > limit:
                        raise HTTPException(status_code=413, detail=f"Files can be up to {st.max_upload_mb} MB.")
                    out.write(block)
            if size == 0:
                raise HTTPException(status_code=422, detail="That file is empty.")
            try:
                media.probe(dest)
            except media.MediaError as e:
                raise HTTPException(status_code=422, detail=str(e))
        except BaseException:
            if os.path.exists(dest):
                os.remove(dest)
            raise
        label = "file:" + os.path.basename(file.filename or "upload")[:80]
        job = enqueue(db, user, label, kind="file", payload=dest)
        db.commit()
        return {"job_id": job.id, "status": job.status}

    @app.get("/api/items/{item_id}/source")
    def item_source(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Why Rila saved this: the quote, where it came from, and the lines around it."""
        item = db.get(Item, item_id)
        if item is None or item.user_id != user.id:
            raise HTTPException(status_code=404, detail="Not found")
        around = []
        doc = db.get(Document, item.document_id) if item.document_id else None
        if doc and item.evidence_at is not None:
            around = sorted((sg for sg in (doc.timeline or []) if sg.get("start") is not None and abs(sg["start"] - item.evidence_at) <= 6),
                            key=lambda sg: sg["start"])
        return {
            "evidence": item.evidence, "source": item.evidence_source, "at": item.evidence_at,
            "grounded": item.grounded, "language": doc.language if doc else None, "around": around[:12],
            "source_url": item.source_url,
        }

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        job = db.get(Job, job_id)
        if job is None or job.user_id != user.id:
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": job.id, "status": job.status, "result": job.result, "error": job.error}

    if os.path.isdir(WEB_DIR):
        @app.get("/")
        def index():
            return FileResponse(os.path.join(WEB_DIR, "index.html"))

        app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
    return app
