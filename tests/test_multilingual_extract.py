import json
from datetime import datetime

from app.services import extract as ex
from app.services.extract import extract

NOW = datetime(2026, 10, 22, 12, 0, 0)

TRANSCRIPT = (
    "[0:03 speech] ಇಂದಿರಾನಗರದಲ್ಲಿ ಹೊಸ ಬಿರಿಯಾನಿ ಅಂಗಡಿ ತೆರೆದಿದೆ\n"
    "[0:05-0:09 screen] Meghana Foods\n"
    "[0:08 speech] aaj sirf 99 rupaye mein full plate"
)


def llm_with(payload):
    calls = []

    def llm(system, user):
        calls.append((system, user))
        return json.dumps(payload)

    llm.calls = calls
    return llm


def item(**kw):
    base = {"kind": "place", "category": "food", "title": "New biryani shop", "venue": "ಮೇಘನಾ ಫುಡ್ಸ್",
            "venue_en": "Meghana Foods", "city": "Bengaluru", "area": "Indiranagar", "confidence": 0.9}
    base.update(kw)
    return base


def test_grounded_item_records_where_the_quote_was_found():
    llm = llm_with({"items": [item(evidence="Meghana Foods")], "signals": []})
    res = extract("", TRANSCRIPT, NOW, llm=llm)
    it = res.items[0]
    assert it.grounded is True and it.evidence_source == "screen" and it.evidence_at == 5
    assert it.venue_en == "Meghana Foods" and it.venue == "ಮೇಘನಾ ಫುಡ್ಸ್"
    assert res.stats["ungrounded_dropped"] == 0


def test_item_with_invented_quote_is_dropped_and_counted():
    llm = llm_with({"items": [item(evidence="Truffle Pizza Co is giving free pizza"), item(venue="X Cafe", venue_en="X Cafe",
                                  title="X Cafe", evidence="aaj sirf 99 rupaye mein full plate")], "signals": []})
    res = extract("X Cafe", TRANSCRIPT, NOW, llm=llm)
    assert [i.venue for i in res.items] == ["X Cafe"]
    assert res.stats["ungrounded_dropped"] == 1


def test_quote_in_the_original_script_is_accepted():
    llm = llm_with({"items": [item(evidence="ಇಂದಿರಾನಗರದಲ್ಲಿ ಹೊಸ ಬಿರಿಯಾನಿ ಅಂಗಡಿ ತೆರೆದಿದೆ")], "signals": []})
    assert extract("", TRANSCRIPT, NOW, llm=llm).items[0].evidence_source == "speech"


def test_item_without_a_quote_is_kept_but_unverified():
    res = extract("", TRANSCRIPT, NOW, llm=llm_with({"items": [item()], "signals": []}))
    assert res.items[0].grounded is None


def test_prompt_asks_for_original_script_english_fields_and_a_quote():
    llm = llm_with({"items": [], "signals": []})
    extract("cap", TRANSCRIPT, NOW, llm=llm)
    system, user = llm.calls[0]
    assert "any language" in system and "venue_en" in system and "evidence" in system
    assert "Caption:\ncap" in user and "[0:05-0:09 screen] Meghana Foods" in user


def test_long_transcript_is_read_in_chunks_and_results_are_merged():
    lines = [f"[{i // 60}:{i % 60:02d} speech] " + ("word " * 60) for i in range(0, 300)]
    lines[0] = "[0:00 speech] Meghana Foods " + "word " * 60
    transcript = "\n".join(lines)
    assert len(transcript) > ex.MAX_CHARS_PER_CALL
    replies = []

    def llm(system, user):
        replies.append(user)
        return json.dumps({"items": [item(title="Same shop", evidence="")], "signals": []})

    res = extract("", transcript, NOW, llm=llm)
    assert len(replies) > 1  # more than one call
    assert all(len(u) < ex.MAX_CHARS_PER_CALL + 500 for u in replies)
    assert len(res.items) == 1  # the same shop found in every chunk is kept once


def test_invented_venue_name_is_dropped_even_if_its_quote_is_real():
    llm = llm_with({"items": [item(venue="Truffle Pizza Co", venue_en="Truffle Pizza Co", title="Truffle Pizza Co",
                                   evidence="aaj sirf 99 rupaye mein full plate")], "signals": []})
    res = extract("", TRANSCRIPT, NOW, llm=llm)
    assert res.items == [] and res.stats["ungrounded_dropped"] == 1


def test_named_place_without_a_city_is_kept_but_an_unnamed_one_is_not():
    named = item(city=None, area=None, evidence="Meghana Foods")
    unnamed = item(venue=None, venue_en=None, title="A smash burger spot", city=None, area=None, evidence=None)
    res = extract("", TRANSCRIPT, NOW, llm=llm_with({"items": [named, unnamed], "signals": []}))
    assert [i.venue_en for i in res.items] == ["Meghana Foods"]
    no_name_in_city = item(venue=None, venue_en=None, title="Burger spot")  # has a city but no name
    assert extract("", TRANSCRIPT, NOW, llm=llm_with({"items": [no_name_in_city], "signals": []})).items == []


def test_prompt_tells_the_model_to_use_the_city_from_hashtags():
    llm = llm_with({"items": [], "signals": []})
    extract("cap #bengaluru", TRANSCRIPT, NOW, llm=llm)
    assert "hashtags" in llm.calls[0][0]
