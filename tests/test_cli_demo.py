import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import api as apimod
from app import cli
from app.models import Event, Item, User
from app.services import demo as demo_svc
from conftest import HERE, NOW


@pytest.fixture()
def client(session):
    return TestClient(apimod.create_app())


@pytest.fixture()
def use_session(session, monkeypatch):
    """Run the CLI against the test database."""
    class Ctx:
        def __enter__(self_inner):
            return session

        def __exit__(self_inner, *a):
            return False

    monkeypatch.setattr(cli, "_db", lambda: Ctx())
    return session


def test_link_creates_once_and_is_case_insensitive(use_session, capsys):
    assert cli.main(["link", "Suresh"]) == 0
    first = capsys.readouterr().out.strip().splitlines()[-1]
    assert "/?t=" in first
    assert cli.main(["link", "suresh"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == first
    assert len(list(use_session.scalars(select(User).where(User.name.ilike("suresh"))))) == 1


def test_status_prints_counts_without_secrets(use_session, capsys, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecret")
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "users" in out and "jobs" in out and "gsk_supersecret" not in out


def test_demo_needs_an_existing_user_and_is_idempotent_and_clearable(use_session, user, capsys):
    assert cli.main(["demo", "Nobody"]) == 1
    assert cli.main(["demo", user.name]) == 0
    count = lambda: len(list(use_session.scalars(select(Item).where(Item.user_id == user.id))))
    after_first = count()
    assert after_first >= 10 and "Added" in capsys.readouterr().out
    assert cli.main(["demo", user.name]) == 0 and count() == after_first  # second run adds nothing
    assert "already" in capsys.readouterr().out
    assert cli.main(["demo", user.name, "--clear"]) == 0
    assert count() == 0 and not list(use_session.scalars(select(Event)))


def test_clear_leaves_real_items_alone(use_session, user):
    from conftest import make_item
    real = make_item(use_session, user, "Real cafe", kind="place", venue="Real", source_url="https://youtu.be/real")
    demo_svc.seed_demo(use_session, user, NOW, HERE)
    demo_svc.clear_demo(use_session, user)
    assert [i.id for i in use_session.scalars(select(Item).where(Item.user_id == user.id))] == [real.id]


def test_demo_data_exercises_every_feature_through_the_api(client, use_session, user):
    from app.models import utcnow
    demo_svc.seed_demo(use_session, user, utcnow(), HERE)  # the API uses the real clock, so seed with it
    h = {"X-Rilla-Token": user.token}
    # Places: Meghana Foods is one place built from three reels
    places = {p["name"]: p for p in client.get("/api/places", headers=h).json()["places"]}
    assert places["Meghana Foods"]["reels"] == 3 and places["Meghana Foods"]["live_offers"]
    # Near me: the deal ending soonest outranks a plain place, and the dismissed sale is hidden after the user's reactions
    near = client.get(f"/api/near?lat={HERE[0]}&lon={HERE[1]}&radius=8000&limit=20", headers=h).json()["items"]
    assert near and all(i["status"] != "expired" for i in near)
    # You: interests learned from the demo reactions, creators ordered by trust
    interests = {r["category"]: r for r in client.get("/api/me/interests", headers=h).json()["interests"]}
    assert interests["food"]["learned"] > 0
    creators = client.get("/api/creators", headers=h).json()["creators"]
    assert creators[0]["creator"] == "bengaluru.eats" and creators[-1]["creator"] == "clickbait.deals"
    assert creators[-1]["trust"] < 0.3 < creators[0]["trust"]
    # Plan: a route over the demo places fits in a few hours
    plan = client.get(f"/api/plan?lat={HERE[0]}&lon={HERE[1]}&minutes=300", headers=h).json()
    assert len(plan["stops"]) >= 3 and plan["total_minutes"] <= 300
