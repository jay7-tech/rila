from datetime import datetime, timedelta

from app.evalkit import recs_eval as R

T0 = datetime(2026, 11, 1)


def item(cat, title, day=0, **k):
    return dict({"category": cat, "kind": "place", "title": title, "created_at": T0 + timedelta(days=day)}, **k)


def history(n=12):
    """A user who only goes to food places: 12 foods and 12 parks exist, they like the food ones one by one."""
    items, events = {}, []
    for i in range(n):
        items[i] = item("food", f"cafe dosa {i}")
        items[100 + i] = item("attraction", f"park walk {i}")
        events.append(("like", i, T0 + timedelta(days=1 + i)))
        events.append(("dismiss", 100 + i, T0 + timedelta(days=1 + i, hours=1)))
    return items, events


def test_taste_beats_chance_on_a_consistent_user():
    items, events = history()
    r = R.evaluate(items, events, k=3)
    assert r["steps"] >= 6 and r["taste"]["hit"] > r["chance_hit"] and r["taste"]["mrr"] > 0.5


def test_only_the_past_is_used():
    items, events = history(6)
    # an item created after the action must not be a candidate for it
    items[999] = item("food", "future cafe", day=400)
    with_future = R.evaluate(items, events, k=3)
    del items[999]
    without = R.evaluate(items, events, k=3)
    assert with_future == without  # a cafe that did not exist yet changes nothing


def test_not_enough_history_says_so():
    r = R.evaluate({1: item("food", "a")}, [("like", 1, T0)], k=3)
    assert r["steps"] == 0 and "Not enough" in R.report(r)


def test_report_mentions_all_rankers():
    items, events = history()
    text = R.report(R.evaluate(items, events, k=3))
    assert "taste model" in text and "category counts" in text and "chance" in text
