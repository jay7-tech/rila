"""ffprobe / ffmpeg wrappers. Arguments are always passed as a list, never through a shell."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

PROBE_TIMEOUT = 30
FFMPEG_TIMEOUT = 180


class MediaError(Exception):
    """Raised with a message that is safe to show the user."""


@dataclass
class Probe:
    duration: Optional[float]
    has_video: bool
    has_audio: bool
    is_image: bool


def probe(path: str) -> Probe:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "--", path],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=True,
        ).stdout
        info = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        raise MediaError("That file is not a video, audio clip or image I can read.")
    streams = info.get("streams") or []
    kinds = {s.get("codec_type") for s in streams}
    fmt = (info.get("format") or {}).get("format_name", "")
    try:
        duration = float((info.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    is_image = bool(video) and "audio" not in kinds and (
        "image2" in fmt or "_pipe" in fmt or "png" in fmt or "jpeg" in fmt or "webp" in fmt or duration is None
        or (duration is not None and duration < 0.1)
    )
    return Probe(duration=duration, has_video=bool(video), has_audio="audio" in kinds, is_image=is_image)


def sample_frames(path: str, out_dir: str, duration: Optional[float], max_frames: int) -> List[Tuple[Optional[float], str]]:
    """Return (approximate time in seconds, jpeg path) for evenly spaced frames, at most max_frames."""
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "f%04d.jpg")
    if duration and duration > 0:
        rate = min(1.0, max(0.05, max_frames / duration))
        vf = f"fps={rate:.4f},scale='min(1280,iw)':-2"
    else:  # image or unknown length: just one frame
        rate = None
        vf = "scale='min(1280,iw)':-2"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", path, "-vf", vf, "-q:v", "3"]
    cmd += ["-frames:v", str(max_frames) if rate else "1", pattern]
    try:
        subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        raise MediaError("I couldn't read the picture from that video.")
    files = sorted(f for f in os.listdir(out_dir) if f.startswith("f") and f.endswith(".jpg"))
    return [((i + 0.5) / rate if rate else None, os.path.join(out_dir, f)) for i, f in enumerate(files)]
