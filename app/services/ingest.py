"""Download a public video link and turn it into a VideoDocument (caption + speech + on-screen text).

Heavy libraries are imported lazily so the API and tests start fast.
"""
from __future__ import annotations

import glob
import html
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from ..config import get_settings
from ..core.document import Segment, VideoDocument
from ..core.text import hint_from_caption
from ..perception import media
from ..perception.perceive import perceive_file


class IngestError(Exception):
    """Raised with a message that is safe to show the user."""


@dataclass
class Ingested:
    caption: str
    transcript: str  # speech and on-screen text as tagged lines (see VideoDocument.render_body)
    creator: Optional[str]
    posted_at: Optional[datetime]
    document: Optional[VideoDocument] = None


def from_document(doc: VideoDocument) -> Ingested:
    return Ingested(
        caption=doc.caption, transcript=doc.render_body(), creator=doc.creator,
        posted_at=doc.posted_at, document=doc,
    )


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


def _find_media(tmp: str, video_id: str) -> Optional[str]:
    files = [f for f in glob.glob(os.path.join(tmp, f"{video_id}*"))
             if not f.endswith((".vtt", ".part", ".json", ".ytdl"))]
    return max(files, key=os.path.getsize) if files else None


# Up to 720p is enough to read overlay text; audio-only is the fallback when no video format is offered.
VIDEO_FORMAT = "bv*[height<=720]+ba/b[height<=720]/b"


def _download_media(url: str, tmp: str, video_id: str) -> Optional[str]:
    from yt_dlp import YoutubeDL

    for fmt in (VIDEO_FORMAT, "bestaudio/best"):
        try:
            opts = _base_opts(tmp)
            opts["format"] = fmt
            opts["max_filesize"] = get_settings().max_upload_mb * 1024 * 1024
            with YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
            path = _find_media(tmp, video_id)
            if path:
                return path
        except Exception:
            continue
    return None


def _subtitle_text(url: str, tmp: str, info: dict) -> str:
    """Creator or auto captions in one language. Optional: rate limits and missing captions are not errors."""
    from yt_dlp import YoutubeDL

    lang = _pick_sub_lang(info)
    if not lang:
        return ""
    try:
        opts = _base_opts(tmp)
        opts.update(skip_download=True, writesubtitles=True, writeautomaticsub=True,
                    subtitleslangs=[lang], subtitlesformat="vtt")
        with YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
        subs = glob.glob(os.path.join(tmp, f"{info['id']}*.vtt"))
        if subs:
            with open(subs[0], encoding="utf-8") as f:
                return parse_vtt(f.read())
    except Exception:
        pass
    return ""


def ingest(url: str) -> Ingested:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    s = get_settings()
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
        if (info.get("duration") or 0) > s.max_video_minutes * 60:
            raise IngestError(f"That video is longer than {s.max_video_minutes} minutes.")

        caption = info.get("description") or info.get("title") or ""
        creator = info.get("uploader") or info.get("channel") or info.get("uploader_id")
        posted_at = None
        if info.get("timestamp"):
            posted_at = datetime.fromtimestamp(info["timestamp"], timezone.utc).replace(tzinfo=None)
        elif info.get("upload_date"):
            try:
                posted_at = datetime.strptime(info["upload_date"], "%Y%m%d")
            except ValueError:
                pass

        # Step 2: download the video (or just its audio) and read speech and on-screen text.
        segments: List[Segment] = []
        meta: dict = {}
        path = _download_media(url, tmp, info.get("id", ""))
        if path:
            try:
                segments, meta = perceive_file(path, hint=hint_from_caption(caption))
            except media.MediaError as e:
                raise IngestError(str(e))

        # Step 3: no speech recognised, so fall back to the platform's captions.
        if not any(sg.source == "speech" for sg in segments):
            subs = _subtitle_text(url, tmp, info)
            if subs:
                segments.append(Segment(source="speech", text=subs, lang=(info.get("language") or None)))
                meta["speech_from"] = "platform captions"

        doc = VideoDocument(
            kind="video", url=url, creator=creator, posted_at=posted_at, language=meta.get("language"),
            duration=info.get("duration") or meta.get("duration"),
            segments=([Segment(source="caption", text=caption)] if caption else []) + segments, meta=meta,
        )
        if not doc.segments:
            raise IngestError("I couldn't get any text, speech or on-screen words from that video.")
        return from_document(doc)
