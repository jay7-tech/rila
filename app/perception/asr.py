"""Speech to text with automatic language detection.

Two engines:
  groq   Whisper large-v3 hosted by Groq (free tier, no card). Far better than the small local model on
         Hindi, Kannada and Telugu. The audio of the video is sent to Groq, as the extracted text already is.
  local  faster-whisper on this machine's CPU. Private and offline, but the small model garbles Indian languages.

ASR_ENGINE=auto uses Groq when GROQ_API_KEY is set and falls back to local if Groq fails (rate limit, outage).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import List, Optional, Tuple

from ..config import get_settings
from ..core.document import Segment

log = logging.getLogger(__name__)
_model = None
_model_name = None
GROQ_FREE_FILE_LIMIT = 24 * 1024 * 1024  # the free tier accepts files up to 25 MB


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


def transcribe_local(path: str, language: Optional[str] = None, hint: Optional[str] = None) -> Tuple[List[Segment], Optional[str]]:
    """faster-whisper on the CPU. `hint` (names from the caption) becomes hotwords, added to every 30 s window.

    multilingual=True re-detects the language in each window, which matters for Hindi/Kannada speech that
    switches to English mid-sentence (one language for the whole file produced gibberish).
    """
    s = get_settings()
    model = _load(s.whisper_model)
    lang = language or s.asr_language or None  # None = let the model detect it
    raw, info = model.transcribe(
        path, beam_size=5, language=lang, vad_filter=True, condition_on_previous_text=False,
        hotwords=hint or None, multilingual=lang is None,
    )
    detected = info.language
    segs: List[Segment] = []
    for r in raw:
        text = r.text.strip()
        if not text or is_hallucination(r.avg_logprob, r.no_speech_prob):
            continue
        segs.append(Segment(source="speech", text=text, start=float(r.start), end=float(r.end), lang=detected))
    return segs, detected


def _groq_client():
    import groq

    return groq.Groq(api_key=get_settings().groq_api_key, timeout=120.0)


def _prepare_audio(path: str, out_dir: str) -> str:
    """16 kHz mono FLAC: what Whisper uses internally, and small enough for the free upload limit."""
    out = os.path.join(out_dir, "audio.flac")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ar", "16000", "-ac", "1", "-c:a", "flac", out],
                   capture_output=True, timeout=180, check=True)
    return out


def _field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def transcribe_groq(path: str, language: Optional[str] = None, hint: Optional[str] = None) -> Tuple[List[Segment], Optional[str]]:
    s = get_settings()
    with tempfile.TemporaryDirectory() as tmp:
        audio = _prepare_audio(path, tmp)
        if os.path.getsize(audio) > GROQ_FREE_FILE_LIMIT:
            raise RuntimeError("audio too large for the Groq free tier")
        kwargs = {"model": s.groq_asr_model, "response_format": "verbose_json", "temperature": 0.0}
        lang = language or s.asr_language
        if lang:
            kwargs["language"] = lang
        if hint:
            kwargs["prompt"] = hint[:600]  # the API accepts about 224 tokens of spelling context
        with open(audio, "rb") as f:
            resp = _groq_client().audio.transcriptions.create(file=("audio.flac", f.read()), **kwargs)
    detected = _field(resp, "language")
    segs: List[Segment] = []
    for r in _field(resp, "segments", None) or []:
        text = (_field(r, "text") or "").strip()
        if not text or is_hallucination(_field(r, "avg_logprob", 0.0) or 0.0, _field(r, "no_speech_prob", 0.0) or 0.0):
            continue
        segs.append(Segment(source="speech", text=text, start=float(_field(r, "start", 0.0)), end=float(_field(r, "end", 0.0)),
                            lang=detected))
    return segs, detected


def transcribe(path: str, language: Optional[str] = None, hint: Optional[str] = None) -> Tuple[List[Segment], Optional[str]]:
    s = get_settings()
    engine = s.asr_engine
    if engine == "auto":
        engine = "groq" if s.groq_api_key else "local"
    if engine == "groq":
        try:
            return transcribe_groq(path, language, hint)
        except Exception as e:  # noqa: BLE001 - rate limit, outage, oversized file: fall back, never fail the job
            log.warning("Groq speech recognition failed (%s); using the local model", type(e).__name__)
    return transcribe_local(path, language, hint)
