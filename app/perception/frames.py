"""Keep only frames that look different from the last kept one.

Each frame becomes a 33x32 grayscale thumbnail. Its fingerprint is a bit per neighbouring-pixel
step that is clearly brighter (so flat areas and compression noise give 0 bits, while new text or
a new scene flips many bits), plus the average brightness, which catches changes between
plain-coloured slides.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

HASH_W, HASH_H = 33, 32  # 32 comparisons per row x 32 rows = 1024 bits
MARGIN = 4  # brightness steps smaller than this are noise, not edges
DEFAULT_DISTANCE = 20  # about 2% of bits: below this two frames count as the same scene
DEFAULT_BRIGHTNESS = 20  # average-brightness difference (0-255) that always counts as a new scene


def fingerprint(path: str) -> Tuple[int, float]:
    from PIL import Image

    with Image.open(path) as im:
        small = im.convert("L").resize((HASH_W, HASH_H), Image.LANCZOS)
        px = list(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())
    bits = 0
    for row in range(HASH_H):
        base = row * HASH_W
        for col in range(HASH_W - 1):
            bits = (bits << 1) | (px[base + col] - px[base + col + 1] > MARGIN)
    return bits, sum(px) / len(px)


def dhash(path: str) -> int:
    return fingerprint(path)[0]


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def distinct_frames(
    frames: Sequence[Tuple[Optional[float], str]],
    max_distance: int = DEFAULT_DISTANCE,
    max_brightness: float = DEFAULT_BRIGHTNESS,
) -> List[Tuple[Optional[float], str]]:
    """Drop frames that match the previously kept frame."""
    kept: List[Tuple[Optional[float], str]] = []
    last: Optional[Tuple[int, float]] = None
    for t, path in frames:
        fp = fingerprint(path)
        same = last is not None and hamming(fp[0], last[0]) <= max_distance and abs(fp[1] - last[1]) <= max_brightness
        if not same:
            kept.append((t, path))
            last = fp
    return kept
