import pytest

from app.core.chunking import chunk_lines
from app.core.document import Line, Segment, VideoDocument, parse_rendered
from app.core.grounding import ground
from app.core.text import approx_tokens, normalize


def test_normalize_keeps_indic_text_intact():
    kn = "ಇಂದಿರಾನಗರದಲ್ಲಿ"
    hi = "इंदिरानगर"
    assert normalize(kn + "!!") == kn  # vowel signs are combining marks and must survive
    assert normalize(hi + ", ") == hi
    assert normalize("Café  ₹250 — Deal!") == "café 250 deal"
    assert normalize("स‍स्ता") == "सस्ता"  # zero-width joiner dropped, word not split
    assert normalize(None) == ""


def test_approx_tokens_never_undercounts_dense_scripts():
    assert approx_tokens("") == 0
    assert approx_tokens("one two three") >= 3
    assert approx_tokens("ಇಂದಿರಾನಗರದಲ್ಲಿಬಿರಿಯಾನಿ") >= 5  # one long word still costs several tokens


def test_document_render_and_parse_round_trip():
    doc = VideoDocument(segments=[
        Segment(source="caption", text="Weekend food crawl"),
        Segment(source="screen", text="50% OFF", start=4, end=9),
        Segment(source="speech", text="free dosa at MTR", start=3, end=5),
        Segment(source="text", text="pasted note"),
    ])
    assert doc.caption == "Weekend food crawl"
    rendered = doc.render_body()
    assert rendered.splitlines() == [
        "[0:03-0:05 speech] free dosa at MTR", "[0:04-0:09 screen] 50% OFF", "[text] pasted note",
    ]  # time order, untimed last, caption excluded
    lines = parse_rendered(rendered)
    assert [(l.source, l.start, l.text) for l in lines] == [
        ("speech", 3, "free dosa at MTR"), ("screen", 4, "50% OFF"), ("text", None, "pasted note"),
    ]


def test_parse_rendered_accepts_plain_untagged_text():
    assert [l.text for l in parse_rendered("just words\n\nmore words")] == ["just words", "more words"]


def mk(n, words=30):
    return [Line(source="speech", start=i * 5.0, text=" ".join(["word"] * words)) for i in range(n)]


def test_chunks_cover_everything_in_order_with_timestamps_and_overlap():
    lines = mk(10)
    chunks = chunk_lines(lines, max_tokens=100, overlap_tokens=40)
    assert chunks[0].first_line == 0 and chunks[-1].last_line == 9
    assert all(c.tokens <= 100 for c in chunks)
    for a, b in zip(chunks, chunks[1:]):
        assert b.first_line > a.first_line  # always moves forward
        assert b.first_line <= a.last_line + 1  # nothing skipped
    assert chunks[0].start == 0.0 and chunks[0].end == 5.0


def test_chunk_oversized_line_and_bad_args():
    big = [Line(source="speech", start=None, text="w " * 500)]
    out = chunk_lines(big, max_tokens=50, overlap_tokens=5)
    assert len(out) == 1 and out[0].start is None
    with pytest.raises(ValueError):
        chunk_lines(big, max_tokens=50, overlap_tokens=50)


LINES = [
    Line(source="speech", start=12.0, text="Third Wave Coffee has a buy one get one offer on cold brews"),
    Line(source="screen", start=14.0, text="ಇಂದಿರಾನಗರ ಬಿರಿಯಾನಿ ಹಬ್ಬ"),
]


def test_ground_finds_exact_quote_and_reports_where():
    hit = ground("buy one get one offer on cold brews", LINES)
    assert hit and hit.source == "speech" and hit.start == 12.0


def test_ground_tolerates_small_ocr_or_asr_errors():
    assert ground("Third Wave Cofee has a buy one get one", LINES) is not None


def test_ground_works_in_other_scripts_and_in_caption():
    assert ground("ಇಂದಿರಾನಗರ ಬಿರಿಯಾನಿ", LINES).source == "screen"
    assert ground("Open till midnight", [], caption="Open till midnight every day!").source == "caption"


def test_ground_rejects_invented_quote_and_too_short_quote():
    assert ground("Free pizza for every student on Sunday", LINES) is None
    assert ground("ab", LINES) is None
    assert ground(None, LINES) is None


def test_short_ocr_fragment_cannot_prove_a_long_quote():
    """Regression from a real reel: the OCR fragment 'VANA' used to 'ground' a long spoken quote."""
    lines = [
        Line(source="speech", start=4.0, text="Spot number one!"),
        Line(source="screen", start=1.0, text="VANA"),
        Line(source="screen", start=3.0, text="1111"),
        Line(source="speech", start=5.0, text="Vidyarthivana!"),
    ]
    hit = ground("Spot number one! Vidyarthivana!", lines)
    assert hit is not None and hit.source == "speech" and hit.start == 4.0  # across two spoken lines, not the OCR noise


def test_ground_prefers_the_single_line_and_still_rejects_inventions_across_lines():
    lines = [Line(source="speech", start=1.0, text="Open till midnight"), Line(source="speech", start=3.0, text="Free parking")]
    assert ground("Open till midnight", lines).start == 1.0
    assert ground("Free parking for everyone all night long", lines) is None
