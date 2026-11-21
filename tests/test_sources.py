import shutil
import subprocess

import pytest
from PIL import Image

from app.core.document import Segment
from app.services.ingest import IngestError
from app.sources import local, safeurl
from app.sources.safeurl import UrlRejected, validate_url

HOSTS = ["instagram.com", "youtu.be"]


def test_validate_url_accepts_known_hosts_and_subdomains():
    assert validate_url("https://www.instagram.com/reel/abc/", HOSTS) == "https://www.instagram.com/reel/abc/"
    assert validate_url("https://youtu.be/xyz", HOSTS)


@pytest.mark.parametrize("bad", [
    "ftp://instagram.com/x", "not a url", "https://evil.com/a", "https://instagram.com.evil.com/a",
    "https://notinstagram.com/a", "https://user:pw@instagram.com/a", "https://instagram.com/a b", "http://",
    "https://instagram.com/" + "a" * 600,
])
def test_validate_url_rejects(bad):
    with pytest.raises(UrlRejected):
        validate_url(bad, HOSTS)


def test_validate_url_blocks_private_and_unresolvable_addresses():
    for ip in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1"):
        with pytest.raises(UrlRejected):
            validate_url("https://instagram.com/a", HOSTS, resolver=lambda h, ip=ip: [ip])
    with pytest.raises(UrlRejected):  # one private answer among public ones is still refused
        validate_url("https://instagram.com/a", HOSTS, resolver=lambda h: ["8.8.8.8", "10.0.0.1"])

    def nxdomain(host):
        raise OSError("no such host")

    with pytest.raises(UrlRejected):
        validate_url("https://instagram.com/a", HOSTS, resolver=nxdomain)


def test_ingest_text_builds_a_text_document():
    ing = local.ingest_text("  Free dosa at MTR this Sunday near Lalbagh  ")
    assert ing.document.kind == "text" and ing.transcript == "[text] Free dosa at MTR this Sunday near Lalbagh"
    assert ing.caption == ""
    with pytest.raises(IngestError):
        local.ingest_text("hi")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_ingest_file_image_and_audio(tmp_path):
    img = tmp_path / "shot.png"
    Image.new("RGB", (200, 120), (10, 10, 10)).save(img)
    from app.perception.ocr import OcrLine

    ing = local.ingest_file(str(img), "shot.png", ocr_fn=lambda p, l, c: [OcrLine("Sale 50% off", 90)])
    assert ing.document.kind == "image" and "[screen] Sale 50% off" in ing.transcript

    wav = tmp_path / "clip.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "2", "-i", "sine=frequency=440", str(wav)], check=True)
    speech = [Segment(source="speech", text="namaskara", start=0, end=1, lang="kn")]
    ing = local.ingest_file(str(wav), "clip.wav", asr_fn=lambda p: (speech, "kn"))
    assert ing.document.kind == "audio" and ing.document.language == "kn"


def test_ingest_file_rejects_junk_and_empty_results(tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00\x01 not media")
    with pytest.raises(IngestError):
        local.ingest_file(str(junk), "junk.bin")
