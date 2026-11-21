"""Read on-screen text from frames with Tesseract (free, offline, many scripts).

Tesseract needs one traineddata file per language; the Docker image installs English plus
Hindi, Kannada, Tamil, Telugu and Malayalam. Set OCR_LANGS to change the set. Languages that
are not installed are dropped with a warning instead of failing.
"""
from __future__ import annotations

import functools
import logging
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..core.document import Segment
from ..core.text import normalize

log = logging.getLogger(__name__)
OCR_TIMEOUT = 60


@dataclass
class OcrLine:
    text: str
    conf: float  # 0-100, mean of its words


def available_langs() -> set:
    if not shutil.which("tesseract"):
        return set()
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=15).stdout
    except (subprocess.SubprocessError, OSError):
        return set()
    return {l.strip() for l in out.splitlines()[1:] if l.strip()}


@functools.lru_cache(maxsize=8)
def usable_langs(wanted: str) -> str:
    have = available_langs()
    want = [l for l in wanted.split("+") if l]
    use = [l for l in want if l in have]
    for miss in set(want) - set(use):
        log.warning("tesseract language %r is not installed; skipping it", miss)
    return "+".join(use)


def plausible(text: str) -> bool:
    """Real words, not '1111' or '५490': at least 3 letters, and mostly letters or digits.

    Marks count as letters (Indic vowel signs); digits are allowed because offers read '50% OFF'.
    """
    chars = [c for c in text if not c.isspace()]
    cats = [unicodedata.category(c)[0] for c in chars]
    letters = sum(1 for k in cats if k in "LM")
    return letters >= 3 and (letters + sum(1 for k in cats if k == "N")) / max(len(chars), 1) >= 0.7


def is_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if "LATIN" in unicodedata.name(c, "")) / len(letters) >= 0.5


def parse_tsv(tsv: str, min_conf: float, min_conf_nonlatin: Optional[float] = None) -> List[OcrLine]:
    """Group tesseract TSV words into lines and drop low-confidence words."""
    rows = tsv.splitlines()
    lines: dict = {}
    order: list = []
    for row in rows[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # level 5 = word
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        word = cols[11].strip()
        if not word or conf < min_conf:
            continue
        key = tuple(cols[2:5])  # block, paragraph, line
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append((word, conf))
    out = []
    for key in order:
        words = lines[key]
        text = " ".join(w for w, _ in words)
        conf = sum(c for _, c in words) / len(words)
        bar = min_conf if min_conf_nonlatin is None or is_latin(text) else max(min_conf, min_conf_nonlatin)
        if plausible(text) and conf >= bar:
            out.append(OcrLine(text=text, conf=conf))
    return out


def ocr_image(path: str, langs: str, min_conf: float = 60.0, min_conf_nonlatin: Optional[float] = None) -> List[OcrLine]:
    if not langs:
        return []
    try:
        res = subprocess.run(
            ["tesseract", path, "stdout", "-l", langs, "--psm", "11", "tsv"],
            capture_output=True, text=True, timeout=OCR_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("tesseract failed on %s: %s", path, type(e).__name__)
        return []
    return parse_tsv(res.stdout, min_conf, min_conf_nonlatin) if res.returncode == 0 else []


def screen_segments(
    frames: Sequence[Tuple[Optional[float], str]],
    langs: str,
    min_conf: float,
    final_end: Optional[float],
    ocr_fn=ocr_image,
    min_conf_nonlatin: Optional[float] = None,
) -> List[Segment]:
    """OCR every frame and merge a line that stays on screen into one segment with start and end.

    A line ends when the first frame without it appears. Lines still visible in the last frame
    end at final_end (the video's duration).
    """
    open_lines: dict = {}  # normalized text -> Segment still on screen
    done: List[Segment] = []
    for t, path in frames:
        seen = set()
        lines = ocr_fn(path, langs, min_conf) if min_conf_nonlatin is None else ocr_fn(path, langs, min_conf, min_conf_nonlatin)
        for ln in lines:
            key = normalize(ln.text)
            if not key or key in seen:
                continue
            seen.add(key)
            seg = open_lines.get(key)
            if seg is not None:
                seg.end = t
            else:
                open_lines[key] = Segment(source="screen", text=ln.text, start=t, end=t, conf=round(ln.conf / 100, 3))
        for key in [k for k in open_lines if k not in seen]:  # gone from this frame: close it
            seg = open_lines.pop(key)
            seg.end = t
            done.append(seg)
    for seg in open_lines.values():
        seg.end = final_end if final_end is not None else seg.end
        done.append(seg)
    done.sort(key=lambda s: (s.start is None, s.start or 0.0))
    return done
