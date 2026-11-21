"""Split a transcript into overlapping windows that keep their timestamps.

Used to read long videos in pieces. Retrieval (stage 5) will index the same chunks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .document import Line
from .text import approx_tokens


@dataclass
class Chunk:
    text: str
    start: Optional[float]
    end: Optional[float]
    tokens: int
    first_line: int
    last_line: int  # inclusive


def chunk_lines(lines: Sequence[Line], max_tokens: int = 300, overlap_tokens: int = 40) -> List[Chunk]:
    """Greedy packing of whole lines. A line longer than max_tokens becomes its own chunk."""
    if max_tokens <= 0 or overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("need 0 <= overlap_tokens < max_tokens")
    sizes = [approx_tokens(l.text) for l in lines]
    chunks: List[Chunk] = []
    i = 0
    while i < len(lines):
        j, total = i, 0
        while j < len(lines) and (j == i or total + sizes[j] <= max_tokens):
            total += sizes[j]
            j += 1
        chunks.append(_make(lines, sizes, i, j - 1))
        if j >= len(lines):
            break
        # step back so the next chunk repeats about overlap_tokens of context, but always move forward
        back, k = 0, j
        while k - 1 > i and back + sizes[k - 1] <= overlap_tokens:
            k -= 1
            back += sizes[k]
        i = max(k, i + 1)
    return chunks


def _make(lines: Sequence[Line], sizes: Sequence[int], a: int, b: int) -> Chunk:
    part = lines[a:b + 1]
    starts = [l.start for l in part if l.start is not None]
    return Chunk(
        text="\n".join(l.text for l in part),
        start=min(starts) if starts else None,
        end=max(starts) if starts else None,
        tokens=sum(sizes[a:b + 1]),
        first_line=a,
        last_line=b,
    )
