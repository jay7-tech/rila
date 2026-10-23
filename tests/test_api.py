from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import api as apimod
from app.models import Job, Signal, utcnow
from app.services import users as users_svc

from conftest import HERE, NOW, make_item


@pytest.fixture()
def client(session):
    return TestClient(apimod.create_app())


@pytest.fixture()
def h(user):
    return {"X-Rilla-Token": user.token}


def test_requires_token(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"X-Rilla-Token": "bad"}).status_code == 401
    assert client.get("/api/health").json() == {"ok": True}


def test_me_and_token_in_query(client, user):
    assert client.get(f"/api/me?t={user.token}").json()["name"] == "Asha"


def test_near_ranks_and_hides_expired(client, session, user, h, monkeypatch):
    monkeypatch.setattr(apimod, "utcnow", lambda: NOW)
    make_item(session, user, "Close deal", dlat=0.001, ends_h=5)
    make_item(session, user, "Old deal", dlat=0.001, ends_h=-3)
    make_item(session, user, "Far deal", dlat=0.3, ends_h=5)
    r = client.get(f"/api/near?lat={HERE[0]}&lon={HERE[1]}", headers=h).json()["items"]
    assert [i["title"] for i in r] == ["Close deal"]
    assert r[0]["status"] == "ending_soon" and "ending soon" in r[0]["reasons"]


def test_users_cannot_see_each_others_items(client, session, user, h, monkeypatch):
    monkeypatch.setattr(apimod, "utcnow", lambda: NOW)
    other = users_svc.create_user(session, 222, "Ravi")
    session.commit()
    mine = make_item(session, user, "Mine", ends_h=50)
    theirs = make_item(session, other, "Theirs", ends_h=50)
    titles = [i["title"] for i in client.get("/api/items", headers=h).json()["items"]]
    assert titles == ["Mine"]
    assert client.delete(f"/api/items/{theirs.id}", headers=h).status_code == 404
    assert client.delete(f"/api/items/{mine.id}", headers=h).status_code == 200


def test_search_and_category_filter(client, session, user, h, monkeypatch):
    monkeypatch.setattr(apimod, "utcnow", lambda: NOW)
    make_item(session, user, "Cold brew BOGO", category="food", ends_h=50)
    make_item(session, user, "Python workshop", category="education", ends_h=50)
    assert len(client.get("/api/items?q=brew", headers=h).json()["items"]) == 1
    assert [i["title"] for i in client.get("/api/items?category=education", headers=h).json()["items"]] == ["Python workshop"]


def test_submit_validates_and_queues(client, session, user, h):
    assert client.post("/api/submit", json={"url": "https://example.com/x"}, headers=h).status_code == 422
    assert client.post("/api/submit", json={"url": "not a url"}, headers=h).status_code == 422
    r = client.post("/api/submit", json={"url": "https://www.instagram.com/reel/abc/"}, headers=h)
    assert r.status_code == 202
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert job["status"] == "queued"


def test_submit_queue_limit(client, session, user, h):
    for i in range(5):
        assert client.post("/api/submit", json={"url": f"https://youtu.be/{i}abc"}, headers=h).status_code == 202
    assert client.post("/api/submit", json={"url": "https://youtu.be/zzzz"}, headers=h).status_code == 429


def test_other_users_job_hidden(client, session, user, h):
    other = users_svc.create_user(session, 333, "Z")
    job = Job(user_id=other.id, url="https://youtu.be/x")
    session.add(job)
    session.commit()
    assert client.get(f"/api/jobs/{job.id}", headers=h).status_code == 404


def test_briefing_confirms_across_users(client, session, user, h):
    now = utcnow()
    for src in ("@a", "@b"):
        session.add(Signal(signal_type="crowd", area_key="gokarna|kudle beach", city="Gokarna", area_name="Kudle Beach",
                           value="packed", confidence=0.9, observed_at=now - timedelta(hours=1), source_id=src))
    session.commit()
    data = client.get("/api/briefing?place=Gokarna", headers=h).json()
    assert data["signals"][0]["status"] == "confirmed" and data["signals"][0]["sources"] == 2
    assert "packed" in data["text"]
    empty = client.get("/api/briefing?place=Hampi", headers=h).json()
    assert empty["signals"] == [] and "No recent reports" in empty["text"]


def test_location_saved(client, session, user, h):
    assert client.post("/api/location", json={"lat": 12.9, "lon": 77.6}, headers=h).status_code == 200
    assert client.get("/api/me", headers=h).json()["location"] == {"lat": 12.9, "lon": 77.6}
    assert client.post("/api/location", json={"lat": 999, "lon": 0}, headers=h).status_code == 422
