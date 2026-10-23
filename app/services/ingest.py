"""Download a public reel / short and turn it into caption + transcript.

Heavy libraries are imported lazily so the API and tests start fast.
"""
from __future__ import annotations

import glob
import html
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..config import get_settings


class IngestError(Exception):
    """Raised with a message that is safe to show the user."""


@dataclass
class Ingested:
    caption: str
    transcript: str
    creator: Optional[str]
    posted_at: Optional[datetime]


_TS = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}")
_TAG = re.compile(r"<[^>]+>")


def parse_vtt(text: str) -> str:
    """Strip timestamps/tags from WebVTT and drop repeated lines."""
    out: list = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.upper() == "WEBVTT" or line.startswith(("Kind:", "Language:", "Style:")):
            continue
        if _TS.match(line):
            continue
        line = _TAG.sub("", html.unescape(line)).replace("align:start position:0%", "").strip()
        if line and (not out or out[-1] != line):
            out.append(line)
    return " ".join(out)


_whisper = None


def _transcribe(path: str) -> str:
    global _whisper
    from faster_whisper import WhisperModel

    if _whisper is None:
        _whisper = WhisperModel(get_settings().whisper_model, device="cpu", compute_type="int8")
    segments, _ = _whisper.transcribe(path, beam_size=5)
    return " ".join(s.text.strip() for s in segments).strip()


def _base_opts(tmp: str) -> dict:
    opts = {
        "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "noplaylist": True,
        "ignore_no_formats_error": True,
        # YouTube needs a JavaScript runtime to unlock formats (installed in the Docker image).
        "js_runtimes": {"node": {}},
    }
    cookies = get_settings().cookies_path
    if cookies and os.path.exists(cookies):
        opts["cookiefile"] = cookies
    return opts


def _pick_sub_lang(info: dict) -> Optional[str]:
    """One subtitle language to try: manual English, then the video's own language, then auto captions."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    lang = (info.get("language") or "").split("-")[0]
    for pool in (manual, auto):
        for cand in ("en", lang):
            if cand and cand in pool:
                return cand
    if manual:
        return next(iter(manual))
    for k in auto:
        if k.endswith("-orig"):
            return k
    return None


def ingest(url: str) -> Ingested:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    with tempfile.TemporaryDirectory() as tmp:
        # Step 1: metadata only. Works even when no video format is downloadable.
        try:
            with YoutubeDL(_base_opts(tmp)) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as e:
            if "unsupported" in str(e).lower():
                raise IngestError("That link is not a video I can read.")
            raise IngestError("I couldn't open that video. It may be private, deleted, or need a login.")
        if not info:
            raise IngestError("I couldn't open that video. It may be private, deleted, or need a login.")

        caption = info.get("description") or info.get("title") or ""
        creator = info.get("uploader") or info.get("channel") or info.get("uploader_id")
        posted_at = None
        if info.get("timestamp"):
            posted_at = datetime.utcfromtimestamp(info["timestamp"])
        elif info.get("upload_date"):
            try:
                posted_at = datetime.strptime(info["upload_date"], "%Y%m%d")
            except ValueError:
                pass

        # Step 2: captions in one language. Optional: rate limits or missing captions are not errors.
        transcript = ""
        lang = _pick_sub_lang(info)
        if lang:
            try:
                sub_opts = _base_opts(tmp)
                sub_opts.update(skip_download=True, writesubtitles=True, writeautomaticsub=True,
                                subtitleslangs=[lang], subtitlesformat="vtt")
                with YoutubeDL(sub_opts) as ydl:
                    ydl.extract_info(url, download=True)
                subs = glob.glob(os.path.join(tmp, f"{info['id']}*.vtt"))
                if subs:
                    with open(subs[0], encoding="utf-8") as f:
                        transcript = parse_vtt(f.read())
            except Exception:
                transcript = ""

        # Step 3: still nothing spoken, so download the audio and transcribe it. Also optional.
        if not transcript:
            try:
                audio_opts = _base_opts(tmp)
                audio_opts["format"] = "bestaudio/best"
                with YoutubeDL(audio_opts) as ydl:
                    got = ydl.extract_info(url, download=True)
                    path = ydl.prepare_filename(got) if got else ""
                if path and os.path.exists(path):
                    transcript = _transcribe(path)
            except Exception:
                transcript = ""

        if not caption and not transcript:
            raise IngestError("I couldn't get any text or speech from that video.")
        return Ingested(caption=caption, transcript=transcript, creator=creator, posted_at=posted_at)
