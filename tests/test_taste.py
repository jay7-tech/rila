from datetime import datetime, timedelta

import pytest

from app.recs import reliability as R
from app.recs import taste as T

NOW = datetime(2026, 11, 24, 12, 0)
CAFE = {"category": "food", "kind": "place", "creator": "foodie", "area": "Indiranagar", "price": "Rs 120", "title": "Filter coffee and dosa", "summary": "classic breakfast"}
PARK = {"category": "attraction", "kind": "place", "creator": "walker", "area": "Cubbon", "title": "Bamboo grove walk", "summary": "quiet morning"}


def test_features_are_deterministic_and_capture_price_band_and_words():
    f = T.features(CAFE)
    assert "bias_cat:food" in f and "price:under300" in f and "w:coffee" in f and "creator:foodie" in f
    assert T.features(CAFE) == f and T.bucket("w:coffee") == T.bucket("w:coffee")
    assert T.price_band("free entry") is None and T.price_band("Rs 0") == "free" and T.price_band("Rs 1,500") == "premium"


def test_cold_until_enough_events():
    assert T.train([("like", CAFE, NOW)], NOW).cold
    assert not T.train([("like", CAFE, NOW)] * 3, NOW).cold


def test_learns_likes_and_dislikes():
    ev = [("like", CAFE, NOW), ("visit", CAFE, NOW), ("dismiss", PARK, NOW), ("dismiss", PARK, NOW)]
    m = T.train(ev, NOW)
    assert m.predict(CAFE) > 0.6 > 0.4 > m.predict(PARK)
    unseen = dict(CAFE, title="Masala dosa", area="Jayanagar", creator="other")
    assert m.predict(unseen) > m.predict(dict(PARK, title="Lake walk", creator="x"))  # generalises through category/price
    tops = m.top_features()
    assert any("food" in r["feature"] for r in tops["likes"]) and any("attraction" in r["feature"] for r in tops["avoids"])


def test_recent_actions_outweigh_old_ones():
    old, new = NOW - timedelta(days=180), NOW
    m = T.train([("like", CAFE, old), ("like", CAFE, old), ("dismiss", CAFE, new), ("dismiss", CAFE, new)], NOW)
    assert m.predict(CAFE) < 0.5


def test_training_is_deterministic_and_explains():
    ev = [("like", CAFE, NOW)] * 2 + [("dismiss", PARK, NOW)] * 2
    a, b = T.train(ev, NOW), T.train(ev, NOW)
    assert a.predict(CAFE) == b.predict(CAFE)
    why = a.explain(CAFE)
    assert why and abs(why[0]["weight"]) >= abs(why[-1]["weight"])
    assert T.feature_text("bias_cat:food") == "category food"
    assert T.Taste().predict(CAFE) == pytest.approx(0.5)


def test_reliability_orders_strong_evidence_above_weak():
    strong = R.score(R.components(True, "multi", None, "confirmed", 0.9, 3))
    weak = R.score(R.components(False, "caption", None, "none", 0.2, 1))
    assert strong > 0.8 > 0.4 > weak and R.label(strong) == "high" and R.label(weak) == "low"
    parts = R.components(True, "speech", None, "approx")
    assert R.score(parts) == pytest.approx(sum(r["points"] for r in R.breakdown(parts)), abs=0.002)
    assert R.components(None, None, None, "none")["grounded"] == 0.5


def test_brier_and_ece():
    assert R.brier([(1.0, 1), (0.0, 0)]) == 0.0 and R.brier([(0.5, 1), (0.5, 0)]) == 0.25 and R.brier([]) is None
    perfect = [(0.9, 1)] * 9 + [(0.9, 0)]
    assert R.ece(perfect) == pytest.approx(0.0, abs=1e-9)
    assert R.ece([(0.95, 0)] * 4) == pytest.approx(0.95)
    assert R.ece([]) is None
    assert 0 < R.extraction_score(True, "speech") <= 1 and R.extraction_score(False, "caption") < R.extraction_score(True, "multi")
