"""Offline evaluation of the taste model: does it rank what the user went on to like near the top?

Replay the user's history in time order. For every like or visit after the first few actions:
  1. train the taste model on the actions BEFORE it (nothing from the future),
  2. take the items that existed then and had not been acted on yet as candidates,
  3. rank them and record where the item the user actually chose landed.
Reported (averaged over the replayed steps):
  hit@k   share of steps where the chosen item is in the top k
  mrr     mean of 1 / rank of the chosen item
  ndcg@k  1 / log2(rank + 1) if in the top k, else 0 (one relevant item per step, so ideal DCG is 1)
Compared with two baselines: a category-frequency ranker (what the old interest score amounts to) and chance
(for a random order the expected hit@k is k / candidates).
With only a handful of actions the numbers are noisy: `steps` tells you how many were replayed.
Pure functions over plain dicts, so it is tested without a database.
"""
from __future__ import annotations

import math
import random
from datetime import datetime
from typing import Callable, Dict, List, Sequence, Tuple

from ..recs import taste

POSITIVE = {"like", "visit"}
Event = Tuple[str, int, datetime]  # action, item id, when


def _rank_of(target: int, scores: Dict[int, float]) -> int:
    order = sorted(scores, key=lambda i: (-scores[i], i))
    return order.index(target) + 1


def _category_scorer(past: Sequence[Event], items: Dict[int, dict]) -> Callable[[dict], float]:
    counts: Dict[str, float] = {}
    for action, iid, _ in past:
        it = items.get(iid)
        if it:
            counts[it.get("category") or "other"] = counts.get(it.get("category") or "other", 0.0) + (1.0 if action in POSITIVE else -1.0)
    return lambda it: counts.get(it.get("category") or "other", 0.0)


def evaluate(items: Dict[int, dict], events: Sequence[Event], k: int = 5, min_history: int = 3) -> dict:
    """items: id -> taste feature dict plus 'created_at'. events: every user action. Returns metrics and the steps used."""
    ev = sorted(events, key=lambda e: e[2])
    steps: List[dict] = []
    for n, (action, target, when) in enumerate(ev):
        if action not in POSITIVE or n < min_history or target not in items:
            continue
        past = ev[:n]
        acted = {iid for _, iid, _ in past}
        existed = lambda it: it.get("created_at") is None or it["created_at"] <= when  # nothing from the future
        cands = {i: it for i, it in items.items() if i not in acted and existed(it)}
        cands[target] = items[target]
        if len(cands) < 2:
            continue
        model = taste.train([(a, items[i], w) for a, i, w in past if i in items], when)
        taste_scores = {i: model.predict(it) for i, it in cands.items()}
        cat = _category_scorer(past, items)
        cat_scores = {i: cat(it) for i, it in cands.items()}
        steps.append({"candidates": len(cands), "taste": _rank_of(target, taste_scores), "category": _rank_of(target, cat_scores)})
    if not steps:
        return {"steps": 0, "note": "Not enough history yet: like or visit a few more places."}

    def summarize(key: str) -> dict:
        ranks = [s[key] for s in steps]
        return {"hit": round(sum(r <= k for r in ranks) / len(ranks), 3), "mrr": round(sum(1 / r for r in ranks) / len(ranks), 3),
                "ndcg": round(sum(1 / math.log2(r + 1) for r in ranks if r <= k) / len(ranks), 3)}
    chance = round(sum(min(k / s["candidates"], 1.0) for s in steps) / len(steps), 3)
    return {"steps": len(steps), "k": k, "taste": summarize("taste"), "category_baseline": summarize("category"), "chance_hit": chance}


def report(r: dict) -> str:
    if not r.get("steps"):
        return r.get("note", "nothing to evaluate")
    t, c = r["taste"], r["category_baseline"]
    return "\n".join([
        f"Replayed {r['steps']} likes/visits, each ranked among the items that existed and were unseen at the time (k={r['k']}).",
        f"  taste model        hit@{r['k']} {t['hit']:.2f}   MRR {t['mrr']:.2f}   NDCG@{r['k']} {t['ndcg']:.2f}",
        f"  category counts    hit@{r['k']} {c['hit']:.2f}   MRR {c['mrr']:.2f}   NDCG@{r['k']} {c['ndcg']:.2f}",
        f"  chance             hit@{r['k']} {r['chance_hit']:.2f}",
        "Few steps means noisy numbers; treat this as a smoke alarm, not a score." if r["steps"] < 20 else "",
    ]).strip()
