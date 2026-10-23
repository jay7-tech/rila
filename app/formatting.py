"""Plain-text messages for the Telegram bot."""
from __future__ import annotations

from typing import List

CAT = {
    "food": "Food", "store": "Store", "event": "Event", "education": "Learning",
    "scheme": "Scheme", "service": "Service", "attraction": "Place", "other": "Other",
}


def maps_link(item: dict) -> str:
    if item.get("lat") is None:
        return ""
    return f"https://www.google.com/maps?q={item['lat']},{item['lon']}"


def distance_text(m) -> str:
    if m is None:
        return ""
    return f"{m} m away" if m < 1000 else f"{m / 1000:.1f} km away"


def one_item(item: dict, with_id: bool = False) -> str:
    head = f"{CAT.get(item['category'], 'Other')}: {item['title']}"
    if with_id:
        head = f"#{item['id']} {head}"
    lines = [head]
    where = ", ".join(x for x in (item.get("venue"), item.get("area"), item.get("city")) if x)
    if where:
        lines.append(f"  {where}" + ("  (approximate pin)" if item.get("geo") == "approx" else ""))
    bits = [b for b in (item.get("price"), item.get("when")) if b]
    if bits:
        lines.append("  " + " | ".join(bits))
    if item["status"] in ("active", "ending_soon", "upcoming") or item["time_left"] not in ("no end date",):
        lines.append(f"  {item['time_left']}")
    extra = [distance_text(item.get("distance_m"))] + list(item.get("reasons") or [])
    extra = [x for x in extra if x]
    if extra:
        lines.append("  " + " · ".join(extra))
    link = maps_link(item)
    if link:
        lines.append("  " + link)
    return "\n".join(lines)


def saved_summary(summary: dict) -> str:
    saved, dups = summary.get("saved", []), summary.get("duplicates", [])
    if summary.get("error"):
        return summary["error"]
    parts = []
    if saved:
        parts.append(f"Saved {len(saved)} from this reel:")
        parts += [f"- {s['title']} ({CAT.get(s['category'], 'Other')})" for s in saved]
    if dups:
        parts.append("Already on your map:")
        parts += [f"- {d['title']}" for d in dups]
    if summary.get("signals"):
        parts.append(f"Added {summary['signals']} live report(s) about conditions nearby.")
    if summary.get("rejected"):
        parts.append("Skipped (could not find on the map): " + ", ".join(summary["rejected"]))
    if not parts:
        parts.append("I couldn't find anything specific to save in that reel.")
    parts.append("Use /near to see what is close to you.")
    return "\n".join(parts)


def recs_message(items: List[dict], radius_m: int) -> str:
    if not items:
        return f"Nothing saved within {radius_m // 1000 or 1} km. Forward me more reels, or try /saved."
    return "Near you:\n\n" + "\n\n".join(one_item(i) for i in items)


def saved_message(items: List[dict], limit: int = 10) -> str:
    if not items:
        return "Nothing saved yet. Forward me an Instagram reel or YouTube short."
    shown = items[:limit]
    more = len(items) - len(shown)
    text = "Your saved items:\n\n" + "\n\n".join(one_item(i, with_id=True) for i in shown)
    return text + (f"\n\n...and {more} more. Open the web app with /web to see all." if more > 0 else "")


HELP = (
    "Rilla turns reels into a map of offers and places around you.\n\n"
    "Forward or paste an Instagram reel or YouTube short link and I will save it.\n"
    "Share your location to see what is near you.\n\n"
    "/near  what is close to your last location\n"
    "/saved [category]  your saved items\n"
    "/search <words>  search what you saved\n"
    "/briefing <place>  what locals report there right now\n"
    "/forget <id>  remove a saved item\n"
    "/web  open the map in your browser"
)
