import json
from types import SimpleNamespace

from app.evalkit import metrics, runner


def pred(venue, kind="place", grounded=True, **kw):
    return SimpleNamespace(venue=venue, venue_en=kw.get("venue_en"), title=kw.get("title", venue), kind=kind, grounded=grounded)


def test_matching_is_one_to_one_and_name_fuzzy():
    exp = [{"kind": "place", "venue": "Meghana Foods"}, {"kind": "offer", "title": "Free dosa"}]
    sc = metrics.score_case("c", [pred("Meghana Food"), pred("Toit", grounded=None)], exp)
    assert (sc.tp, sc.fp, sc.fn) == (1, 1, 1)
    assert sc.extra == ["toit"] and sc.missed == ["free dosa"]
    assert sc.grounded == 1 and sc.unverified == 1


def test_alternate_spellings_in_other_scripts_match():
    exp = [{"kind": "place", "venue": "मेघना फूड्स", "names": ["Meghana Foods"]}]
    assert metrics.score_case("c", [pred("x", venue_en="Meghana Foods")], exp).tp == 1
    assert metrics.score_case("c", [pred("मेघना फूड्स")], exp).tp == 1


def test_wrong_kind_counts_as_match_but_not_kind_ok():
    sc = metrics.score_case("c", [pred("Cafe Nine", kind="offer")], [{"kind": "place", "venue": "Cafe Nine"}])
    assert sc.tp == 1 and sc.kind_ok == 0


def test_summary_arithmetic():
    a = metrics.score_case("a", [pred("A One"), pred("B Two")], [{"kind": "place", "venue": "A One"}], dropped=1)
    b = metrics.score_case("b", [pred("C Three")], [{"kind": "place", "venue": "C Three"}, {"kind": "place", "venue": "D Four"}])
    m = metrics.summarize([a, b])
    assert (m["tp"], m["fp"], m["fn"]) == (2, 1, 1)
    assert m["precision"] == round(2 / 3, 4) and m["recall"] == round(2 / 3, 4) and m["f1"] == round(2 / 3, 4)
    assert m["ungrounded_rate"] == round(1 / 4, 4)
    assert metrics.summarize([])["f1"] == 0.0


def test_runner_on_synthetic_cases_with_a_fake_model_and_cache(tmp_path):
    cases = runner.load_cases(include_synthetic=True)
    assert {c["id"] for c in cases} >= {"synthetic_en_text", "synthetic_hi_speech", "synthetic_kn_screen"}
    assert runner.load_cases() == []  # synthetic cases are never counted unless asked for
    calls = []

    def fake(system, user):
        calls.append(1)
        return json.dumps({"items": [{"kind": "place", "category": "food", "title": "Meghana Foods", "venue_en": "Meghana Foods",
                                      "city": "Bengaluru", "evidence": "Meghana Foods", "confidence": 0.9}], "signals": []})

    llm = runner.cached(fake, str(tmp_path / "cache"))
    first = runner.run(cases, llm)
    assert len(calls) == 3
    # the Hindi case quotes "Meghana Foods", which is not in its Devanagari transcript: caught as ungrounded
    by = {c["case_id"]: c for c in first["cases"]}
    assert by["synthetic_en_text"]["tp"] == 1 and by["synthetic_kn_screen"]["tp"] == 1
    assert by["synthetic_hi_speech"]["tp"] == 0 and first["summary"]["ungrounded_rate"] > 0
    second = runner.run(cases, llm)
    assert len(calls) == 3 and second["summary"] == first["summary"]  # served from the cache, identical
    assert "F1" in runner.report(first)
