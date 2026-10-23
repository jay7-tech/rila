import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db as dbmod  # noqa: E402
from app.models import Item, User  # noqa: E402
from app.services import users as users_svc  # noqa: E402

NOW = datetime(2026, 10, 22, 12, 0, 0)
HERE = (12.9716, 77.6412)  # Indiranagar, Bangalore


@pytest.fixture()
def session():
    dbmod.init_db("sqlite://")  # fresh in-memory database
    s = dbmod.SessionLocal()
    yield s
    s.close()
    dbmod.Base.metadata.drop_all(dbmod._engine)


@pytest.fixture()
def user(session):
    u = users_svc.create_user(session, 111, "Asha")
    session.commit()
    return u


def make_item(session, user, title, category="food", dlat=0.0, ends_h=None, starts_h=None, **kw):
    item = Item(
        user_id=user.id, kind=kw.pop("kind", "offer"), category=category, title=title,
        normalized=title.lower(), city="Bengaluru", lat=HERE[0] + dlat, lon=HERE[1], geo_status="confirmed",
        starts_at=NOW + timedelta(hours=starts_h) if starts_h is not None else None,
        ends_at=NOW + timedelta(hours=ends_h) if ends_h is not None else None,
        created_at=NOW, **kw,
    )
    session.add(item)
    session.commit()
    return item
