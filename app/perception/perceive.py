"""Turn one media file (video, audio or image) into timeline segments."""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Callable, List, Optional, Tuple

from ..config import get_settings
from ..core.document import Segment
from . import frames as frames_mod
from . import media, ocr

log = logging.getLogger(__name__)

AsrFn = Callable[[str], Tuple[List[Segment], Optional[str]]]


def perceive_file(
    path: str,
    asr_fn: Optional[AsrFn] = None,
    ocr_fn=ocr.ocr_image,
    max_minutes: Optional[int] = None,
    hint: Optional[str] = None,
) -> Tuple[List[Segment], dict]:
    """Return (segments, meta). Speech and screen text are independent: one failing never blocks the other."""
    s = get_settings()
    limit = (max_minutes or s.max_video_minutes) * 60
    info = media.probe(path)
    if info.duration and info.duration > limit and not info.is_image:
        raise media.MediaError(f"That video is longer than {limit // 60} minutes.")
    meta: dict = {"duration": info.duration, "has_audio": info.has_audio, "has_video": info.has_video}
    segments: List[Segment] = []

    if info.has_audio and not info.is_image:
        try:
            if asr_fn is None:
                from .asr import transcribe as asr_fn  # heavy import only when needed
            speech, lang = asr_fn(path, hint=hint) if hint else asr_fn(path)
            segments += speech
            meta.update(language=lang, asr_model=s.whisper_model)
        except Exception:
            log.exception("speech recognition failed; continuing with on-screen text")
            meta["asr_error"] = True

    if info.has_video:
        langs = ocr.usable_langs(s.ocr_langs) if ocr_fn is ocr.ocr_image else s.ocr_langs
        if langs:
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    raw = media.sample_frames(path, os.path.join(tmp, "frames"), None if info.is_image else info.duration, s.max_frames)
                    kept = raw if info.is_image else frames_mod.distinct_frames(raw)
                    segments += ocr.screen_segments(kept, langs, s.ocr_min_conf, info.duration, ocr_fn=ocr_fn)
                    meta.update(frames_sampled=len(raw), frames_kept=len(kept), ocr_langs=langs)
            except media.MediaError:
                log.warning("could not sample frames")
                meta["frames_error"] = True
        else:
            meta["ocr_unavailable"] = True
    return segments, meta
