import os
import shutil
import subprocess
from types import SimpleNamespace as NS

import pytest

from app.perception import asr

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture()
def wav(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    f = tmp_path / "clip.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "2", "-i", "sine=frequency=300", str(f)], check=True)
    return str(f)


class FakeGroq:
    def __init__(self, resp=None, error=None):
        self.calls = []
        self.audio = NS(transcriptions=NS(create=self.create))
        self.resp, self.error = resp, error

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.resp


RESP = NS(language="kannada", segments=[
    {"start": 0.0, "end": 2.0, "text": " ನಮಸ್ಕಾರ ", "avg_logprob": -0.2, "no_speech_prob": 0.01},
    {"start": 2.0, "end": 4.0, "text": "Thanks for watching", "avg_logprob": -1.8, "no_speech_prob": 0.95},
])


def test_engine_choice(monkeypatch):
    seen = []
    monkeypatch.setattr(asr, "transcribe_groq", lambda *a, **k: seen.append("groq") or ([], "en"))
    monkeypatch.setattr(asr, "transcribe_local", lambda *a, **k: seen.append("local") or ([], "en"))
    for env, key, expect in [("auto", "k", "groq"), ("auto", "", "local"), ("local", "k", "local"), ("groq", "", "groq")]:
        seen.clear()
        monkeypatch.setenv("ASR_ENGINE", env)
        monkeypatch.setenv("GROQ_API_KEY", key)
        asr.transcribe("x.wav")
        assert seen == [expect], (env, key)


def test_groq_failure_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("ASR_ENGINE", "auto")
    monkeypatch.setenv("GROQ_API_KEY", "k")

    def boom(*a, **k):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(asr, "transcribe_groq", boom)
    monkeypatch.setattr(asr, "transcribe_local", lambda *a, **k: (["local result"], "hi"))
    assert asr.transcribe("x.wav") == (["local result"], "hi")


@needs_ffmpeg
def test_groq_call_shape_hint_and_segment_mapping(wav, monkeypatch):
    fake = FakeGroq(RESP)
    monkeypatch.setattr(asr, "_groq_client", lambda: fake)
    monkeypatch.setenv("GROQ_ASR_MODEL", "whisper-large-v3")
    monkeypatch.delenv("ASR_LANGUAGE", raising=False)
    segs, lang = asr.transcribe_groq(wav, hint="Atithya Vidyarthi Bhavan")
    call = fake.calls[0]
    assert call["model"] == "whisper-large-v3" and call["response_format"] == "verbose_json"
    assert call["prompt"] == "Atithya Vidyarthi Bhavan" and "language" not in call  # language is auto-detected
    assert call["file"][0] == "audio.flac" and call["file"][1][:4] == b"fLaC"  # real FLAC bytes were uploaded
    assert lang == "kannada" and [s.text for s in segs] == ["ನಮಸ್ಕಾರ"]  # music hallucination dropped
    assert segs[0].start == 0.0 and segs[0].end == 2.0 and segs[0].source == "speech"


@needs_ffmpeg
def test_groq_language_override_and_oversized_audio(wav, monkeypatch):
    fake = FakeGroq(RESP)
    monkeypatch.setattr(asr, "_groq_client", lambda: fake)
    monkeypatch.setenv("ASR_LANGUAGE", "kn")
    asr.transcribe_groq(wav)
    assert fake.calls[0]["language"] == "kn" and "prompt" not in fake.calls[0]
    monkeypatch.setattr(asr, "GROQ_FREE_FILE_LIMIT", 10)
    with pytest.raises(RuntimeError, match="too large"):
        asr.transcribe_groq(wav)


def test_local_model_redetects_language_per_window(monkeypatch):
    seen = {}

    class FakeModel:
        def transcribe(self, path, **kw):
            seen.update(kw)
            return iter([]), NS(language="hi", language_probability=0.9)

    monkeypatch.setattr(asr, "_model", FakeModel())
    monkeypatch.setattr(asr, "_model_name", "small")
    monkeypatch.delenv("ASR_LANGUAGE", raising=False)
    asr.transcribe_local("x.wav")
    assert seen["multilingual"] is True
    asr.transcribe_local("x.wav", language="kn")
    assert seen["multilingual"] is False and seen["language"] == "kn"
