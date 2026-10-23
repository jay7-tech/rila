from datetime import datetime, timedelta

from app.signals.aggregate import build_consensus
from app.signals.briefing import format_briefing
from app.signals.freshness import age_label, freshness, resolve_observed_at
from app.signals.models import Signal

NOW = datetime(2026, 10, 22, 12, 0, 0)


def sig(stype, value, hours_ago, source, conf=0.9, area="gokarna|om beach", sev=None):
    return Signal(
        signal_type=stype, area_key=area, value=value, severity=sev, confidence=conf,
        observed_at=NOW - timedelta(hours=hours_ago), source_id=source,
    )


def test_freshness_halves_at_half_life():
    assert abs(freshness("crowd", NOW - timedelta(hours=6), NOW) - 0.5) < 1e-9
    assert freshness("crowd", NOW, NOW) == 1.0


def test_future_timestamp_does_not_exceed_one():
    assert freshness("crowd", NOW + timedelta(hours=3), NOW) == 1.0


def test_crowd_decays_faster_than_opening():
    t = NOW - timedelta(hours=12)
    assert freshness("crowd", t, NOW) < freshness("opening", t, NOW)


def test_resolve_observed_at_uses_hint_and_post_time():
    posted = NOW - timedelta(hours=2)
    assert resolve_observed_at("today", posted, NOW) == posted
    assert resolve_observed_at("yesterday", posted, NOW) == posted - timedelta(days=1)
    assert resolve_observed_at(None, None, NOW) == NOW


def test_two_independent_sources_confirm():
    out = build_consensus([sig("crowd", "Packed", 1, "a"), sig("crowd", "packed", 2, "b")], NOW)
    assert len(out) == 1 and out[0].status == "confirmed" and out[0].n_sources == 2


def test_same_creator_cannot_self_confirm():
    out = build_consensus([sig("crowd", "packed", h, "a") for h in (1, 2, 3)], NOW)
    assert out[0].n_sources == 1 and out[0].status == "unconfirmed"


def test_stale_signals_dropped():
    assert build_consensus([sig("crowd", "packed", 48, "a")], NOW) == []


def test_fresh_majority_beats_old_opposite():
    out = build_consensus(
        [sig("crowd", "empty", 20, "a"), sig("crowd", "packed", 1, "b"), sig("crowd", "packed", 2, "c")], NOW
    )
    assert out[0].value == "packed"


def test_briefing_contents_and_empty_case():
    out = build_consensus(
        [sig("weather", "heavy rain", 1, "a", sev=3), sig("weather", "heavy rain", 2, "b", sev=2), sig("crowd", "quiet", 1, "c")],
        NOW,
    )
    text = format_briefing("Gokarna", out, NOW)
    assert "heavy rain" in text and "2 reports" in text and "unconfirmed" in text
    assert text.index("Weather") < text.index("Crowd")
    assert "No recent reports" in format_briefing("Hampi", [], NOW)


def test_age_label():
    assert age_label(NOW - timedelta(minutes=35), NOW) == "35 min ago"
    assert age_label(NOW - timedelta(hours=5), NOW) == "5h ago"
    assert age_label(NOW - timedelta(days=3), NOW) == "3d ago"
