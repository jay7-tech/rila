from datetime import datetime, timedelta

import pytest

from app.recs.offers import Offer, status, time_left_label
from app.recs.ranking import haversine_m, recommend

NOW = datetime(2026, 10, 22, 18, 0, 0)
HERE = (12.9716, 77.6412)


def offer(i, cat="food", dlat=0.0, ends_h=None, starts_h=None, **kw):
    return Offer(
        id=i, title=f"o{i}", category=cat, lat=HERE[0] + dlat, lon=HERE[1],
        ends_at=NOW + timedelta(hours=ends_h) if ends_h is not None else None,
        starts_at=NOW + timedelta(hours=starts_h) if starts_h is not None else None, **kw,
    )


def test_haversine_known_distance():
    assert 1100 < haversine_m(12.9716, 77.6412, 12.9816, 77.6412) < 1125


def test_status_and_labels():
    assert status(offer(1, ends_h=-1), NOW) == "expired"
    assert status(offer(2, ends_h=10), NOW) == "ending_soon"
    assert status(offer(3, ends_h=200), NOW) == "active"
    assert status(offer(4, starts_h=5, ends_h=50), NOW) == "upcoming"
    assert status(offer(5), NOW) == "open_ended"
    assert time_left_label(offer(2, ends_h=10), NOW) == "ends in 10h"
    assert time_left_label(offer(3, ends_h=100), NOW) == "ends in 4d"
    assert time_left_label(offer(6, ends_h=0.5), NOW) == "ends in 30 min"


def test_invalid_window_rejected():
    with pytest.raises(ValueError):
        offer(1, starts_h=10, ends_h=2)


def test_expired_and_far_excluded():
    recs = recommend([offer(1, ends_h=-2), offer(2, dlat=0.2, ends_h=10), offer(3, ends_h=10)], *HERE, NOW)
    assert [r.offer.id for r in recs] == [3]


def test_ending_soon_and_close_rank_first():
    recs = recommend(
        [offer(1, dlat=0.01, ends_h=300), offer(2, dlat=0.01, ends_h=5), offer(3, dlat=0.0002, ends_h=300)], *HERE, NOW
    )
    assert recs[0].offer.id in (2, 3) and recs[-1].offer.id == 1


def test_interest_breaks_ties():
    recs = recommend(
        [offer(1, cat="food", dlat=0.005, ends_h=100), offer(2, cat="education", dlat=0.005, ends_h=100)],
        *HERE, NOW, interests={"education": 1.0},
    )
    assert recs[0].offer.id == 2 and "matches your interests" in recs[0].reasons


def test_category_query_filters():
    recs = recommend([offer(1, cat="food", ends_h=9), offer(2, cat="store", ends_h=9)], *HERE, NOW, query_category="store")
    assert [r.offer.id for r in recs] == [2]


def test_offer_without_location_listed_lower():
    no_loc = Offer(id=9, title="online scheme", category="scheme", ends_at=NOW + timedelta(hours=20))
    recs = recommend([no_loc, offer(1, dlat=0.001, ends_h=20)], *HERE, NOW)
    assert recs[0].offer.id == 1 and recs[1].offer.id == 9 and recs[1].distance_m is None
