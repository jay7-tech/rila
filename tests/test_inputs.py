import io
import os
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app import api as apimod
from app.core.document import Segment, VideoDocument
from app.models import Document, Item, Job
from app.perception.ocr import OcrLine
from app.services import worker
from app.services.extract import ExtractedItem, ExtractionResult
from app.services.ingest import Ingested, from_document
from app.sources import local

from conftest import HERE, NOW

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe not installed")


@pytest.fixture()
def client(session):
    return TestClient(apimod.create_app())


@pytest.fixture()
def h(user):
    return {"X-Rilla-Token": user.token}


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "up"))
    return tmp_path / "up"


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


# ---- sign-up without Telegram ------------------------------------------------------------------

def test_signup_is_closed_by_default(client):
    assert client.get("/api/config").json()["signup"] == "closed"
    assert client.post("/api/signup", json={"name": "Ravi"}).status_code == 403


def test_signup_with_invite_code(client, monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "letmein")
    assert client.get("/api/config").json()["signup"] == "invite"
    assert client.post("/api/signup", json={"name": "Ravi", "code": "wrong"}).status_code == 403
    r = client.post("/api/signup", json={"name": "Ravi", "code": "letmein"})
    assert r.status_code == 200
    assert client.get("/api/me", headers={"X-Rilla-Token": r.json()["token"]}).json()["name"] == "Ravi"


def test_open_signup_and_rate_limit(client, monkeypatch):
    monkeypatch.setenv("OPEN_SIGNUP", "1")
    codes = [client.post("/api/signup", json={"name": f"u{i}"}).status_code for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


# ---- text and file submissions -----------------------------------------------------------------

def test_submit_text_queues_a_text_job(client, session, user, h):
    assert client.post("/api/submit/text", json={"text": "short"}, headers=h).status_code == 422
    r = client.post("/api/submit/text", json={"text": "Free dosa at MTR this Sunday in Lalbagh"}, headers=h)
    assert r.status_code == 202
    job = session.get(Job, r.json()["job_id"])
    assert job.kind == "text" and job.payload.startswith("Free dosa")


def test_submit_requires_login(client):
    assert client.post("/api/submit/text", json={"text": "x" * 20}).status_code == 401
    assert client.post("/api/submit/file", files={"file": ("a.png", b"x")}).status_code == 401


@needs_ffmpeg
def test_upload_image_is_stored_under_a_random_name(client, session, user, h, uploads):
    r = client.post("/api/submit/file", files={"file": ("../../etc/menu.png", png_bytes(), "image/png")}, headers=h)
    assert r.status_code == 202
    job = session.get(Job, r.json()["job_id"])
    assert job.kind == "file" and job.url == "file:menu.png"  # path parts stripped from the label
    assert os.path.dirname(job.payload) == str(uploads) and os.path.exists(job.payload)
    assert os.path.basename(job.payload).isalnum()  # random hex, no user-controlled name or extension


@needs_ffmpeg
def test_upload_rejects_non_media_empty_and_oversized(client, session, user, h, uploads, monkeypatch):
    assert client.post("/api/submit/file", files={"file": ("x.mp4", b"not a video")}, headers=h).status_code == 422
    assert client.post("/api/submit/file", files={"file": ("x.mp4", b"")}, headers=h).status_code == 422
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    big = png_bytes() + b"0" * (1024 * 1024 + 10)
    assert client.post("/api/submit/file", files={"file": ("x.png", big)}, headers=h).status_code == 413
    assert os.listdir(uploads) == []  # nothing left behind after rejections
    assert session.scalars(select(Job)).all() == []


# ---- worker: every kind of job, evidence, documents, clean-up ----------------------------------

ITEM = ExtractedItem(kind="place", category="food", title="Meghana Foods", venue="ಮೇಘನಾ", venue_en="Meghana Foods",
                     city="Bengaluru", evidence="Meghana Foods", evidence_source="screen", evidence_at=5.0,
                     grounded=True, confidence=0.9)


def patch_rest(monkeypatch, seen):
    def fake_extract(c, t, n, p):
        seen["transcript"] = t
        return ExtractionResult(items=[ITEM])

    monkeypatch.setattr(worker.extract_svc, "extract", fake_extract)

    def geo(s, name, where):
        seen["geocoded"] = name
        return HERE[0], HERE[1], "confirmed"

    monkeypatch.setattr(worker.geo_svc, "geocode_place", geo)
    monkeypatch.setattr(worker.geo_svc, "geocode_area", lambda s, w: (HERE[0], HERE[1], "approx"))


def test_text_job_stores_document_evidence_and_geocodes_with_the_english_name(session, user, monkeypatch):
    seen = {}
    patch_rest(monkeypatch, seen)
    worker.enqueue(session, user, "text", kind="text", payload="Meghana Foods opened in Indiranagar, Bengaluru")
    session.commit()
    job = worker.claim_next(session)
    worker.run_job(session, job, NOW)
    assert job.status == "done", job.error
    item = session.scalars(select(Item)).one()
    assert item.venue_en == "Meghana Foods" and item.evidence_source == "screen" and item.grounded is True
    assert seen["geocoded"] == "Meghana Foods"  # not the Kannada spelling
    doc = session.get(Document, item.document_id)
    assert doc.kind == "text" and doc.timeline[0]["source"] == "text"
    assert seen["transcript"].startswith("[text] Meghana Foods")


def test_file_job_reads_the_upload_and_deletes_it(session, user, monkeypatch, tmp_path):
    seen = {}
    patch_rest(monkeypatch, seen)
    path = tmp_path / "abc123"
    path.write_bytes(b"data")
    doc = VideoDocument(kind="image", segments=[Segment(source="screen", text="Meghana Foods")])
    monkeypatch.setattr(worker.local_src, "ingest_file", lambda p, name="": from_document(doc))
    worker.enqueue(session, user, "file:menu.png", kind="file", payload=str(path))
    session.commit()
    job = worker.claim_next(session)
    worker.run_job(session, job, NOW)
    assert job.status == "done" and not path.exists()
    assert seen["transcript"] == "[screen] Meghana Foods"


def test_upload_is_deleted_even_when_reading_fails(session, user, monkeypatch, tmp_path):
    path = tmp_path / "bad"
    path.write_bytes(b"data")

    def boom(p, name=""):
        raise RuntimeError("decoder crashed")

    monkeypatch.setattr(worker.local_src, "ingest_file", boom)
    worker.enqueue(session, user, "file:x", kind="file", payload=str(path))
    session.commit()
    job = worker.claim_next(session)
    worker.run_job(session, job, NOW)
    assert job.status == "failed" and not path.exists()


def test_evidence_endpoint_returns_quote_and_nearby_timeline(client, session, user, h, monkeypatch):
    seen = {}
    patch_rest(monkeypatch, seen)
    doc = VideoDocument(kind="video", language="kn", segments=[
        Segment(source="speech", text="line near", start=3.0, end=4.0),
        Segment(source="screen", text="Meghana Foods", start=5.0, end=9.0),
        Segment(source="speech", text="far away line", start=60.0, end=62.0),
    ])
    row = Document(user_id=user.id, kind="video", language="kn", timeline=[s.model_dump(mode="json") for s in doc.segments], meta={})
    session.add(row)
    session.flush()
    item = Item(user_id=user.id, title="Meghana Foods", normalized="meghana foods", evidence="Meghana Foods",
                evidence_source="screen", evidence_at=5.0, grounded=True, document_id=row.id)
    session.add(item)
    session.commit()
    body = client.get(f"/api/items/{item.id}/source", headers=h).json()
    assert body["evidence"] == "Meghana Foods" and body["source"] == "screen" and body["language"] == "kn"
    assert [s["text"] for s in body["around"]] == ["line near", "Meghana Foods"]
    other = client.get(f"/api/items/{item.id + 99}/source", headers=h)
    assert other.status_code == 404
    assert client.get("/api/items", headers=h).json()["items"][0]["evidence"] == "Meghana Foods"
