from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import api as apimod
from conftest import make_item

from app.services.places import cluster, list_places, name_key, resolve_places

NOW = datetime(2026, 11, 23, 12, 0)


def item(i, venue, **kw):
    base = dict(id=i, kind="place", category="food", title=venue, venue=venue, venue_en=venue, city="Bengaluru", area=None,
                lat=None, lon=None, geo_status="none", price_text=None, summary=None, source_url=f"https://y/{i}", creator=None,
                confidence=0.8, grounded=True, evidence=f"try {venue}", evidence_source="speech", evidence_at=3.0,
                created_at=NOW - timedelta(days=i), ends_at=None, visited=False)
    base.update(kw)
    return NS(**base)


def test_same_venue_spelled_differently_is_one_place():
    items = [item(1, "Meghana Foods"), item(2, "meghana foods"), item(3, "Meghana Food"), item(4, "Truffles")]
    groups = cluster(items)
    assert sorted(len(g) for g in groups) == [1, 3]


def test_different_cities_are_not_merged():
    assert len(cluster([item(1, "Corner House", city="Bengaluru"), item(2, "Corner House", city="Mysuru")])) == 2


def test_pinned_mentions_far_apart_are_different_branches():
    near = dict(lat=12.9716, lon=77.5946, geo_status="confirmed")
    far = dict(lat=13.1, lon=77.7, geo_status="confirmed")
    assert len(cluster([item(1, "Truffles", **near), item(2, "Truffles", **far)])) == 2
    assert len(cluster([item(1, "Truffles", **near), item(2, "Truffles", lat=12.9718, lon=77.5948, geo_status="confirmed")])) == 1


def test_generic_words_do_not_decide_identity():
    assert name_key(item(1, "Hotel Vijay")) == name_key(item(2, "Vijay"))
    assert len(cluster([item(1, "Cafe Coffee Day"), item(2, "Hotel Vijay")])) == 2


def test_aggregation_counts_reels_prices_creators_and_proof():
    items = [
        item(1, "Meghana Foods", creator="a", price_text="Rs 299", confidence=0.7, source_url="u1"),
        item(2, "Meghana Foods", creator="b", price_text="Rs 299", confidence=0.9, source_url="u2", evidence="Meghana is great"),
        item(3, "Meghana Foods", kind="offer", creator="a", title="1+1", ends_at=NOW + timedelta(days=2), source_url="u1"),
        item(4, "Meghana Foods", kind="offer", title="old", ends_at=NOW - timedelta(days=1), source_url="u3"),
    ]
    (p,) = resolve_places(items, NOW)
    assert p.mentions == 4 and p.reels == 3 and p.creators == ["a", "b"] and p.prices == ["Rs 299"]
    assert [o["title"] for o in p.live_offers] == ["1+1"]  # expired offer is not live
    assert p.best_evidence["quote"] == "Meghana is great"  # highest-confidence grounded mention
    assert p.confidence > 0.95  # three independent reels
    assert p.first_seen < p.last_seen


def test_same_reel_twice_does_not_inflate_confidence():
    twice = resolve_places([item(1, "Toit", source_url="same"), item(2, "Toit", source_url="same")], NOW)[0]
    once = resolve_places([item(1, "Toit", source_url="same")], NOW)[0]
    assert twice.reels == 1 and twice.confidence == once.confidence


def test_info_items_are_ignored_and_most_reels_rank_first():
    items = [item(1, "A Cafe"), item(2, "B Cafe"), item(3, "B Cafe", source_url="x"), item(4, "Scheme", kind="info")]
    assert [p.name for p in resolve_places(items, NOW)] == ["B Cafe", "A Cafe"]


def test_list_places_filters_by_text_and_orders_by_distance():
    items = [item(1, "Far Cafe", lat=13.0, lon=77.7, geo_status="confirmed", city="X"),
             item(2, "Near Cafe", lat=12.9717, lon=77.5947, geo_status="confirmed", city="X"),
             item(3, "No Pin Cafe", city="X")]
    out = list_places(items, NOW, lat=12.9716, lon=77.5946)
    assert [p["name"] for p in out] == ["Near Cafe", "Far Cafe", "No Pin Cafe"] and out[0]["distance_m"] < 50
    assert [p["name"] for p in list_places(items, NOW, q="near")] == ["Near Cafe"]


@pytest.fixture()
def client(session):
    return TestClient(apimod.create_app())


def test_places_endpoint(client, session, user):
    make_item(session, user, "Meghana lunch", kind="place", venue="Meghana Foods", source_url="u1")
    make_item(session, user, "Meghana biryani", kind="place", venue="Meghana Foods", source_url="u2")
    h = {"X-Rilla-Token": user.token}
    r = client.get("/api/places", headers=h)
    assert r.status_code == 200 and r.json()["places"][0]["reels"] == 2
    assert client.get("/api/places").status_code in (401, 403, 422)
