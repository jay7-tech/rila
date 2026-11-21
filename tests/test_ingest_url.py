"""The link downloader, with yt-dlp replaced by a fake so nothing touches the network."""
import os
import sys
import types

import pytest

from app.core.document import Segment
from app.services import ingest as ing

INFO = {"id": "abc123", "description": "Biryani fest!", "uploader": "@foodie", "timestamp": 1_760_000_000,
        "duration": 30, "language": "kn", "automatic_captions": {"kn": [{}]}}


class DownloadError(Exception):
    pass


def install_fake_ytdlp(monkeypatch, info=INFO, video_ok=True, audio_ok=True, subs="WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nnamaskara\n",
                       meta_error=None):
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            if not download:
                if meta_error:
                    raise DownloadError(meta_error)
                return info
            if self.opts.get("writesubtitles"):
                calls.append("subs")
                if subs:
                    with open(os.path.join(os.path.dirname(self.opts["outtmpl"]), f"{info['id']}.kn.vtt"), "w", encoding="utf-8") as f:
                        f.write(subs)
                return info
            fmt = self.opts["format"]
            calls.append(fmt)
            ok = video_ok if fmt == ing.VIDEO_FORMAT else audio_ok
            if not ok:
                raise DownloadError("Requested format is not available")
            with open(os.path.join(os.path.dirname(self.opts["outtmpl"]), f"{info['id']}.mp4"), "wb") as f:
                f.write(b"media")
            return info

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = FakeYDL
    utils = types.ModuleType("yt_dlp.utils")
    utils.DownloadError = DownloadError
    mod.utils = utils
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils)
    return calls


def fake_perceive(segments, meta=None):
    def run(path, **kw):
        run.kw = kw
        return segments, meta or {"language": "kn", "duration": 30}

    return run


def test_video_is_downloaded_and_perceived_into_one_document(monkeypatch):
    calls = install_fake_ytdlp(monkeypatch)
    speech = Segment(source="speech", text="ಹೊಸ ಬಿರಿಯಾನಿ", start=1, end=3, lang="kn")
    screen = Segment(source="screen", text="Meghana Foods", start=2, end=6)
    fake = fake_perceive([speech, screen])
    monkeypatch.setattr(ing, "perceive_file", fake)
    out = ing.ingest("https://www.instagram.com/reel/abc/")
    assert fake.kw["hint"] == "Biryani fest"  # the caption's own spelling guides speech recognition
    assert calls == [ing.VIDEO_FORMAT]  # no subtitles needed, speech was recognised
    doc = out.document
    assert doc.kind == "video" and doc.creator == "@foodie" and doc.language == "kn" and doc.url.endswith("/abc/")
    assert [s.source for s in doc.segments] == ["caption", "speech", "screen"]
    assert out.caption == "Biryani fest!" and "[0:02-0:06 screen] Meghana Foods" in out.transcript
    assert out.posted_at is not None


def test_falls_back_to_audio_then_to_platform_captions(monkeypatch):
    calls = install_fake_ytdlp(monkeypatch, video_ok=False)
    monkeypatch.setattr(ing, "perceive_file", fake_perceive([]))  # nothing recognised
    out = ing.ingest("https://youtu.be/abc")
    assert calls == [ing.VIDEO_FORMAT, "bestaudio/best", "subs"]
    speech = [s for s in out.document.segments if s.source == "speech"]
    assert speech and "namaskara" in speech[0].text and out.document.meta["speech_from"] == "platform captions"


def test_caption_only_still_works_when_no_media_can_be_downloaded(monkeypatch):
    install_fake_ytdlp(monkeypatch, video_ok=False, audio_ok=False, subs="")
    out = ing.ingest("https://youtu.be/abc")
    assert [s.source for s in out.document.segments] == ["caption"]


def test_nothing_at_all_is_a_friendly_error(monkeypatch):
    install_fake_ytdlp(monkeypatch, info={**INFO, "description": "", "title": ""}, video_ok=False, audio_ok=False, subs="")
    with pytest.raises(ing.IngestError):
        ing.ingest("https://youtu.be/abc")


def test_too_long_and_unsupported_links(monkeypatch):
    install_fake_ytdlp(monkeypatch, info={**INFO, "duration": 3 * 3600})
    with pytest.raises(ing.IngestError, match="longer than"):
        ing.ingest("https://youtu.be/abc")
    install_fake_ytdlp(monkeypatch, meta_error="Unsupported URL: https://x.com/q")
    with pytest.raises(ing.IngestError, match="not a video"):
        ing.ingest("https://x.com/q")
    install_fake_ytdlp(monkeypatch, meta_error="Private video. Sign in")
    with pytest.raises(ing.IngestError, match="private"):
        ing.ingest("https://youtu.be/abc")


def test_media_error_from_perception_becomes_a_user_message(monkeypatch):
    from app.perception.media import MediaError

    install_fake_ytdlp(monkeypatch)

    def too_long(path, **kw):
        raise MediaError("That video is longer than 20 minutes.")

    monkeypatch.setattr(ing, "perceive_file", too_long)
    with pytest.raises(ing.IngestError, match="longer"):
        ing.ingest("https://youtu.be/abc")
