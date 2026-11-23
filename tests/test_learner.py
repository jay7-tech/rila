import importlib.util
import os
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient

from app import api as apimod
from app.models import Event
from app.recs import learner
from app.services import query as q
from conftest import HERE, NOW, make_item

DAY = timedelta(days=1)


def obs(action, key, days_ago=0):
    return (action, key, NOW - days_ago * DAY)


def test_no_data_means_no_opinion():
    assert learner.scores([], NOW) == {}
    assert learner.effective_interests({}, [], NOW, ["food"])["food"] == pytest.approx(learner.EXPLORE, abs=0.001)


def test_likes_raise_and_dismissals_lower_the_score_smoothly():
    one = learner.scores([obs("like", "food")], NOW)["food"][0]
    three = learner.scores([obs("like", "food")] * 3, NOW)["food"][0]
    mixed = learner.scores([obs("like", "food")] * 3 + [obs("dismiss", "food")] * 3, NOW)["food"][0]
    assert 0.3 < one < 0.35 and three > 0.55 and abs(mixed) < 0.01  # one tap does not swing the ranking
    assert learner.scores([obs("dismiss", "event")] * 4, NOW)["event"][0] < -0.5


def test_visit_counts_more_than_open_and_old_actions_fade():
    assert learner.scores([obs("visit", "a")], NOW)["a"][0] > learner.scores([obs("open", "a")], NOW)["a"][0]
    fresh = learner.scores([obs("like", "a")], NOW)["a"][1]
    old = learner.scores([obs("like", "a", 60)], NOW)["a"][1]
    assert old == pytest.approx(fresh / 4, rel=0.01)  # two half-lives


def test_effective_interest_is_bounded_and_blends_the_saved_habit():
    out = learner.effective_interests({"food": 1.0}, [obs("like", "food")] * 20, NOW)
    assert 0.9 <= out["food"] <= 1.0
    assert learner.effective_interests({"food": 0.4}, [obs("dismiss", "food")] * 10, NOW)["food"] == 0.0


def test_exploration_bonus_shrinks_with_evidence():
    assert learner.explore_bonus(0) > learner.explore_bonus(10) > 0


def test_explain_lists_strongest_first_with_evidence():
    rows = learner.explain({"food": 0.2}, [obs("like", "event")] * 3, NOW)
    assert rows[0]["category"] == "event" and rows[0]["evidence"] > 2


def test_creator_trust_starts_neutral_and_moves_with_evidence():
    assert learner.trust([], NOW) == 0.5
    assert learner.trust([], NOW, grounded=8, ungrounded=0) > 0.85
    assert learner.trust([], NOW, grounded=0, ungrounded=6) < 0.15
    assert learner.trust([obs("dismiss", None)] * 3, NOW) < 0.3


@pytest.fixture()
def client(session):
    return TestClient(apimod.create_app())


def test_feedback_changes_recommendations_and_hides_dismissed(client, session, user):
    a = make_item(session, user, "Cafe A", category="food", kind="place", venue="A", source_url="a")
    b = make_item(session, user, "Event B", category="event", kind="place", venue="B", source_url="b")
    h = {"X-Rilla-Token": user.token}
    near = lambda: [i["title"] for i in client.get(f"/api/near?lat={HERE[0]}&lon={HERE[1]}", headers=h).json()["items"]]
    assert set(near()) == {"Cafe A", "Event B"}
    assert client.post(f"/api/items/{b.id}/feedback", json={"action": "dismiss"}, headers=h).status_code == 200
    assert near() == ["Cafe A"]  # dismissed item is gone
    assert client.post(f"/api/items/{a.id}/feedback", json={"action": "visit"}, headers=h).status_code == 200
    session.refresh(a)
    assert a.visited is True
    interests = {r["category"]: r for r in client.get("/api/me/interests", headers=h).json()["interests"]}
    assert interests["food"]["learned"] > 0 > interests["event"]["learned"]


def test_feedback_validates_action_and_ownership(client, session, user):
    from app.services import users as users_svc
    other = users_svc.create_user(session, 222, "Other")
    session.commit()
    item = make_item(session, other, "Not yours", kind="place", venue="X", source_url="x")
    h = {"X-Rilla-Token": user.token}
    assert client.post(f"/api/items/{item.id}/feedback", json={"action": "like"}, headers=h).status_code == 404
    mine = make_item(session, user, "Mine", kind="place", venue="Y", source_url="y")
    assert client.post(f"/api/items/{mine.id}/feedback", json={"action": "explode"}, headers=h).status_code == 422


def test_migration_0003_is_idempotent_and_creates_events():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("m0003", os.path.join(here, "..", "migrations", "versions", "0003_events.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(sa.text("create table users (id integer primary key)"))
        c.execute(sa.text("create table items (id integer primary key)"))
    for _ in range(2):  # second run must do nothing
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                m.upgrade()
    assert {"action", "category", "creator"} <= {c["name"] for c in sa.inspect(engine).get_columns("events")}
