"""Text helpers that are safe for every script, including Indic ones.

Python's regex \\w does not match combining marks, so stripping punctuation with it
destroys Kannada, Hindi, Tamil and other Indic text (vowel signs are combining marks).
These helpers keep letters, marks and digits and nothing else.
"""
from __future__ import annotations

import unicodedata


def normalize(text: str | None) -> str:
    """Lower-case, NFKC-normalise, drop punctuation/symbols, collapse whitespace."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFKC", text or "").casefold():
        cat = unicodedata.category(ch)
        if cat[0] in "LMN":
            out.append(ch)
        elif cat == "Cf":  # zero-width joiners and similar: invisible, keep words intact
            continue
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def approx_tokens(text: str) -> int:
    """Cheap token estimate that does not pretend to match any one tokenizer.

    Space-separated scripts: about one token per word. Dense scripts (Indic, CJK) split into
    more tokens, so never estimate fewer than one token per four characters.
    """
    t = (text or "").strip()
    if not t:
        return 0
    return max(len(t.split()), -(-len(t) // 4))


def clock(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def hint_from_caption(caption: str | None, limit: int = 300) -> str:
    """Caption cleaned for use as a speech-model hint: no hashtags, links, emoji or list numbers.

    Creators usually spell venue and dish names correctly in the caption, which the speech model
    then hears as ordinary words ('Atithya' becomes 'Arthitia').
    """
    words = []
    for w in (caption or "").split():
        if w.startswith(("#", "@", "http")):
            continue
        w = "".join(c for c in w if unicodedata.category(c)[0] in "LMN" or c in "-&'")
        if w and not w.isdigit():
            words.append(w)
    return " ".join(words)[:limit]
