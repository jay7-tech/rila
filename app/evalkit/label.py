"""Fast labelling of eval cases: show what the model extracted, you confirm or fix with one short command.

A draft case already holds the model's items as `expected.items`. Labelling means checking them against the
video's words and correcting mistakes, then setting "labeled": true so the case counts in `eval`.

Commands at the prompt (items are numbered):
  Enter or y      everything is right, save as labelled
  d 2             delete item 2 (the model made it up, or it is not a real venue)
  k 2 offer       change item 2's kind to place, offer or info
  v 2 Name Here   change item 2's venue name
  a place | Name | City    add an item the model missed (city optional)
  n               there is nothing worth saving in this video
  t               show the whole transcript
  s               skip for now          q   save and quit
"""
from __future__ import annotations

import json
import os
from typing import Callable, Dict, List, Optional, Tuple

KINDS = ("place", "offer", "info")
HELP = __doc__.split("Commands at the prompt (items are numbered):")[1].strip()


def show_case(case: dict, full: bool = False, width: int = 700) -> str:
    items = case.get("expected", {}).get("items", [])
    drafts = {(d.get("venue") or d.get("title")): d for d in case.get("draft_from_model", [])}
    lines = [f"--- {case.get('id')}  ({case.get('language') or 'language unknown'})", f"source: {case.get('source', '')}"]
    cap = (case.get("caption") or "").strip()
    if cap:
        lines.append("caption: " + (cap if full else cap[:300]))
    tr = (case.get("transcript") or "").strip()
    lines.append("transcript: " + (tr if full or len(tr) <= width else tr[:width] + " ..."))
    lines.append("extracted:" if items else "extracted: nothing")
    for n, it in enumerate(items, 1):
        ev = (drafts.get(it.get("venue")) or {}).get("evidence")
        lines.append(f"  {n}. {it.get('kind', '?'):<6} {it.get('venue') or '?'}" + (f"  [{it['city']}]" if it.get("city") else "")
                     + (f'   proof: "{ev[:90]}"' if ev else ""))
    return "\n".join(lines)


def apply_command(case: dict, cmd: str) -> Tuple[str, str]:
    """Mutates case. Returns (result, message): result is 'done', 'skip', 'quit', 'show' or 'edit'."""
    items: List[dict] = case.setdefault("expected", {}).setdefault("items", [])
    c = cmd.strip()
    low = c.lower()
    if low in ("", "y", "yes"):
        return "done", "labelled as shown"
    if low == "n":
        items.clear()
        return "done", "labelled: nothing to save"
    if low in ("s", "skip"):
        return "skip", "skipped"
    if low in ("q", "quit"):
        return "quit", ""
    if low == "t":
        return "show", ""
    if low in ("?", "h", "help"):
        return "edit", HELP
    head, _, rest = c.partition(" ")
    h = head.lower()
    try:
        if h == "d":
            n = int(rest)
            gone = items.pop(n - 1)
            return "edit", f"deleted {gone.get('venue')}"
        if h == "k":
            num, _, kind = rest.partition(" ")
            kind = kind.strip().lower()
            if kind not in KINDS:
                return "edit", "kind must be place, offer or info"
            items[int(num) - 1]["kind"] = kind
            return "edit", "kind changed"
        if h == "v":
            num, _, name = rest.partition(" ")
            if not name.strip():
                return "edit", "give the new name after the number"
            items[int(num) - 1]["venue"] = name.strip()
            return "edit", "name changed"
        if h == "a":
            parts = [p.strip() for p in rest.split("|")]
            if len(parts) < 2 or parts[0].lower() not in KINDS or not parts[1]:
                return "edit", "use:  a place | Venue name | City"
            items.append({"kind": parts[0].lower(), "venue": parts[1], "names": [], "city": parts[2] if len(parts) > 2 and parts[2] else None})
            return "edit", "added"
    except (ValueError, IndexError):
        return "edit", "that item number does not exist"
    return "edit", "not understood. Type ? for the commands"


def label_case(case: dict, ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> str:
    """Interactive loop for one case. Returns 'done', 'skip' or 'quit'."""
    say(show_case(case))
    while True:
        res, msg = apply_command(case, ask("ok? (Enter = yes, ? = help) > "))
        if res == "show":
            say(show_case(case, full=True))
        elif res == "edit":
            say(msg)
            say(show_case(case))
        else:
            if msg:
                say(msg)
            if res == "done":
                case["labeled"] = True
            return res


def pending(cases_dir: str, relabel: bool = False) -> List[Tuple[str, dict]]:
    out = []
    for name in sorted(os.listdir(cases_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(cases_dir, name)
        with open(path, encoding="utf-8") as f:
            case = json.load(f)
        if case.get("synthetic") or (case.get("labeled") and not relabel):
            continue
        out.append((path, case))
    return out


def run(cases_dir: str, relabel: bool = False, ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> Dict[str, int]:
    todo = pending(cases_dir, relabel)
    stats = {"labelled": 0, "skipped": 0, "left": 0}
    say(f"{len(todo)} case(s) to check. Type ? for the commands.\n")
    for n, (path, case) in enumerate(todo):
        say(f"[{n + 1}/{len(todo)}]")
        res = label_case(case, ask, say)
        if res == "quit":
            stats["left"] = len(todo) - n
            break
        if res == "done":
            with open(path, "w", encoding="utf-8") as f:
                json.dump(case, f, ensure_ascii=False, indent=2)
            stats["labelled"] += 1
        else:
            stats["skipped"] += 1
        say("")
    return stats
