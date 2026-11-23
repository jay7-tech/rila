from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import api as apimod
from app.recs.offers import Offer
from app.recs.planner import CLOSING_MARGIN_MIN, plan_route, simulate, travel_minutes
from app.recs.ranking import Recommendation
from conftest import HERE, NOW, make_item

START = (12.9716, 77.6412)


def rec(i, dlat, dlon=0.0, score=0.5, category="food", **kw):
    o = Offer(id=i, title=f"S{i}", category=category, lat=START[0] + dlat, lon=START[1] + dlon, **kw)
    return Recommendation(o, score, None, "", [])


def test_travel_time_walks_short_hops_and_drives_long_ones():
    near = travel_minutes(START, (START[0] + 0.005, START[1]))  # about 750 m with detour
    far = travel_minutes(START, (START[0] + 0.05, START[1]))  # about 7.5 km with detour
    assert near == pytest.approx(0.55 * 1.35 / 4.5 * 60, rel=0.15)
    assert far < 30 and far > near
    assert travel_minutes(START, (START[0] + 0.005, START[1]), "drive") < near


def test_everything_fits_in_a_long_budget_and_stops_are_timed_in_order():
    recs = [rec(1, 0.002), rec(2, 0.004), rec(3, 0.006)]
    plan = plan_route(recs, *START, NOW, budget_min=480)
    assert len(plan.stops) == 3 and plan.skipped == 0
    assert all(a.depart <= b.arrive for a, b in zip(plan.stops, plan.stops[1:]))
    assert plan.total_minutes == pytest.approx((plan.stops[-1].depart - NOW).total_seconds() / 60, abs=0.1)


def test_tight_budget_keeps_the_highest_value_per_minute():
    close_low = rec(1, 0.001, score=0.3)
    far_high = rec(2, 0.08, score=0.9)  # a long drive away
    plan = plan_route([close_low, far_high], *START, NOW, budget_min=70)
    assert [s.offer.id for s in plan.stops] == [1] and plan.skipped == 1


def test_nothing_fits_returns_an_empty_plan():
    plan = plan_route([rec(1, 0.0, category="event")], *START, NOW, budget_min=20)
    assert plan.stops == [] and plan.total_minutes == 0.0


def test_offer_that_ends_too_soon_is_skipped_and_one_that_starts_later_is_waited_for():
    ends_soon = rec(1, 0.003, ends_at=NOW + timedelta(minutes=5))
    starts_later = rec(2, 0.003, starts_at=NOW + timedelta(minutes=25))
    plan = plan_route([ends_soon, starts_later], *START, NOW, budget_min=240)
    assert [s.offer.id for s in plan.stops] == [2]
    assert plan.stops[0].arrive == NOW + timedelta(minutes=25) and plan.stops[0].wait_min > 5


def test_waiting_too_long_for_an_offer_is_not_planned():
    plan = plan_route([rec(1, 0.0, starts_at=NOW + timedelta(hours=3))], *START, NOW, budget_min=600)
    assert plan.stops == []


def test_closing_margin_is_respected():
    r = rec(1, 0.0, ends_at=NOW + timedelta(minutes=CLOSING_MARGIN_MIN - 1))
    assert simulate([r], START, NOW, 300, "auto") is None


def test_two_opt_untangles_a_crossed_route():
    a, b, c = rec(1, 0.01), rec(2, 0.02), rec(3, 0.03)  # a straight line north: best order is by distance
    plan = plan_route([c, a, b], *START, NOW, budget_min=600)
    assert [s.offer.id for s in plan.stops] == [1, 2, 3]


def test_unlocated_items_are_skipped_and_max_stops_is_enforced():
    unpinned = Recommendation(Offer(id=99, title="No pin", category="food"), 0.9, None, "", [])
    many = [rec(i, 0.001 * i) for i in range(1, 12)]
    plan = plan_route([unpinned] + many, *START, NOW, budget_min=2000, max_stops=4)
    assert len(plan.stops) == 4 and all(s.offer.id != 99 for s in plan.stops)


@pytest.fixture()
def client(session):
    return TestClient(apimod.create_app())


def test_plan_endpoint_orders_saved_places_and_skips_visited_and_dismissed(client, session, user):
    a = make_item(session, user, "Cafe A", kind="place", venue="A", source_url="a", dlat=0.002)
    b = make_item(session, user, "Cafe B", kind="place", venue="B", source_url="b", dlat=0.004)
    c = make_item(session, user, "Visited C", kind="place", venue="C", source_url="c", dlat=0.003, visited=True)
    h = {"X-Rilla-Token": user.token}
    r = client.get(f"/api/plan?lat={HERE[0]}&lon={HERE[1]}&minutes=240", headers=h)
    assert r.status_code == 200
    titles = [s["title"] for s in r.json()["stops"]]
    assert titles == ["Cafe A", "Cafe B"] and "note" in r.json()
    client.post(f"/api/items/{b.id}/feedback", json={"action": "dismiss"}, headers=h)
    assert [s["title"] for s in client.get(f"/api/plan?lat={HERE[0]}&lon={HERE[1]}", headers=h).json()["stops"]] == ["Cafe A"]
    assert client.get(f"/api/plan?lat={HERE[0]}&lon={HERE[1]}&minutes=5", headers=h).status_code == 422
