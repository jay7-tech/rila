"""One shape for everything Rila perceives: caption, speech, on-screen text, pasted text.

A VideoDocument is a timeline of Segments. Every later stage (extraction, grounding,
chunking, retrieval, evaluation) reads this and nothing else, so adding a new kind of
input only means producing a VideoDocument.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .text import clock

SegmentSource = Literal["caption", "speech", "screen", "text"]


class Segment(BaseModel):
    source: SegmentSource
    text: str
    start: Optional[float] = None  # seconds from the start of the video
    end: Optional[float] = None
    lang: Optional[str] = None
    conf: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class VideoDocument(BaseModel):
    kind: Literal["video", "audio", "image", "text"] = "video"
    url: Optional[str] = None
    creator: Optional[str] = None
    posted_at: Optional[datetime] = None
    language: Optional[str] = None  # spoken language reported by the speech model
    duration: Optional[float] = None
    segments: List[Segment] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)  # model names, frame counts: lets us re-run and compare

    def of(self, *sources: str) -> List[Segment]:
        return [s for s in self.segments if s.source in sources]

    @property
    def caption(self) -> str:
        return "\n".join(s.text for s in self.of("caption"))

    def render_body(self) -> str:
        """Speech, on-screen text and pasted text as tagged lines, in time order.

        The tag tells the extractor where each line came from, e.g. '[0:12 speech]'.
        """
        body = [s for s in self.segments if s.source != "caption"]
        body.sort(key=lambda s: (s.start is None, s.start or 0.0))
        lines = []
        for s in body:
            if s.start is None:
                tag = s.source
            elif s.end is not None and clock(s.end) != clock(s.start):
                tag = f"{clock(s.start)}-{clock(s.end)} {s.source}"
            else:
                tag = f"{clock(s.start)} {s.source}"
            lines.append(f"[{tag}] {s.text.strip()}")
        return "\n".join(lines)


_TAG = re.compile(r"^\[(?:(\d+):(\d{2})(?:-\d+:\d{2})? )?(speech|screen|text)\]\s*(.*)$")


class Line(BaseModel):
    source: str
    start: Optional[float]
    text: str


def parse_rendered(rendered: str) -> List[Line]:
    """Inverse of render_body(): lets grounding work from the transcript string alone."""
    out: List[Line] = []
    for raw in (rendered or "").splitlines():
        m = _TAG.match(raw.strip())
        if m:
            start = int(m.group(1)) * 60 + int(m.group(2)) if m.group(1) else None
            out.append(Line(source=m.group(3), start=start, text=m.group(4)))
        elif raw.strip():  # untagged text (older callers, plain transcripts)
            out.append(Line(source="speech", start=None, text=raw.strip()))
    return out
