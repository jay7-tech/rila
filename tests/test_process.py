from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import Item, Signal
from app.services.extract import ExtractedItem, ExtractionResult
from app.services.ingest import Ingested
from app.services.process import area_key, process_url
from app.signals.models import ExtractedSignal

from conftest import HERE, NOW


def run(session, user, items=(), signals=(), geo=None, area=None, creator="@bangalorebites", posted=None):
    ing = Ingested("cap", "tr", creator, posted)
    ex = ExtractionResult(items=list(items), signals=list(signals))
    return process_url(
        session, user, "https://instagram.com/reel/abc", NOW,
        ingest_fn=lambda url: ing,
        extract_fn=lambda c, t, n, p: ex,
        geocode_place_fn=geo or (lambda s, name, where: (HERE[0], HERE[1], "confirmed")),
        geocode_area_fn=area or (lambda s, where: (HERE[0] + 0.001, HERE[1], "approx")),
    )


def offer(title="BOGO on cold brews", venue="Third Wave Coffee", **kw):
    base = dict(kind="offer", category="food", title=title, venue=venue, city="Bengaluru", area="Indiranagar",
                price_text="2 for 1", ends_at="2026-10-25", confidence=0.9)
    base.update(kw)
    return ExtractedItem(**base)


def test_saves_offer_with_window_and_pin(session, user):
    res = run(session, user, [offer()])
    session.commit()
    item = session.scalars(select(Item)).one()
    assert len(res.items) == 1
    assert item.geo_status == "confirmed" and item.lat == HERE[0]
    assert item.ends_at == datetime(2026, 10, 25, 23, 59)
    assert item.creator == "@bangalorebites"
    assert user.interests["food"] > 0


def test_rejected_name_is_not_saved(session, user):
    res = run(session, user, [offer(venue="Made Up Cafe")], geo=lambda s, n, w: (None, None, "rejected"))
    assert res.items == [] and res.rejected == ["Made Up Cafe"]
    assert session.scalars(select(Item)).all() == []


def test_unknown_place_falls_back_to_area_pin(session, user):
    res = run(session, user, [offer()], geo=lambda s, n, w: (None, None, "no_candidates"))
    assert res.items[0].geo_status == "approx"


def test_no_pin_when_area_lookup_also_fails(session, user):
    res = run(
        session, user, [offer()],
        geo=lambda s, n, w: (None, None, "error"), area=lambda s, w: (None, None, "error"),
    )
    assert res.items[0].geo_status == "none" and res.items[0].lat is None


def test_info_items_skip_geocoding(session, user):
    called = []
    info = ExtractedItem(kind="info", category="scheme", title="Scholarship portal", ends_at="2026-11-30", confidence=0.9)
    res = run(session, user, [info], geo=lambda *a: called.append(1) or (None, None, "error"))
    assert called == [] and res.items[0].lat is None


def test_duplicate_same_place_is_merged_and_extends_end(session, user):
    run(session, user, [offer()])
    session.commit()
    res = run(session, user, [offer(ends_at="2026-10-30")])
    session.commit()
    assert res.items == [] and len(res.duplicates) == 1
    assert session.scalars(select(Item)).one().ends_at == datetime(2026, 10, 30, 23, 59)


def test_same_name_in_far_city_is_not_duplicate(session, user):
    run(session, user, [offer()])
    session.commit()
    far = lambda s, n, w: (HERE[0] + 0.5, HERE[1], "confirmed")
    res = run(session, user, [offer()], geo=far)
    assert len(res.items) == 1


def test_signals_saved_with_creator_as_source_and_hint(session, user):
    sig = ExtractedSignal(signal_type="crowd", area_name="Kudle Beach", city="Gokarna", value="packed",
                          time_hint="yesterday", confidence=0.8)
    run(session, user, signals=[sig], posted=NOW - timedelta(hours=1))
    session.commit()
    row = session.scalars(select(Signal)).one()
    assert row.source_id == "@bangalorebites"
    assert row.area_key == "gokarna|kudle beach"
    assert row.observed_at == NOW - timedelta(hours=1) - timedelta(days=1)


def test_area_key_without_area_uses_city():
    assert area_key("Gokarna", None) == "gokarna|gokarna"


def test_place_ignores_dates_so_it_never_expires(session, user):
    # model copied the posting date into a restaurant recommendation
    res = run(session, user, [offer(kind="place", title="Klai", venue="Klai",
                                    starts_at="2024-09-21", ends_at="2024-09-22")])
    session.commit()
    item = session.scalars(select(Item)).one()
    assert len(res.items) == 1 and item.starts_at is None and item.ends_at is None


def test_offer_that_already_ended_is_skipped_and_reported(session, user):
    res = run(session, user, [offer(title="Old deal", ends_at="2024-09-22")])
    assert res.items == [] and res.expired == ["Old deal"]
    assert session.scalars(select(Item)).first() is None


def test_contradictory_window_keeps_end_date(session, user):
    res = run(session, user, [offer(starts_at="2026-10-30", ends_at="2026-10-25")])
    assert res.items[0].starts_at is None and res.items[0].ends_at == datetime(2026, 10, 25, 23, 59)


def test_expired_is_shown_to_the_user():
    from app.formatting import saved_summary
    assert "Skipped (already ended): Old deal" in saved_summary({"saved": [], "duplicates": [], "expired": ["Old deal"]})
