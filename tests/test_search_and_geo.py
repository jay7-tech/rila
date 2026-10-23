"""Runs on SQLite always, and on real Postgres (PostGIS + pgvector) when TEST_DATABASE_URL is set."""
import os
import zlib
from datetime import timedelta

import pytest
from sqlalchemy import text

from app import db as dbmod
from app.models import Item
from app.services import embeddings, query as q
from app.services import users as users_svc
from app.services.process import norm

from conftest import HERE, NOW, make_item


def fake_vec(s: str):
    """Deterministic bag-of-words vector standing in for the real model."""
    v = [0.0] * 384
    for w in norm(s).split():
        v[zlib.crc32(w.encode()) % 384] += 1.0
    return v


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, monkeypatch):
    if request.param == "postgres":
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            pytest.skip("set TEST_DATABASE_URL to a PostGIS+pgvector database to run this")
        dbmod.init_db(url)
        dbmod.reset_postgres()
    else:
        dbmod.init_db("sqlite://")
    monkeypatch.setattr(embeddings, "embed_query", lambda t: fake_vec(t))
    session = dbmod.SessionLocal()
    user = users_svc.create_user(session, 111, "Asha")
    session.commit()
    yield session, user
    session.close()
    if request.param == "sqlite":
        dbmod.Base.metadata.drop_all(dbmod._engine)


def add(session, user, title, **kw):
    item = make_item(session, user, title, **kw)
    item.embedding = fake_vec(embeddings.item_text(item))
    session.commit()
    return item


def test_nearby_uses_radius_and_keeps_unlocated(env):
    session, user = env
    add(session, user, "Close cafe", dlat=0.002, ends_h=50)
    add(session, user, "Far cafe", dlat=0.2, ends_h=50)
    online = Item(user_id=user.id, kind="info", category="scheme", title="Online scheme", normalized="online scheme",
                  created_at=NOW, ends_at=NOW + timedelta(hours=50))
    session.add(online)
    session.commit()
    titles = {i.title for i in q.nearby_candidates(session, user, HERE[0], HERE[1], 3000)}
    assert "Close cafe" in titles and "Online scheme" in titles
    if dbmod.is_postgres(session.get_bind()):
        assert "Far cafe" not in titles  # PostGIS filtered it in the database
    recs = q.recommend_for_user(session, user, HERE[0], HERE[1], NOW, 3000)
    assert [r["title"] for r in recs][0] == "Close cafe" and "Far cafe" not in [r["title"] for r in recs]


def test_semantic_search_ranks_by_meaning_and_hides_expired(env):
    session, user = env
    add(session, user, "Cold brew coffee deal", category="food", ends_h=50, summary="two for one coffee")
    add(session, user, "Python programming workshop", category="education", ends_h=50, summary="free coding class")
    add(session, user, "Old coffee offer", category="food", ends_h=-5, summary="coffee")
    res = q.search_saved(session, user, "coffee", NOW)
    assert [r["title"] for r in res] == ["Cold brew coffee deal"]
    res2 = q.search_saved(session, user, "free coding class", NOW)
    assert res2[0]["title"] == "Python programming workshop"


def test_search_falls_back_to_keywords_when_model_unavailable(env, monkeypatch):
    session, user = env
    add(session, user, "Dosa festival", ends_h=50)

    def boom(t):
        raise RuntimeError("model download blocked")

    monkeypatch.setattr(embeddings, "embed_query", boom)
    assert [r["title"] for r in q.search_saved(session, user, "dosa", NOW)] == ["Dosa festival"]


def test_search_is_private_to_each_user(env):
    session, user = env
    other = users_svc.create_user(session, 222, "Ravi")
    session.commit()
    add(session, other, "Secret coffee place", ends_h=50)
    assert q.search_saved(session, user, "coffee", NOW) == []


def test_embedding_round_trips_with_full_dimension(env):
    session, user = env
    item = add(session, user, "Round trip", ends_h=50)
    session.expire_all()
    got = session.get(Item, item.id)
    assert len(got.embedding) == 384 and abs(got.embedding[zlib.crc32(b"round") % 384]) > 0


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs Postgres")
def test_postgres_schema_has_geography_and_indexes():
    dbmod.init_db(os.environ["TEST_DATABASE_URL"])
    dbmod.init_db(os.environ["TEST_DATABASE_URL"])  # migrations are idempotent
    with dbmod.get_engine().connect() as c:
        idx = {r[0] for r in c.execute(text("SELECT indexname FROM pg_indexes WHERE tablename IN ('items','signals')"))}
        col = c.execute(text("SELECT udt_name FROM information_schema.columns WHERE table_name='items' AND column_name='geog'")).scalar()
        ext = {r[0] for r in c.execute(text("SELECT extname FROM pg_extension"))}
    assert {"ix_items_geog", "ix_items_embedding", "ix_signals_type_time"} <= idx
    assert col == "geography" and {"postgis", "vector"} <= ext
