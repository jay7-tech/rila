import json

from app.evalkit import label as L


def case(**k):
    return dict({"id": "yt_a", "labeled": False, "language": "kn", "source": "https://y/a", "caption": "cap", "transcript": "try Meghana Foods biryani",
                 "expected": {"items": [{"kind": "place", "venue": "Meghana Foods", "names": [], "city": "Bengaluru"}, {"kind": "offer", "venue": "Junk", "names": [], "city": None}]},
                 "draft_from_model": [{"venue": "Meghana Foods", "evidence": "try Meghana Foods"}]}, **k)


def test_edit_commands():
    c = case()
    assert L.apply_command(c, "d 2")[0] == "edit" and len(c["expected"]["items"]) == 1
    assert L.apply_command(c, "k 1 offer")[0] == "edit" and c["expected"]["items"][0]["kind"] == "offer"
    assert "place, offer or info" in L.apply_command(c, "k 1 shop")[1]
    L.apply_command(c, "v 1 Meghana Foods Indiranagar")
    assert c["expected"]["items"][0]["venue"] == "Meghana Foods Indiranagar"
    L.apply_command(c, "a place | Third Wave | Bengaluru")
    assert c["expected"]["items"][-1] == {"kind": "place", "venue": "Third Wave", "names": [], "city": "Bengaluru"}
    L.apply_command(c, "a info | Water cut notice")
    assert c["expected"]["items"][-1]["city"] is None
    assert "does not exist" in L.apply_command(c, "d 9")[1] and "does not exist" in L.apply_command(c, "d x")[1]
    assert "use:" in L.apply_command(c, "a thing | x")[1] and "not understood" in L.apply_command(c, "zzz")[1]


def test_outcomes():
    c = case()
    assert L.apply_command(c, "")[0] == "done" and len(c["expected"]["items"]) == 2
    assert L.apply_command(c, "n")[0] == "done" and c["expected"]["items"] == []
    assert [L.apply_command(case(), x)[0] for x in ("s", "q", "t")] == ["skip", "quit", "show"]


def test_label_case_marks_labelled_only_when_confirmed():
    c = case()
    answers = iter(["d 2", "y"])
    out = []
    assert L.label_case(c, lambda _: next(answers), out.append) == "done"
    assert c["labeled"] is True and len(c["expected"]["items"]) == 1 and any("proof" in o for o in out)
    c2 = case()
    assert L.label_case(c2, lambda _: "s", lambda *_: None) == "skip" and c2["labeled"] is False


def test_run_saves_labelled_cases_skips_synthetic_and_stops_on_quit(tmp_path):
    for name, extra in (("a", {}), ("b", {}), ("c", {}), ("syn", {"synthetic": True}), ("done", {"labeled": True})):
        (tmp_path / f"{name}.json").write_text(json.dumps(case(id=name, **extra)), encoding="utf-8")
    answers = iter(["", "s", "q"])
    stats = L.run(str(tmp_path), ask=lambda _: next(answers), say=lambda *_: None)
    assert stats == {"labelled": 1, "skipped": 1, "left": 1}
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["labeled"] is True
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))["labeled"] is False
    assert [p.split("/")[-1].split("\\")[-1] for p, _ in L.pending(str(tmp_path))] == ["b.json", "c.json"]
