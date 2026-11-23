"""How far to trust each creator, from evidence Rilla already holds.

For every creator the user has saved from: how many of their items had a quote found in the video (grounded),
how many items the quote check threw away as invented, and how the user reacted (likes, visits, dismissals).
learner.trust() turns that into a smoothed 0-1 score (0.5 = nothing known). Ranking uses it as a small bounded
adjustment, and the numbers are shown to the user. Per-user: one person's feedback never changes another's ranking.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document, Event, Item, User
from ..recs import learner


def creator_stats(session: Session, user: User, now: datetime, events: Optional[List[Event]] = None) -> List[dict]:
    items = list(session.scalars(select(Item).where(Item.user_id == user.id, Item.creator.is_not(None))))
    docs = list(session.scalars(select(Document).where(Document.user_id == user.id)))
    if events is None:
        events = list(session.scalars(select(Event).where(Event.user_id == user.id)))
    grounded: Dict[str, int] = {}
    reels: Dict[str, set] = {}
    for i in items:
        reels.setdefault(i.creator, set()).add(i.source_url)
        if i.grounded:
            grounded[i.creator] = grounded.get(i.creator, 0) + 1
    dropped: Dict[str, int] = {}
    doc_grounded: Dict[str, int] = {}
    for d in docs:
        meta = d.meta or {}
        c = meta.get("creator")
        if c:
            dropped[c] = dropped.get(c, 0) + int(meta.get("ungrounded_dropped", 0))
            if "grounded_kept" in meta:  # exact count recorded when the video was read; older documents fall back to counting items
                doc_grounded[c] = doc_grounded.get(c, 0) + int(meta["grounded_kept"])
    grounded.update(doc_grounded)
    by_creator: Dict[str, list] = {}
    for e in events:
        if e.creator:
            by_creator.setdefault(e.creator, []).append((e.action, None, e.created_at))
    rows = []
    for c in set(reels) | set(dropped):
        obs = by_creator.get(c, [])
        rows.append({
            "creator": c, "reels": len(reels.get(c, ())), "grounded": grounded.get(c, 0), "dropped": dropped.get(c, 0),
            "likes": sum(1 for a, _, _ in obs if a == "like"), "visits": sum(1 for a, _, _ in obs if a == "visit"),
            "dismissals": sum(1 for a, _, _ in obs if a == "dismiss"),
            "trust": learner.trust(obs, now, grounded.get(c, 0), dropped.get(c, 0)),
        })
    return sorted(rows, key=lambda r: (-r["trust"], -r["reels"], r["creator"]))


def trust_map(session: Session, user: User, now: datetime, events: Optional[List[Event]] = None) -> Dict[str, float]:
    return {r["creator"]: r["trust"] for r in creator_stats(session, user, now, events)}
