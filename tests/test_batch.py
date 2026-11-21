import json
import os
from datetime import datetime
from types import SimpleNamespace

from app.core.document import Segment, VideoDocument
from app.evalkit import batch
from app.services.ingest import IngestError, from_document

LINKS = """https://youtube.com/shorts/tCpsk0y5INA?si=zRzAFEmlBypQ5Cvj
 https://youtube.com/shorts/fTyAyzCKb2s?si=abc
not a link
https://youtube.com/shorts/tCpsk0y5INA?si=again
https://www.youtube.com/watch?v=XYZ123&si=q"""


def test_clean_links_strips_tracking_and_duplicates():
    assert batch.clean_links(LINKS) == [
        "https://youtube.com/shorts/tCpsk0y5INA", "https://youtube.com/shorts/fTyAyzCKb2s",
        "https://www.youtube.com/watch?v=XYZ123",
    ]
    assert batch.case_id("https://youtube.com/shorts/tCpsk0y5INA") == "yt_tCpsk0y5INA"
    assert batch.case_id("https://www.youtube.com/watch?v=XYZ123") == "yt_XYZ123"


def fake_ingest(url):
    if "fTyAy" in url:
        raise IngestError("private video")
    doc = VideoDocument(language="kn", segments=[Segment(source="caption", text="Biryani walk"),
                                                 Segment(source="speech", text="Meghana Foods in Indiranagar", start=2, end=4)])
    return from_document(doc)


def fake_llm(system, user):
    return json.dumps({"items": [{"kind": "place", "category": "food", "title": "Meghana Foods", "venue_en": "Meghana Foods",
                                  "city": "Bengaluru", "evidence": "Meghana Foods in Indiranagar", "confidence": 0.9}], "signals": []})


def test_batch_writes_drafts_survives_failures_and_resumes(tmp_path):
    out = str(tmp_path / "cases")
    urls = batch.clean_links(LINKS)
    logs = []
    status = batch.run_batch(urls, fake_ingest, fake_llm, out, now=datetime(2026, 11, 21), pause=0, log=logs.append)
    assert [status[u][:3] for u in urls] == ["ok", "Ing", "ok"]  # the private video failed, the others went on
    case = json.load(open(os.path.join(out, "yt_tCpsk0y5INA.json"), encoding="utf-8"))
    assert case["labeled"] is False and case["language"] == "kn" and case["expected"]["items"][0]["venue"] == "Meghana Foods"
    assert "[2:" not in case["transcript"] and "[0:02-0:04 speech]" in case["transcript"]
    again = batch.run_batch(urls, fake_ingest, fake_llm, out, pause=0, log=logs.append)
    assert again[urls[0]] == "skipped" and again[urls[1]].startswith("Ing")  # failed ones are retried
    sheet = batch.review_sheet(out)
    assert "yt_tCpsk0y5INA" in sheet and "Meghana Foods" in sheet and "quote:" in sheet
