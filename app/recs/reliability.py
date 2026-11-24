"""How much to trust one saved item: a score in [0, 1] built from checkable evidence, plus calibration metrics.

Components (each in [0, 1]) and weights:
  grounded       0.30  the extractor's quote was found in the transcript / on-screen text
  evidence       0.20  where the proof came from: several sources > speech/screen > caption
  proximity      0.10  the quote and the venue name were seen close together in time
  location       0.20  confirmed pin > approximate > none
  creator        0.10  smoothed creator trust (0.5 = unknown)
  mentions       0.10  distinct reels that mention the same place (saturating)
The score is a transparent weighted sum so a user can be shown exactly why. `breakdown` returns the parts.

Calibration: when labelled cases exist, `brier` and `ece` tell whether "0.8" really means right ~80% of the time.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

WEIGHTS = {"grounded": 0.30, "evidence": 0.20, "proximity": 0.10, "location": 0.20, "creator": 0.10, "mentions": 0.10}
EVIDENCE = {"multi": 1.0, "speech": 0.8, "screen": 0.8, "text": 0.7, "caption": 0.55}
LOCATION = {"confirmed": 1.0, "approx": 0.55, "none": 0.0}
NEAR = 8.0


def components(grounded: Optional[bool], evidence_source: Optional[str], quote_gap_s: Optional[float], geo: str,
                creator_trust: float = 0.5, mentions: int = 1) -> Dict[str, float]:
    return {
        "grounded": 1.0 if grounded else (0.5 if grounded is None else 0.0),  # None = older item, never checked
        "evidence": EVIDENCE.get(evidence_source or "", 0.4),
        "proximity": 0.5 if quote_gap_s is None else max(0.0, 1.0 - quote_gap_s / (2 * NEAR)),
        "location": LOCATION.get(geo, 0.0),
        "creator": min(max(creator_trust, 0.0), 1.0),
        "mentions": 1.0 - 0.5 ** max(mentions, 1),  # 1 reel 0.5, 2 -> 0.75, 3 -> 0.875
    }


def score(parts: Dict[str, float]) -> float:
    return round(sum(WEIGHTS[k] * parts[k] for k in WEIGHTS), 3)


def label(s: float) -> str:
    return "high" if s >= 0.75 else "medium" if s >= 0.5 else "low"


def breakdown(parts: Dict[str, float]) -> List[dict]:
    return sorted(({"part": k, "value": round(parts[k], 2), "weight": WEIGHTS[k], "points": round(WEIGHTS[k] * parts[k], 3)} for k in WEIGHTS),
                  key=lambda r: -r["points"])


def brier(pairs: Sequence[Tuple[float, int]]) -> Optional[float]:
    """Mean squared error between predicted probability and the 0/1 outcome (0 is perfect; always saying 0.5 gives 0.25)."""
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None


def ece(pairs: Sequence[Tuple[float, int]], bins: int = 5) -> Optional[float]:
    """Expected calibration error: average gap between confidence and accuracy, over equal-width confidence bins."""
    if not pairs:
        return None
    total, err = len(pairs), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        grp = [(p, y) for p, y in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if grp:
            err += len(grp) / total * abs(sum(p for p, _ in grp) / len(grp) - sum(y for _, y in grp) / len(grp))
    return round(err, 4)


def extraction_score(grounded: Optional[bool], evidence_source: Optional[str]) -> float:
    """Reliability using only what exists at extraction time (no pin, no creator history): used to check calibration in eval."""
    g, e = WEIGHTS["grounded"], WEIGHTS["evidence"]
    c = components(grounded, evidence_source, None, "none")
    return round((g * c["grounded"] + e * c["evidence"]) / (g + e), 3)
