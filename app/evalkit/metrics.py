"""Score extraction against hand-labelled cases. No LLM, no network: pure functions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from rapidfuzz import fuzz

from ..core.text import normalize

NAME_MATCH = 80  # rapidfuzz ratio (0-100) at which a predicted name counts as the labelled one


@dataclass
class CaseScore:
    case_id: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    kind_ok: int = 0  # matched items whose kind (place/offer/info) is also right
    predicted: int = 0
    grounded: int = 0
    unverified: int = 0  # items with no evidence quote
    ungrounded_dropped: int = 0
    missed: List[str] = field(default_factory=list)
    extra: List[str] = field(default_factory=list)


def _names(item) -> List[str]:
    names = [getattr(item, k, None) for k in ("venue_en", "venue", "title")] if not isinstance(item, dict) else [
        item.get(k) for k in ("venue_en", "venue", "title")] + list(item.get("names") or [])
    return [n for n in (normalize(x) for x in names if x) if n]


def _similarity(a: Sequence[str], b: Sequence[str]) -> float:
    return max((fuzz.ratio(x, y) for x in a for y in b), default=0.0)


def score_case(case_id: str, predicted: Sequence, expected: Sequence[dict], dropped: int = 0) -> CaseScore:
    """One-to-one matching of predicted items to labelled items by name, best pairs first."""
    sc = CaseScore(case_id=case_id, predicted=len(predicted), ungrounded_dropped=dropped)
    pairs = sorted(
        ((_similarity(_names(p), _names(e)), i, j) for i, p in enumerate(predicted) for j, e in enumerate(expected)),
        reverse=True,
    )
    used_p, used_e = set(), set()
    for score, i, j in pairs:
        if score < NAME_MATCH:
            break
        if i in used_p or j in used_e:
            continue
        used_p.add(i)
        used_e.add(j)
        sc.tp += 1
        if getattr(predicted[i], "kind", None) == expected[j].get("kind"):
            sc.kind_ok += 1
    sc.fp = len(predicted) - len(used_p)
    sc.fn = len(expected) - len(used_e)
    sc.extra = [(_names(predicted[i]) or ["?"])[0] for i in range(len(predicted)) if i not in used_p]
    sc.missed = [(_names(expected[j]) or ["?"])[0] for j in range(len(expected)) if j not in used_e]
    for p in predicted:
        if p.grounded is True:
            sc.grounded += 1
        elif p.grounded is None:
            sc.unverified += 1
    return sc


def _div(a: float, b: float) -> float:
    return round(a / b, 4) if b else 0.0


def summarize(scores: Sequence[CaseScore]) -> Dict[str, float]:
    tp, fp, fn = sum(s.tp for s in scores), sum(s.fp for s in scores), sum(s.fn for s in scores)
    precision, recall = _div(tp, tp + fp), _div(tp, tp + fn)
    pred = sum(s.predicted for s in scores)
    dropped = sum(s.ungrounded_dropped for s in scores)
    return {
        "cases": len(scores), "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall,
        "f1": _div(2 * precision * recall, precision + recall),
        "kind_accuracy": _div(sum(s.kind_ok for s in scores), tp),
        "grounded_rate": _div(sum(s.grounded for s in scores), pred),
        "unverified_rate": _div(sum(s.unverified for s in scores), pred),
        # share of everything the model proposed that the quote check had to throw away
        "ungrounded_rate": _div(dropped, pred + dropped),
    }
