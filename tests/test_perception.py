import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from app.perception import asr, frames, media, ocr, perceive
from app.perception.ocr import OcrLine

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")

TSV_HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def tsv_row(block, line, word, conf, text):
    return f"5\t1\t{block}\t1\t{line}\t{word}\t0\t0\t10\t10\t{conf}\t{text}"


def test_parse_tsv_groups_words_into_lines_and_drops_weak_ones():
    tsv = "\n".join([
        TSV_HEAD,
        "4\t1\t1\t1\t1\t0\t0\t0\t10\t10\t-1\t",  # a line-level row, ignored
        tsv_row(1, 1, 1, 96.5, "50%"), tsv_row(1, 1, 2, 91, "OFF"), tsv_row(1, 1, 3, 20, "~~junk"),
        tsv_row(2, 1, 1, 94, "ಇಂದಿರಾನಗರ"),
        tsv_row(3, 1, 1, 95, "a"),  # too short to keep
        tsv_row(4, 1, 1, 95, "   "),
    ])
    lines = ocr.parse_tsv(tsv, 60)
    assert [l.text for l in lines] == ["50% OFF", "ಇಂದಿರಾನಗರ"]
    assert lines[0].conf == pytest.approx((96.5 + 91) / 2)


def test_screen_segments_merge_lines_that_stay_on_screen():
    script = {
        "f1": [OcrLine("Biryani Fest", 95), OcrLine("50% OFF", 90)],
        "f2": [OcrLine("biryani fest!", 93)],  # same line (case/punctuation differ), OFF is gone
        "f3": [OcrLine("Sunday only", 88)],
    }
    segs = ocr.screen_segments([(1.0, "f1"), (3.0, "f2"), (5.0, "f3")], "eng", 60, 8.0, ocr_fn=lambda p, l, c: script[p])
    got = {s.text: (s.start, s.end) for s in segs}
    assert got == {"Biryani Fest": (1.0, 5.0), "50% OFF": (1.0, 3.0), "Sunday only": (5.0, 8.0)}
    assert all(s.source == "screen" for s in segs)


def test_usable_langs_drops_missing_languages(monkeypatch):
    ocr.usable_langs.cache_clear()
    monkeypatch.setattr(ocr, "available_langs", lambda: {"eng", "hin"})
    assert ocr.usable_langs("eng+hin+kan") == "eng+hin"
    ocr.usable_langs.cache_clear()


def slide(path, shade, bars=(), noise=0):
    """A plain slide with dark/light bars standing in for lines of text."""
    im = Image.new("L", (320, 568), shade)
    d = ImageDraw.Draw(im)
    for (x0, y0, x1, y1) in bars:
        d.rectangle((x0, y0, x1, y1), fill=255 - shade if shade < 128 else 0)
    if noise:
        im = im.point(lambda v: min(255, v + noise))
    im.save(path)
    return str(path)


TITLE = [(40, 200, 280, 240), (40, 260, 200, 300)]
OTHER = [(40, 120, 280, 160), (40, 380, 240, 420), (40, 440, 160, 480)]


def test_distinct_frames_separates_scenes_and_ignores_noise(tmp_path):
    a = slide(tmp_path / "a.png", 30, TITLE)
    a_noisy = slide(tmp_path / "a2.png", 30, TITLE, noise=3)  # same slide, slight brightness drift
    new_text = slide(tmp_path / "b.png", 30, OTHER)  # same background, different text
    new_colour = slide(tmp_path / "c.png", 220, [])  # plain slide of another colour (no edges at all)
    assert frames.hamming(frames.dhash(a), frames.dhash(a_noisy)) <= 2
    assert frames.hamming(frames.dhash(a), frames.dhash(new_text)) > frames.DEFAULT_DISTANCE
    kept = frames.distinct_frames([(0.5, a), (1.5, a_noisy), (2.5, new_text), (3.5, new_text), (4.5, new_colour)])
    assert [t for t, _ in kept] == [0.5, 2.5, 4.5]


def test_whisper_hallucination_filter():
    assert asr.is_hallucination(avg_logprob=-1.4, no_speech_prob=0.9)
    assert not asr.is_hallucination(avg_logprob=-0.3, no_speech_prob=0.9)
    assert not asr.is_hallucination(avg_logprob=-1.4, no_speech_prob=0.1)


def test_probe_rejects_non_media(tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"this is not a video at all")
    with pytest.raises(media.MediaError):
        media.probe(str(f))


@pytest.fixture()
def reel(tmp_path):
    """An 8 second video with a silent audio track and two different slides."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    slide(a, 30, TITLE)
    slide(b, 30, OTHER)
    out = tmp_path / "reel.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", "4", "-i", str(a), "-loop", "1", "-t", "4", "-i", str(b),
        "-f", "lavfi", "-t", "8", "-i", "anullsrc=r=16000:cl=mono",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-c:a", "aac", str(out)], check=True)
    return str(out)


@needs_ffmpeg
def test_probe_and_frame_sampling_on_a_real_video(reel, tmp_path):
    info = media.probe(reel)
    assert info.has_video and info.has_audio and not info.is_image and info.duration == pytest.approx(8, abs=0.3)
    sampled = media.sample_frames(reel, str(tmp_path / "fr"), info.duration, max_frames=8)
    assert 6 <= len(sampled) <= 8 and sampled[0][0] < sampled[-1][0]
    assert len(frames.distinct_frames(sampled)) == 2  # the two slides


@needs_ffmpeg
def test_perceive_file_combines_speech_and_screen_and_survives_asr_failure(reel, monkeypatch):
    from app.core.document import Segment

    def fake_ocr(path, langs, conf):
        return [OcrLine("Biryani Fest", 95)]

    speech = [Segment(source="speech", text="aaj biryani fest hai", start=0.5, end=3, lang="hi")]
    segs, meta = perceive.perceive_file(reel, asr_fn=lambda p: (speech, "hi"), ocr_fn=fake_ocr)
    assert {s.source for s in segs} == {"speech", "screen"}
    assert meta["language"] == "hi" and meta["frames_kept"] == 2

    def broken(path):
        raise RuntimeError("model missing")

    segs, meta = perceive.perceive_file(reel, asr_fn=broken, ocr_fn=fake_ocr)
    assert [s.source for s in segs] == ["screen"] and meta["asr_error"] is True


@needs_ffmpeg
def test_perceive_file_rejects_overlong_video(reel):
    with pytest.raises(media.MediaError):
        perceive.perceive_file(reel, asr_fn=lambda p: ([], None), ocr_fn=lambda *a: [], max_minutes=0.05)


def test_transcribe_uses_detected_language_and_drops_hallucinated_segments(monkeypatch):
    from types import SimpleNamespace as NS

    seen = {}

    class FakeModel:
        def transcribe(self, path, **kw):
            seen.update(kw)
            segs = [
                NS(start=0.0, end=2.0, text=" ನಮಸ್ಕಾರ ", avg_logprob=-0.2, no_speech_prob=0.01),
                NS(start=2.0, end=4.0, text=" Thanks for watching! ", avg_logprob=-1.6, no_speech_prob=0.95),  # music
                NS(start=4.0, end=5.0, text="   ", avg_logprob=-0.1, no_speech_prob=0.0),
            ]
            return iter(segs), NS(language="kn", language_probability=0.93)

    monkeypatch.setattr(asr, "_model", FakeModel())
    monkeypatch.setattr(asr, "_model_name", "small")
    segs, lang = asr.transcribe("x.wav")
    assert lang == "kn" and [s.text for s in segs] == ["ನಮಸ್ಕಾರ"] and segs[0].lang == "kn"
    assert seen["language"] is None and seen["vad_filter"] is True  # language detected automatically
    asr.transcribe("x.wav", language="hi")
    assert seen["language"] == "hi" and seen["hotwords"] is None
    asr.transcribe("x.wav", hint="Atithya Vidyarthi Bhavan")
    assert seen["hotwords"] == "Atithya Vidyarthi Bhavan"


def test_ocr_drops_numbers_and_symbol_noise_but_keeps_indic_words():
    assert not ocr.plausible("1111") and not ocr.plausible("५490") and not ocr.plausible("೧] a")
    assert ocr.plausible("Biryani Fest") and ocr.plausible("ಗಾಂಧಿ ಬಜಾರ್") and ocr.plausible("इंदिरानगर")


def test_hint_from_caption_keeps_names_and_drops_noise():
    from app.core.text import hint_from_caption

    cap = "Breakfast Walk 🔖\n1. Vidyarthi Bhavan\n2. Atithya #BasavanagudiFood @bangaloreepicure https://x.com/a ಗಾಂಧಿ ಬಜಾರ್"
    assert hint_from_caption(cap) == "Breakfast Walk Vidyarthi Bhavan Atithya ಗಾಂಧಿ ಬಜಾರ್"
    assert len(hint_from_caption("word " * 500)) <= 300


def test_perceive_passes_the_hint_only_when_there_is_one(tmp_path, monkeypatch):
    from app.core.document import Segment

    seen = []

    def asr_fn(path, hint=None):
        seen.append(hint)
        return [Segment(source="speech", text="x y z", start=0, end=1)], "en"

    f = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "1", "-i", "sine=frequency=300", str(f)], check=True)
    perceive.perceive_file(str(f), asr_fn=asr_fn, hint="Atithya")
    assert seen == ["Atithya"]
