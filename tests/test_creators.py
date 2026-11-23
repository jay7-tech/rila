from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import api as apimod
from app.models import Document, Event
from app.recs.offers import Offer
from app.recs.ranking import recommend
from app.services import creators as creators_svc
from conftest import HERE, NOW, make_item


def offer(i, creator, dlat=0.001):
    return Offer(id=i, title=f"T{i}", category="food", lat=HERE[0] + dlat, lon=HERE[1], creator=creator, confidence=0.8)


def test_trusted_creator_ranks_above_an_untrusted_one_otherwise_equal():
    recs = recommend([offer(1, "bad"), offer(2, "good")], HERE[0], HERE[1], NOW, trust={"good": 0.95, "bad": 0.1})
    assert [r.offer.id for r in recs] == [2, 1] and "reliable creator" in recs[0].reasons


def test_unknown_creator_is_neutral_and_effect_is_bounded():
    base = recommend([offer(1, None)], HERE[0], HERE[1], NOW)[0].score
    neutral = recommend([offer(1, "x")], HERE[0], HERE[1], NOW, trust={"x": 0.5})[0].score
    best = recommend([offer(1, "x")], HERE[0], HERE[1], NOW, trust={"x": 1.0})[0].score
    worst = recommend([offer(1, "x")], HERE[0], HERE[1], NOW, trust={"x": 0.0})[0].score
    assert neutral == pytest.approx(base, rel=0.01) and best == pytest.approx(base * 1.15, rel=0.01) and worst == pytest.approx(base * 0.85, rel=0.01)


@pytest.fixture()
def client(session):
    return TestClient(apimod.create_app())


def test_stats_combine_grounding_drops_and_reactions(session, user):
    a = make_item(session, user, "Good cafe", kind="place", venue="A", source_url="r1", creator="honest", grounded=True)
    make_item(session, user, "Other", kind="place", venue="B", source_url="r2", creator="sloppy", grounded=True)
    session.add(Document(user_id=user.id, source_url="r2", timeline=[], meta={"creator": "sloppy", "ungrounded_dropped": 5}, created_at=NOW))
    session.add(Event(user_id=user.id, item_id=a.id, action="like", category="food", creator="honest", created_at=NOW))
    session.commit()
    rows = {r["creator"]: r for r in creators_svc.creator_stats(session, user, NOW)}
    assert rows["honest"]["trust"] == 0.75 and rows["sloppy"]["trust"] == 0.25
    assert rows["sloppy"]["dropped"] == 5 and rows["honest"]["likes"] == 1 and rows["honest"]["grounded"] == 1
    assert next(iter(creators_svc.trust_map(session, user, NOW))) == "honest"


def test_creators_endpoint_is_per_user(client, session, user):
    from app.services import users as users_svc
    other = users_svc.create_user(session, 222, "Other")
    session.commit()
    make_item(session, user, "Mine", kind="place", venue="A", source_url="r1", creator="mine")
    make_item(session, other, "Theirs", kind="place", venue="B", source_url="r2", creator="theirs")
    r = client.get("/api/creators", headers={"X-Rilla-Token": user.token})
    assert [c["creator"] for c in r.json()["creators"]] == ["mine"]


def test_process_records_creator_and_drop_count_on_the_document(session, user):
    from app.core.document import Segment, VideoDocument
    from app.services.extract import ExtractedItem, ExtractionResult
    from app.services.ingest import Ingested, from_document
    from app.services.process import process_url
    from sqlalchemy import select
    doc = VideoDocument(kind="video", creator="foodie", segments=[Segment(source="caption", text="Toit")])
    ing = from_document(doc)
    ing.creator = "foodie"
    ex = ExtractionResult(items=[ExtractedItem(kind="place", title="Toit", venue="Toit", venue_en="Toit", city="Bengaluru", confidence=0.9)],
                          stats={"ungrounded_dropped": 2})
    process_url(session, user, "https://youtu.be/x", NOW, lambda u: ing, lambda *a: ex,
                lambda s, n, w: (12.9, 77.6, "confirmed"), lambda s, w: (12.9, 77.6, "approx"), None)
    session.commit()
    meta = session.scalars(select(Document)).first().meta
    assert meta["creator"] == "foodie" and meta["ungrounded_dropped"] == 2 and meta["kept"] == 1
