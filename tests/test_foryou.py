import pytest
from fastapi.testclient import TestClient

from app import api as apimod
from conftest import HERE, make_item


@pytest.fixture()
def client(session):
    return TestClient(apimod.create_app())


def _feed(client, user, **params):
    r = client.get("/api/foryou", params=params, headers={"X-Rilla-Token": user.token})
    assert r.status_code == 200, r.text
    return r.json()


def test_foryou_is_cold_without_feedback_and_still_returns_items(client, session, user):
    make_item(session, user, "Dosa", kind="place", venue="Dosa Camp", grounded=True, evidence_source="speech")
    make_item(session, user, "Walk", category="attraction", kind="place", venue="Lake", dlat=0.001)
    d = _feed(client, user, lat=HERE[0], lon=HERE[1])
    assert d["cold"] is True and len(d["items"]) == 2 and d["learned"] == {}
    assert all(0 <= i["reliability"]["score"] <= 1 and i["reliability"]["parts"] for i in d["items"])
    assert "new to you" in d["items"][0]["reasons"]


def test_foryou_learns_from_feedback_and_hides_dismissed(client, session, user):
    h = {"X-Rilla-Token": user.token}
    liked = [make_item(session, user, f"Cafe {n}", kind="place", venue=f"Cafe {n}", dlat=0.0005 * n) for n in range(3)]
    walks = [make_item(session, user, f"Park {n}", category="attraction", kind="place", venue=f"Park {n}", dlat=0.0005 * n) for n in range(3)]
    fresh_food = make_item(session, user, "Bakery", kind="place", venue="Bakery X", dlat=0.002)
    fresh_walk = make_item(session, user, "Trail", category="attraction", kind="place", venue="Trail Y", dlat=0.002)
    for it in liked[:2]:
        client.post(f"/api/items/{it.id}/feedback", json={"action": "like"}, headers=h)
    client.post(f"/api/items/{liked[2].id}/feedback", json={"action": "visit"}, headers=h)
    for it in walks[:2]:
        client.post(f"/api/items/{it.id}/feedback", json={"action": "dismiss"}, headers=h)
    d = _feed(client, user, lat=HERE[0], lon=HERE[1], limit=20)
    ids = [i["id"] for i in d["items"]]
    assert d["cold"] is False and d["trained_on"] == 5
    assert walks[0].id not in ids and walks[1].id not in ids  # dismissed means gone
    assert ids.index(fresh_food.id) < ids.index(fresh_walk.id)  # taste carries over to unseen items
    row = next(i for i in d["items"] if i["id"] == fresh_food.id)
    assert row["taste"] > 0.5 and any("food" in r for r in row["reasons"])
    assert any("food" in x["feature"] for x in d["learned"]["likes"])


def test_foryou_works_without_location_and_requires_auth(client, session, user):
    make_item(session, user, "Dosa", kind="place", venue="Dosa Camp")
    assert len(_feed(client, user)["items"]) == 1
    assert client.get("/api/foryou").status_code in (401, 403, 422)


def test_search_and_saved_carry_reliability(client, session, user):
    make_item(session, user, "Meghana biryani", kind="place", venue="Meghana Foods", source_url="a", grounded=True, evidence_source="multi")
    h = {"X-Rilla-Token": user.token}
    hit = client.get("/api/search", params={"q": "biriyani"}, headers=h)
    body = hit.json()
    rows = body.get("items") or body.get("results") or []
    assert rows and rows[0]["reliability"]["label"] in ("high", "medium", "low")
