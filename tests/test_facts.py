from datetime import date, datetime

from app.core.facts import check_dates, explicit_end_date, numbers_in, verify_price
from app.services.extract import extract

NOW = datetime(2026, 11, 24, 12, 0)  # a Tuesday
POSTED = datetime(2026, 11, 23, 9, 0)  # a Monday


def test_numbers_in_handles_commas_and_indic_digits():
    assert numbers_in("Rs 1,299 only") == {"1299"}
    assert numbers_in("सिर्फ ९९ रुपये") == {"99"}
    assert numbers_in("1.50 and 007") == {"1.5", "7"}


def test_verify_price():
    assert verify_price("Rs 99", "meals just 99 rs today") is True
    assert verify_price("Rs 149", "meals just 99 rs today") is False
    assert verify_price("50% off", "fifty percent off") is None  # source says it in words: cannot check
    assert verify_price("free entry", "free entry") is None  # no number to check


def test_explicit_end_dates():
    assert explicit_end_date("offer till 30 Nov", date(2026, 11, 23)) == date(2026, 11, 30)
    assert explicit_end_date("until Dec 2nd", date(2026, 11, 23)) == date(2026, 12, 2)
    assert explicit_end_date("valid until Sunday", date(2026, 11, 23)) == date(2026, 11, 29)
    assert explicit_end_date("started on 30 Nov", date(2026, 11, 23)) is None  # no end cue
    assert explicit_end_date("till 5 Jan", date(2026, 12, 20)) == date(2027, 1, 5)  # rolls into next year


def test_check_dates_corrects_model_arithmetic():
    wrong_end = datetime(2026, 11, 30, 23, 59)
    s, e, notes = check_dates("offer", None, wrong_end, ["until Sunday"], NOW, POSTED)
    assert e.date() == date(2026, 11, 29) and "end_corrected" in notes


def test_check_dates_drops_impossible_ones():
    s, e, notes = check_dates("offer", datetime(2026, 12, 5), datetime(2026, 12, 1), [], NOW, POSTED)
    assert (s, e) == (None, None) and "dates_dropped" in notes
    s, e, notes = check_dates("offer", None, datetime(2025, 1, 1), [], NOW, POSTED)
    assert e is None and "end_before_posting" in notes
    s, e, notes = check_dates("offer", None, datetime(2030, 1, 1), [], NOW, POSTED)
    assert e is None and "date_too_far" in notes
    assert check_dates("place", datetime(2026, 12, 1), None, [], NOW, POSTED)[:2] == (None, None)


def test_check_dates_leaves_good_dates_alone():
    end = datetime(2026, 11, 30, 23, 59)
    s, e, notes = check_dates("offer", datetime(2026, 11, 25), end, ["till 30 Nov"], NOW, POSTED)
    assert (s, e, notes) == (datetime(2026, 11, 25), end, [])


def _llm(item):
    import json
    return lambda system, user: json.dumps({"items": [item], "signals": []})


def test_extract_drops_invented_price_and_fixes_end_date():
    transcript = "[0:03 speech] Kaapi Kitchen in Indiranagar has a combo just 99 rupees until Sunday"
    item = {"kind": "offer", "category": "food", "title": "Combo", "venue": "Kaapi Kitchen", "venue_en": "Kaapi Kitchen",
            "city": "Bengaluru", "area": "Indiranagar", "price_text": "Rs 149", "duration_text": "until Sunday",
            "ends_at": "2026-12-02", "evidence": "Kaapi Kitchen in Indiranagar has a combo just 99 rupees until Sunday",
            "confidence": 0.9}
    r = extract("", transcript, NOW, POSTED, llm=_llm(item))
    it = r.items[0]
    assert it.price_text is None and r.stats["price_dropped"] == 1
    assert it.ends_at.startswith("2026-11-29") and r.stats["end_corrected"] == 1
