"""A per-user taste model: online logistic regression over hashed item features.

Each saved item becomes a sparse bag of features (category, creator, area, kind, price band, content words, source).
The label comes from what the user did with it: like / visit -> 1, dismiss -> 0, open -> a weak 1.
Training is plain stochastic gradient descent on the log-loss with L2 regularisation, every example weighted by
recency (half-life 30 days) and by how strong the action is:

    p(like | x) = sigmoid(b + sum_f w_f)         w <- w - lr * (weight * (p - y) * 1 + l2 * w)

Features are hashed into 2**16 buckets with crc32 (deterministic across processes, unlike Python's hash()), so the
model needs no vocabulary and handles words it has never seen. It is retrained from the user's event log on demand
(a few hundred examples train in milliseconds), so there is no stored model that can drift or leak.

`explain` reports which features pushed an item up or down, so the "For you" feed can say why.
With fewer than MIN_EVENTS labelled examples it reports `cold` and the feed falls back to ranking + exploration.
"""
from __future__ import annotations

import math
import re
import zlib
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DIM = 1 << 16
LR = 0.25
L2 = 0.01
EPOCHS = 12
MIN_EVENTS = 3
HALF_LIFE_DAYS = 30.0
ACTION_LABEL = {"like": (1.0, 1.0), "visit": (1.0, 1.5), "open": (1.0, 0.3), "dismiss": (0.0, 1.0)}  # label, weight
STOP = {"the", "a", "an", "in", "at", "of", "for", "and", "to", "with", "is", "on", "rs", "this", "that"}
WORD = re.compile(r"[a-z]{3,}")


def bucket(name: str) -> int:
    return zlib.crc32(name.encode("utf-8")) % DIM


def price_band(text: Optional[str]) -> Optional[str]:
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text or "")]
    if not nums:
        return None
    v = min(nums)
    return "free" if v == 0 else "under100" if v < 100 else "under300" if v < 300 else "under1000" if v < 1000 else "premium"


def features(item: dict) -> List[str]:
    """Names of the features an item switches on. `item` is a plain dict so this works for any source."""
    f = ["bias_cat:" + (item.get("category") or "other"), "kind:" + (item.get("kind") or "place")]
    if item.get("creator"):
        f.append("creator:" + item["creator"].lower())
    if item.get("area"):
        f.append("area:" + item["area"].lower())
    if item.get("city"):
        f.append("city:" + item["city"].lower())
    band = price_band(item.get("price"))
    if band:
        f.append("price:" + band)
    if item.get("evidence_source"):
        f.append("src:" + item["evidence_source"])
    text = " ".join(filter(None, [item.get("title"), item.get("summary")])).lower()
    seen = set()
    for w in WORD.findall(text):
        if w not in STOP and w not in seen:
            seen.add(w)
            f.append("w:" + w)
    return f


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(z, 30.0), -30.0)))


def _recency(created: datetime, now: datetime) -> float:
    return 0.5 ** (max((now - created).total_seconds() / 86400.0, 0.0) / HALF_LIFE_DAYS)


class Taste:
    def __init__(self) -> None:
        self.w: Dict[int, float] = {}
        self.b = 0.0
        self.n = 0
        self.names: Dict[int, str] = {}  # bucket -> a feature name, for explanations only

    @property
    def cold(self) -> bool:
        return self.n < MIN_EVENTS

    def _z(self, buckets: Sequence[int]) -> float:
        return self.b + sum(self.w.get(i, 0.0) for i in buckets)

    def fit(self, examples: Iterable[Tuple[Sequence[str], float, float]]) -> "Taste":
        """examples: (feature names, label 0/1, sample weight). Deterministic: fixed order, fixed epochs."""
        data = []
        for names, y, wt in examples:
            idx = [bucket(n) for n in names]
            for n, i in zip(names, idx):
                self.names.setdefault(i, n)
            data.append((idx, y, wt))
        self.n = len(data)
        for _ in range(EPOCHS):
            for idx, y, wt in data:
                g = wt * (_sigmoid(self._z(idx)) - y)
                self.b -= LR * g * 0.5
                for i in idx:
                    self.w[i] = self.w.get(i, 0.0) - LR * (g + L2 * self.w.get(i, 0.0))
        return self

    def predict(self, item: dict) -> float:
        return _sigmoid(self._z([bucket(n) for n in features(item)]))

    def explain(self, item: dict, top: int = 3) -> List[dict]:
        """Features that moved this item's score most, with direction. Hashed buckets are mapped back to names seen in training."""
        rows = []
        for n in features(item):
            w = self.w.get(bucket(n), 0.0)
            if abs(w) >= 0.05:
                rows.append({"feature": n, "weight": round(w, 3)})
        rows.sort(key=lambda r: -abs(r["weight"]))
        return rows[:top]

    def top_features(self, k: int = 8) -> dict:
        rows = [{"feature": self.names.get(i, "?"), "weight": round(w, 3)} for i, w in self.w.items() if i in self.names and abs(w) >= 0.05]
        rows.sort(key=lambda r: -r["weight"])
        return {"likes": [r for r in rows if r["weight"] > 0][:k], "avoids": [r for r in reversed(rows) if r["weight"] < 0][:k]}


def train(events: Iterable[Tuple[str, dict, datetime]], now: datetime) -> Taste:
    """events: (action, item dict, when). Items the user acted on become labelled examples."""
    ex = []
    for action, item, when in sorted(events, key=lambda e: e[2]):
        if action not in ACTION_LABEL or not item:
            continue
        y, wt = ACTION_LABEL[action]
        ex.append((features(item), y, wt * _recency(when, now)))
    return Taste().fit(ex)


def feature_text(name: str) -> str:
    """'creator:foo' -> 'creator foo' for display."""
    kind, _, val = name.partition(":")
    return {"bias_cat": "category", "w": "word", "src": "found via", "price": "price"}.get(kind, kind) + " " + val
