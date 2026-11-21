"""Speech to text with automatic language detection (faster-whisper, CPU)."""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from ..config import get_settings
from ..core.document import Segment

log = logging.getLogger(__name__)
_model = None
_model_name = None


def _load(name: str):
    global _model, _model_name
    from faster_whisper import WhisperModel

    if _model is None or _model_name != name:
        _model = WhisperModel(name, device="cpu", compute_type="int8")
        _model_name = name
    return _model


def is_hallucination(avg_logprob: float, no_speech_prob: float) -> bool:
    """Whisper invents text over music and silence; those segments score low on both."""
    return no_speech_prob > 0.8 and avg_logprob < -1.0


def transcribe(path: str, language: Optional[str] = None, hint: Optional[str] = None) -> Tuple[List[Segment], Optional[str]]:
    """Return timestamped speech segments and the detected language code (e.g. 'kn', 'hi', 'en').

    `hint` (names from the caption) is passed as hotwords, which faster-whisper adds to the prompt of
    every 30-second window.
    """
    s = get_settings()
    model = _load(s.whisper_model)
    lang = language or s.asr_language or None  # None = let the model detect it
    raw, info = model.transcribe(
        path, beam_size=5, language=lang, vad_filter=True, condition_on_previous_text=False,
        hotwords=hint or None,
    )
    detected = info.language
    segs: List[Segment] = []
    for r in raw:
        text = r.text.strip()
        if not text or is_hallucination(r.avg_logprob, r.no_speech_prob):
            continue
        segs.append(Segment(source="speech", text=text, start=float(r.start), end=float(r.end), lang=detected))
    return segs, detected
