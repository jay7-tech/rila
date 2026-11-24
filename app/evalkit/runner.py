"""Run extraction over labelled cases, with an on-disk LLM cache so repeat runs are free and identical."""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..config import get_settings
from ..services import extract as extract_svc
from . import metrics

CASES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "eval", "cases")
CACHE_DIR = os.path.join(os.path.dirname(CASES_DIR), ".llm_cache")


def load_cases(path: str = CASES_DIR, include_synthetic: bool = False, include_unlabeled: bool = False) -> List[dict]:
    out = []
    for name in sorted(os.listdir(path)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(path, name), encoding="utf-8") as f:
            case = json.load(f)
        case.setdefault("id", name[:-5])
        if case.get("synthetic") and not include_synthetic:
            continue
        if not case.get("labeled") and not include_unlabeled and not case.get("synthetic"):
            continue
        out.append(case)
    return out


def cached(llm: Callable[[str, str], str], cache_dir: str = CACHE_DIR) -> Callable[[str, str], str]:
    """Wrap an LLM call so identical requests are answered from disk. The key includes the model chain
    and prompt version, so changing either triggers fresh calls."""
    os.makedirs(cache_dir, exist_ok=True)
    s = get_settings()
    salt = json.dumps([s.groq_model, list(s.groq_fallback_models), extract_svc.PROMPT_VERSION])

    def call(system: str, user: str) -> str:
        key = hashlib.sha256("\n\x00".join([salt, system, user]).encode()).hexdigest()
        path = os.path.join(cache_dir, key + ".json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)["reply"]
        reply = llm(system, user)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"reply": reply}, f, ensure_ascii=False)
        return reply

    return call


def run(cases: List[dict], llm: Optional[Callable[[str, str], str]] = None, min_confidence: Optional[float] = None) -> dict:
    scores = []
    for case in cases:
        now = datetime.fromisoformat(case["now"]) if case.get("now") else datetime.utcnow()
        posted = datetime.fromisoformat(case["posted_at"]) if case.get("posted_at") else None
        res = extract_svc.extract(case.get("caption", ""), case.get("transcript", ""), now, posted, llm=llm,
                                  min_confidence=min_confidence)
        scores.append(metrics.score_case(case["id"], res.items, case.get("expected", {}).get("items", []),
                                         res.stats.get("ungrounded_dropped", 0)))
    s = get_settings()
    return {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "prompt_version": extract_svc.PROMPT_VERSION,
        "models": [s.groq_model, *s.groq_fallback_models],
        "summary": metrics.summarize(scores),
        "cases": [vars(sc) for sc in scores],
    }


def report(result: Dict) -> str:
    m = result["summary"]
    lines = [
        f"Prompt v{result['prompt_version']}  models: {', '.join(result['models'])}",
        f"Cases: {m['cases']}   precision {m['precision']:.2f}   recall {m['recall']:.2f}   F1 {m['f1']:.2f}",
        f"Kind accuracy {m['kind_accuracy']:.2f}   grounded {m['grounded_rate']:.2f}   "
        f"unverified {m['unverified_rate']:.2f}   ungrounded (dropped) {m['ungrounded_rate']:.2f}",
        f"Calibration: Brier {m['brier']}   ECE {m['ece']}   (lower is better; None = no predictions)",
        "",
    ]
    for c in result["cases"]:
        flag = "ok " if c["fp"] == 0 and c["fn"] == 0 else "!! "
        extra = f"  extra: {c['extra']}" if c["extra"] else ""
        missed = f"  missed: {c['missed']}" if c["missed"] else ""
        lines.append(f"{flag}{c['case_id']}: tp {c['tp']} fp {c['fp']} fn {c['fn']}{extra}{missed}")
    return "\n".join(lines)
