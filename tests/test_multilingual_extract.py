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
    res = extract("", TRANSCRIPT, NOW, llm=llm)
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
