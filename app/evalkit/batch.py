"""Read many links in one go and write a draft evaluation case for each, plus one review sheet."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from ..services import extract as extract_svc

URL = re.compile(r"https?://\S+")


def clean_links(text: str) -> List[str]:
    """One link per line; tracking parameters (?si=...) removed; duplicates dropped."""
    seen, out = set(), []
    for line in text.splitlines():
        m = URL.search(line)
        if not m:
            continue
        parts = urlsplit(m.group(0))
        query = "&".join(f"{k}={v[0]}" for k, v in parse_qs(parts.query).items() if k in ("v", "list", "t"))
        url = f"{parts.scheme}://{parts.netloc}{parts.path}" + (f"?{query}" if query else "")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def case_id(url: str) -> str:
    tail = urlsplit(url).path.rstrip("/").split("/")[-1] or "video"
    vid = parse_qs(urlsplit(url).query).get("v", [tail])[0]
    return "yt_" + re.sub(r"[^A-Za-z0-9_-]", "", vid)[:20]


def draft_case(url: str, ing, res, now: datetime) -> dict:
    doc = ing.document
    return {
        "id": case_id(url), "labeled": False, "source": url, "language": doc.language if doc else None,
        "now": now.replace(microsecond=0).isoformat(),
        "posted_at": ing.posted_at.replace(microsecond=0).isoformat() if ing.posted_at else None,
        "caption": ing.caption, "transcript": ing.transcript,
        "expected": {"items": [
            {"kind": i.kind, "venue": i.venue or i.title, "names": [n for n in (i.venue_en, i.title) if n and n != (i.venue or i.title)],
             "city": i.city} for i in res.items]},
        "draft_from_model": [i.model_dump(exclude_none=True) for i in res.items],
        "ungrounded_dropped": res.stats.get("ungrounded_dropped", 0),
    }


def run_batch(
    urls: List[str], ingest_fn: Callable, llm: Callable[[str, str], str], out_dir: str,
    now: Optional[datetime] = None, pause: float = 3.0, force: bool = False, log: Callable[[str], None] = print,
) -> Dict[str, str]:
    """Returns {url: 'ok' | 'skipped' | error message}. One bad link never stops the rest."""
    os.makedirs(out_dir, exist_ok=True)
    status: Dict[str, str] = {}
    for n, url in enumerate(urls, 1):
        cid = case_id(url)
        path = os.path.join(out_dir, cid + ".json")
        if os.path.exists(path) and not force:
            status[url] = "skipped"
            log(f"[{n}/{len(urls)}] {cid}: already done, skipping")
            continue
        t0 = time.time()
        try:
            ing = ingest_fn(url)
            res = extract_svc.extract(ing.caption, ing.transcript, now or datetime.utcnow(), ing.posted_at, llm=llm)
            case = draft_case(url, ing, res, now or datetime.utcnow())
            with open(path, "w", encoding="utf-8") as f:
                json.dump(case, f, ensure_ascii=False, indent=2)
                f.write("\n")
            status[url] = "ok"
            log(f"[{n}/{len(urls)}] {cid}: {len(res.items)} items, language {case['language']}, {time.time() - t0:.0f}s")
        except Exception as e:  # noqa: BLE001 - report and keep going
            status[url] = f"{type(e).__name__}: {str(e)[:150]}"
            log(f"[{n}/{len(urls)}] {cid}: FAILED {status[url]}")
        if pause and n < len(urls):
            time.sleep(pause)
    return status


def review_sheet(out_dir: str) -> str:
    """One markdown file listing every unlabelled draft, so the labeller reads it instead of opening JSON."""
    rows = ["# Review sheet", "", "For each video: watch it, then in its JSON file fix `expected.items`"
            " (delete wrong ones, add missed ones) and set `\"labeled\": true`.", ""]
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(out_dir, name), encoding="utf-8") as f:
            c = json.load(f)
        if c.get("labeled") or c.get("synthetic"):
            continue
        cap = " ".join((c.get("caption") or "").split())[:160]
        rows += [f"## {c['id']}  ({c.get('language') or '?'})", c.get("source", ""), f"Caption: {cap}", ""]
        items = c.get("draft_from_model") or []
        if not items:
            rows.append("- model found nothing to save")
        for i in items:
            rows.append(f"- {i.get('kind')}: **{i.get('venue_en') or i.get('venue') or i.get('title')}** "
                        f"({i.get('area') or ''} {i.get('city') or ''}) quote: \"{(i.get('evidence') or '')[:90]}\"")
        if c.get("ungrounded_dropped"):
            rows.append(f"- {c['ungrounded_dropped']} proposed item(s) dropped because their quote was not in the video")
        rows.append("")
    return "\n".join(rows)
