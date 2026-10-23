import json
from datetime import datetime

from app.services.extract import extract, parse_dt, parse_start

NOW = datetime(2026, 10, 22, 12, 0, 0)


def llm_returning(*replies):
    calls = []

    def llm(system, user):
        calls.append((system, user))
        return replies[min(len(calls) - 1, len(replies) - 1)]

    llm.calls = calls
    return llm


GOOD = json.dumps({
    "items": [
        {"kind": "offer", "category": "food", "title": "Buy one get one on cold brews", "venue": "Third Wave Coffee",
         "city": "Bengaluru", "area": "Indiranagar", "price_text": "2 for 1", "duration_text": "weekdays 3-6pm",
         "ends_at": "2026-10-25", "confidence": 0.9},
        {"kind": "place", "category": "food", "title": "toit", "venue": "toit", "city": "Bengaluru", "confidence": 0.9},
        {"kind": "place", "category": "food", "title": "Nameless Cafe", "city": None, "area": None, "confidence": 0.95},
        {"kind": "offer", "category": "store", "title": "Sale", "venue": "Shop", "city": "Bengaluru", "confidence": 0.4},
    ],
    "signals": [
        {"signal_type": "crowd", "area_name": "Kudle Beach", "city": "Gokarna", "value": "packed", "confidence": 0.85},
        {"signal_type": "weather", "value": "rain", "confidence": 0.9},
    ],
})


def test_filters_low_confidence_unplaceable_and_noise():
    res = extract("cap", "tr", NOW, llm=llm_returning(GOOD), min_confidence=0.7)
    assert [i.title for i in res.items] == ["Buy one get one on cold brews"]
    assert [s.value for s in res.signals] == ["packed"]  # the one with no area is dropped


def test_prompt_contains_dates_for_relative_words():
    llm = llm_returning(GOOD)
    extract("cap", "tr", NOW, posted_at=datetime(2026, 10, 21), llm=llm)
    system = llm.calls[0][0]
    assert "2026-10-22" in system and "2026-10-21" in system


def test_retries_once_on_invalid_json_then_succeeds():
    llm = llm_returning("not json", GOOD)
    res = extract("cap", "tr", NOW, llm=llm, min_confidence=0.7)
    assert len(llm.calls) == 2 and "invalid" in llm.calls[1][1] and len(res.items) == 1


def test_gives_empty_result_when_always_invalid():
    res = extract("cap", "tr", NOW, llm=llm_returning("nope"), min_confidence=0.7)
    assert res.items == [] and res.signals == []


def test_place_gets_venue_from_title():
    reply = json.dumps({"items": [{"kind": "place", "category": "food", "title": "Dyu Art Cafe", "city": "Bengaluru", "confidence": 0.9}]})
    res = extract("c", "t", NOW, llm=llm_returning(reply), min_confidence=0.7)
    assert res.items[0].venue == "Dyu Art Cafe"


def test_date_parsing():
    assert parse_dt("2026-10-25") == datetime(2026, 10, 25, 23, 59)
    assert parse_start("2026-10-25") == datetime(2026, 10, 25, 0, 0)
    assert parse_dt("2026-10-25T18:30") == datetime(2026, 10, 25, 18, 30)
    assert parse_dt("sunday") is None and parse_dt(None) is None


# ---- Groq model fallback and lenient JSON -------------------------------------------------

import httpx
import groq
import pytest

from app.services import extract as ex


def _status_error(cls, code):
    req = httpx.Request("POST", "https://api.groq.com/x")
    return cls("boom", response=httpx.Response(code, request=req), body=None)


class _FakeGroq:
    """Stands in for groq.Groq; behaviour per model is scripted."""

    script: dict = {}
    calls: list = []

    def __init__(self, api_key=None):
        self.chat = self
        self.completions = self

    def create(self, **kw):
        _FakeGroq.calls.append((kw["model"], "response_format" in kw, kw.get("reasoning_effort")))
        out = _FakeGroq.script[kw["model"]]
        if isinstance(out, Exception):
            raise out
        if callable(out):
            out = out(kw)
            if isinstance(out, Exception):
                raise out

        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": out})()})()]

        return R()


@pytest.fixture
def fake_groq(monkeypatch):
    _FakeGroq.calls = []
    monkeypatch.setattr(groq, "Groq", _FakeGroq)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_FALLBACK_MODELS", "openai/gpt-oss-20b,llama-3.1-8b-instant")
    return _FakeGroq


def test_groq_moves_to_next_model_when_primary_is_gone(fake_groq):
    fake_groq.script = {
        "openai/gpt-oss-120b": _status_error(groq.NotFoundError, 404),
        "openai/gpt-oss-20b": '{"items": [], "signals": []}',
    }
    assert ex._groq_json("s", "u") == '{"items": [], "signals": []}'
    assert [c[0] for c in fake_groq.calls] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert fake_groq.calls[1][2] == "low"  # gpt-oss gets low reasoning effort


def test_groq_retries_without_json_mode_when_rejected(fake_groq):
    fake_groq.script = {
        "openai/gpt-oss-120b": lambda kw: _status_error(groq.BadRequestError, 400) if "response_format" in kw else "{}",
    }
    assert ex._groq_json("s", "u") == "{}"
    assert [c[1] for c in fake_groq.calls] == [True, False]


def test_groq_raises_when_every_model_fails(fake_groq):
    err = _status_error(groq.RateLimitError, 429)
    fake_groq.script = {m: err for m in ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant")}
    with pytest.raises(groq.RateLimitError):
        ex._groq_json("s", "u")
    assert len(fake_groq.calls) == 3


def test_lenient_json_accepts_fences_and_surrounding_text():
    assert ex._loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}
    assert ex._loads_lenient('Here you go: {"a": 1} hope that helps') == {"a": 1}
    with pytest.raises(Exception):
        ex._loads_lenient("no json here")
