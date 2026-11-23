"""Fixes found by reviewing 23 real Shorts: evidence that names the venue, OCR name fragments,
deal headlines saved as venues, menu prices labelled as offers, and invented filler speech."""
import json
from datetime import datetime

from app.core.document import Line
from app.core.grounding import looks_like_deal, name_line, trim_name_fragment
from app.perception.asr import too_sparse
from app.services.extract import extract

NOW = datetime(2026, 11, 23, 12, 0)


def llm_with(items):
    return lambda system, user: json.dumps({"items": items, "signals": []})


def place(**kw):
    base = {"kind": "place", "category": "food", "title": "Breakfast", "venue": "By2Coffee", "venue_en": "By2Coffee",
            "city": "Bengaluru", "confidence": 0.9}
    base.update(kw)
    return base


def test_evidence_that_does_not_name_the_venue_is_replaced_by_the_line_that_does():
    caption = "Budget breakfast.\n\nLocation: By2Coffee, Bengaluru, Karnataka"
    transcript = "[0:23-0:25 speech] now the food is in rupees 30"
    res = extract(caption, transcript, NOW, llm=llm_with([place(evidence="now the food is in rupees 30")]))
    it = res.items[0]
    assert "By2Coffee" in it.evidence and it.evidence_source == "caption" and it.grounded is True


def test_evidence_that_already_names_the_venue_is_kept():
    transcript = "[0:10-0:12 speech] we ate at By2Coffee today\n[0:12-0:14 speech] it was cheap"
    res = extract("", transcript, NOW, llm=llm_with([place(evidence="we ate at By2Coffee today")]))
    assert res.items[0].evidence == "we ate at By2Coffee today" and res.items[0].evidence_at == 10.0


def test_name_line_prefers_spoken_or_shown_lines_over_caption():
    lines = [Line(source="speech", start=4.0, text="welcome to Truffles Cafe")]
    hit = name_line(["Truffles Cafe"], lines, "Truffles Cafe in Koramangala")
    assert hit.source == "speech" and hit.start == 4.0 and hit.text == "welcome to Truffles Cafe"
    assert name_line(["Nowhere Place"], lines, "") is None
    assert name_line(["ab"], lines, "ab") is None  # too short to check


def test_ocr_fragment_of_restaurant_is_trimmed():
    assert trim_name_fragment("RANT MAJLIS MANDI") == "MAJLIS MANDI"
    assert trim_name_fragment("Majlis Mandi") == "Majlis Mandi"
    assert trim_name_fragment("Hotel Vijay Bhavan") == "Hotel Vijay Bhavan"  # a whole word, not a fragment
    assert trim_name_fragment("Rant House") == "Rant House"  # two words: leave alone


def test_trimmed_name_still_grounds_and_is_saved_clean():
    transcript = "[0:27-0:29 screen] RANT MAJLIS MANDI\n[0:35-0:37 screen] RESTAURANT MAJLIS MANDI"
    item = place(venue="RANT MAJLIS MANDI", venue_en="RANT MAJLIS MANDI", evidence="RANT MAJLIS MANDI", city="Bangalore")
    res = extract("", transcript, NOW, llm=llm_with([item]))
    assert res.items[0].venue == "MAJLIS MANDI"
    assert "MAJLIS MANDI" in res.items[0].evidence


def test_deal_headline_is_not_kept_as_a_venue():
    assert looks_like_deal("Fish meals just 99 rs")
    assert not looks_like_deal("Sri Ganesh Bhavan") and not looks_like_deal("7th Block Cafe")
    item = {"kind": "offer", "category": "food", "title": "Fish meals for 99", "venue": "Fish meals just 99 rs",
            "venue_en": "Fish meals just 99 rs", "area": "Jagadish Nagar", "city": "Bengaluru", "confidence": 0.9}
    res = extract("fish meals just 99 rs", "", NOW, llm=llm_with([item]))
    assert res.items and res.items[0].venue is None


def test_menu_price_list_is_a_place_not_an_offer():
    item = {"kind": "offer", "category": "food", "title": "Chow chow bath Rs 70", "venue": "Brahmins Coffee Bar",
            "venue_en": "Brahmins Coffee Bar", "city": "Bengaluru", "price_text": "Rs 70", "confidence": 0.9,
            "evidence": "Brahmins Coffee Bar"}
    res = extract("Brahmins Coffee Bar menu", "", NOW, llm=llm_with([item]))
    assert res.items[0].kind == "place"


def test_real_deals_stay_offers():
    deal = {"kind": "offer", "category": "food", "title": "Buy one get one on cold brew", "venue": "Toit",
            "venue_en": "Toit", "city": "Bengaluru", "confidence": 0.9}
    windowed = dict(deal, title="Weekend special", ends_at="2026-11-30")
    res = extract("Toit", "", NOW, llm=llm_with([deal, dict(windowed, venue="Third Wave", venue_en="Third Wave")]))
    assert res.items[0].kind == "offer"
    assert extract("Third Wave", "", NOW, llm=llm_with([dict(windowed, venue="Third Wave", venue_en="Third Wave")])).items[0].kind == "offer"


def test_long_segment_with_almost_no_text_is_filler():
    assert too_sparse("₹12 o o", 0.0, 22.0)
    assert not too_sparse("Whoever said Bangalore street food", 0.0, 3.0)
    assert not too_sparse("ok", 0.0, 2.0)  # short segment: fine
    assert not too_sparse("a long sentence spoken slowly over many seconds, with words", 0.0, 20.0)


def test_quote_close_in_time_to_the_venue_line_is_kept():
    transcript = "[0:03 speech] ಇಂದಿರಾನಗರದಲ್ಲಿ ಹೊಸ ಬಿರಿಯಾನಿ ಅಂಗಡಿ ತೆರೆದಿದೆ\n[0:05-0:09 screen] Meghana Foods"
    item = place(venue="Meghana Foods", venue_en="Meghana Foods", evidence="ಇಂದಿರಾನಗರದಲ್ಲಿ ಹೊಸ ಬಿರಿಯಾನಿ ಅಂಗಡಿ ತೆರೆದಿದೆ")
    assert extract("", transcript, NOW, llm=llm_with([item])).items[0].evidence_source == "speech"


def test_instagram_handle_is_not_a_venue():
    from app.core.grounding import looks_like_handle
    assert looks_like_handle("Tasteofbengaluru_") and looks_like_handle("@indiaeatmania") and looks_like_handle("taste.of.bengaluru")
    assert not looks_like_handle("Taste of Bengaluru") and not looks_like_handle("Toit") and not looks_like_handle("Dr. Reddy")


def test_location_suffix_is_stripped_and_duplicate_area_removed():
    from app.core.grounding import strip_location_suffix
    assert strip_location_suffix("Dolphins in Cox Town", "Cox Town", "Bengaluru") == "Dolphins"
    assert strip_location_suffix("Dolphins", "Cox Town") == "Dolphins"
    item = place(venue="Dolphins in Cox Town", venue_en="Dolphins in Cox Town", area="Cox Town", city="Bengaluru", evidence="Dolphins in Cox Town")
    res = extract("Dolphins in Cox Town", "", NOW, llm=llm_with([item]))
    assert res.items[0].venue == "Dolphins"
    same = place(venue="Vidyarthi Bhavan", venue_en="Vidyarthi Bhavan", area="Bangalore", city="Bangalore", evidence="Vidyarthi Bhavan")
    assert extract("Vidyarthi Bhavan", "", NOW, llm=llm_with([same])).items[0].area is None


def test_same_venue_as_place_and_offer_in_one_video_is_one_item_and_the_real_offer_wins():
    base = dict(category="food", venue="Siddiqui Kebab Centre", venue_en="Siddiqui Kebab Centre", city="Bangalore", confidence=0.9)
    p = dict(base, kind="place", title="Siddiqui Kebab Centre")
    o = dict(base, kind="offer", title="Kebab combo offer today only", duration_text="today only")
    res = extract("Siddiqui Kebab Centre", "", NOW, llm=llm_with([p, o]))
    assert [i.kind for i in res.items] == ["offer"]
    both_places = extract("Siddiqui Kebab Centre", "", NOW, llm=llm_with([p, dict(p, title="again")]))
    assert len(both_places.items) == 1


def test_a_menu_style_offer_does_not_replace_the_place():
    base = dict(category="food", venue="Sri Ganesh Bhavan", venue_en="Sri Ganesh Bhavan", city="Bangalore", confidence=0.9)
    res = extract("Sri Ganesh Bhavan", "", NOW, llm=llm_with([dict(base, kind="place", title="Sri Ganesh Bhavan"), dict(base, kind="offer", title="Full meals Rs 80")]))
    assert [i.kind for i in res.items] == ["place"]
