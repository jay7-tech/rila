"""Inputs that are not a link: an uploaded video, audio clip or screenshot, or pasted text."""
from __future__ import annotations

from typing import Callable, Optional

from ..core.document import Segment, VideoDocument
from ..perception import media
from ..perception.perceive import AsrFn, perceive_file
from ..services.ingest import IngestError, Ingested, from_document

MAX_TEXT_CHARS = 20000


def ingest_file(path: str, name: str = "", asr_fn: Optional[AsrFn] = None, ocr_fn: Optional[Callable] = None) -> Ingested:
    try:
        info = media.probe(path)
        kwargs = {"asr_fn": asr_fn}
        if ocr_fn is not None:
            kwargs["ocr_fn"] = ocr_fn
        segments, meta = perceive_file(path, **kwargs)
    except media.MediaError as e:
        raise IngestError(str(e))
    kind = "image" if info.is_image else "video" if info.has_video else "audio"
    if not segments:
        raise IngestError("I couldn't find any speech or readable text in that file.")
    doc = VideoDocument(kind=kind, url=f"file:{name}" if name else None, language=meta.get("language"),
                        duration=info.duration, segments=segments, meta=meta)
    return from_document(doc)


def ingest_text(text: str) -> Ingested:
    text = (text or "").strip()
    if len(text) < 10:
        raise IngestError("Paste a little more text.")
    doc = VideoDocument(kind="text", segments=[Segment(source="text", text=text[:MAX_TEXT_CHARS])])
    return from_document(doc)
