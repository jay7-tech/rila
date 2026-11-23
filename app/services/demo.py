"""Sample data so every screen can be tried without watching a single reel.

Everything created here has a source link under DEMO_PREFIX, so `clear_demo` removes exactly it and nothing else.
The set is built to show each feature: one venue in three reels (Places merges them), a deal ending soon (Near me),
offers with windows (Plan), several creators with different track records (You), and a few reactions.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Document, Event, Item, User

DEMO_PREFIX = "https://demo.rilla.local/"
DEFAULT_CENTRE = (12.9716, 77.6412)  # Indiranagar, Bengaluru

# name, kind, category, creator, d_lat, d_lon (degrees from centre), price, summary, ends_in_hours, reel
_ROWS = [
    ("Meghana Foods", "place", "food", "bengaluru.eats", 0.004, 0.003, "Rs 350 biryani", "Andhra-style biryani, always a queue.", None, "r1"),
    ("Meghana Foods", "place", "food", "foodie.priya", 0.004, 0.003, "Rs 399 for two", "Boneless chicken biryani is the pick.", None, "r2"),
    ("Meghana Foods", "offer", "food", "bengaluru.eats", 0.004, 0.003, "1+1 on starters", "Buy one starter, get one free this week.", 30, "r3"),
    ("Toit", "offer", "food", "foodie.priya", -0.003, 0.006, "Rs 299 pitcher", "Weekday pitcher offer until 7 pm.", 6, "r4"),
    ("Third Wave Coffee", "place", "food", "bengaluru.eats", 0.009, -0.004, "Rs 180 cold brew", "Quiet corner for working.", None, "r5"),
    ("Cubbon Park Walk", "place", "attraction", "weekend.walks", -0.012, -0.010, None, "Morning walk, bamboo grove at the north end.", None, "r6"),
    ("Chaos Comedy Night", "offer", "event", "weekend.walks", 0.002, -0.009, "Rs 499 ticket", "Stand-up night this Saturday.", 52, "r7"),
    ("Bluebell Book Sale", "offer", "store", "clickbait.deals", 0.006, 0.012, "Up to 70% off", "Clearance sale at the old library.", 20, "r8"),
    ("Corner House", "place", "food", "bengaluru.eats", -0.008, 0.002, "Rs 220 sundae", "Death by Chocolate since the 1980s.", None, "r9"),
    ("Vidyarthi Bhavan", "place", "food", "foodie.priya", -0.014, -0.006, "Rs 90 dosa", "Benne masala dosa, cash only.", None, "r10"),
]
# creator -> (quotes verified, quotes the video check dropped)
_RECORD = {"bengaluru.eats": (6, 0), "foodie.priya": (4, 1), "weekend.walks": (2, 0), "clickbait.deals": (1, 6)}
_REACTIONS = [("Meghana Foods", "like", 2), ("Toit", "like", 1), ("Cubbon Park Walk", "visit", 1), ("Bluebell Book Sale", "dismiss", 2)]


def seed_demo(session: Session, user: User, now: datetime, centre: Optional[Tuple[float, float]] = None) -> int:
    """Create the sample items for this user. Returns how many were created (0 if already seeded)."""
    if session.scalars(select(Item.id).where(Item.user_id == user.id, Item.source_url.like(DEMO_PREFIX + "%")).limit(1)).first():
        return 0
    lat0, lon0 = centre or ((user.last_lat, user.last_lon) if user.last_lat is not None else DEFAULT_CENTRE)
    made, by_name = 0, {}
    for creator, (grounded, dropped) in _RECORD.items():
        session.add(Document(user_id=user.id, kind="video", source_url=DEMO_PREFIX + "creator/" + creator, timeline=[],
                             meta={"creator": creator, "ungrounded_dropped": dropped, "demo": True}, created_at=now))
    for name, kind, cat, creator, dlat, dlon, price, summary, hours, reel in _ROWS:
        item = Item(
            user_id=user.id, kind=kind, category=cat, title=name if kind == "place" else f"{name}: {price}",
            normalized=name.lower(), venue=name, venue_en=name, city="Bengaluru", area=None, summary=summary, price_text=price,
            ends_at=now + timedelta(hours=hours) if hours else None, lat=lat0 + dlat, lon=lon0 + dlon, geo_status="confirmed",
            source_url=DEMO_PREFIX + reel, creator=creator, confidence=0.85, grounded=True,
            evidence=f"Have you tried {name}?", evidence_source="speech", evidence_at=4.0, created_at=now - timedelta(days=int(reel[1:]) % 5),
        )
        session.add(item)
        by_name.setdefault(name, item)
        made += 1
    session.flush()
    # grounded counts per creator come from items; top up so the demo record matches _RECORD
    for creator, (grounded, _dropped) in _RECORD.items():
        have = sum(1 for r in _ROWS if r[3] == creator)
        for k in range(max(grounded - have, 0)):
            session.add(Item(user_id=user.id, kind="place", category="food", title=f"{creator} extra {k}", normalized=f"{creator} extra {k}",
                             venue=f"{creator} extra {k}", venue_en=f"{creator} extra {k}", city="Bengaluru", lat=None, lon=None, geo_status="none",
                             source_url=f"{DEMO_PREFIX}extra/{creator}/{k}", creator=creator, confidence=0.8, grounded=True, created_at=now))
    for name, action, times in _REACTIONS:
        it = by_name[name]
        for k in range(times):
            session.add(Event(user_id=user.id, item_id=it.id, action=action, category=it.category, creator=it.creator,
                              created_at=now - timedelta(days=k)))
    session.commit()
    return made


def clear_demo(session: Session, user: User) -> int:
    ids = list(session.scalars(select(Item.id).where(Item.user_id == user.id, Item.source_url.like(DEMO_PREFIX + "%"))))
    if ids:
        session.execute(delete(Event).where(Event.item_id.in_(ids)))
        session.execute(delete(Item).where(Item.id.in_(ids)))
    session.execute(delete(Document).where(Document.user_id == user.id, Document.source_url.like(DEMO_PREFIX + "%")))
    session.commit()
    return len(ids)
