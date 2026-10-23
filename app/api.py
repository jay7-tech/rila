"""HTTP API and static web app."""
from __future__ import annotations

import os
import re
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Item, Job, User, utcnow
from .services import query as q
from .services import users as users_svc
from .services.worker import enqueue

URL_RE = re.compile(r"^https?://[^\s]+$")
ALLOWED_HOSTS = ("instagram.com", "youtube.com", "youtu.be")
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def current_user(
    db: Session = Depends(get_db),
    x_rilla_token: Optional[str] = Header(default=None),
    t: Optional[str] = Query(default=None),
) -> User:
    user = users_svc.get_by_token(db, x_rilla_token or t or "")
    if user is None:
        raise HTTPException(status_code=401, detail="Open the web app from the link the Telegram bot sent you (/web).")
    return user


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class SubmitIn(BaseModel):
    url: str = Field(max_length=500)


def create_app(lifespan=None) -> FastAPI:
    app = FastAPI(title="Rilla", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    def health():
        return {"ok": True}

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

    @app.get("/api/items")
    def items(
        category: Optional[str] = None, q_text: Optional[str] = Query(default=None, alias="q"),
        lat: Optional[float] = None, lon: Optional[float] = None, include_expired: bool = False,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        return {"items": q.list_saved(db, user, utcnow(), category, q_text, lat, lon, include_expired)}

    @app.get("/api/search")
    def search(
        q_text: str = Query(min_length=2, max_length=120, alias="q"),
        lat: Optional[float] = None, lon: Optional[float] = None, limit: int = Query(default=10, ge=1, le=30),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        return {"items": q.search_saved(db, user, q_text, utcnow(), lat, lon, limit)}

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
        url = body.url.strip()
        if not URL_RE.match(url) or not any(h in url.lower() for h in ALLOWED_HOSTS):
            raise HTTPException(status_code=422, detail="Send an Instagram or YouTube link.")
        pending = db.scalar(select(Job.id).where(Job.user_id == user.id, Job.status.in_(("queued", "running"))).limit(5).offset(4))
        if pending is not None:
            raise HTTPException(status_code=429, detail="You already have 5 reels in the queue. Try again in a minute.")
        job = enqueue(db, user, url)
        db.commit()
        return {"job_id": job.id, "status": job.status}

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
