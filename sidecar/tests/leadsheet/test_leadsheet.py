"""Lead-sheet reading: the pdfminer adapter's quirks, detection, dispatch.

SongSelect lead sheets are copyrighted, so none are committed. The quirks
the adapter exists for are reproduced with hand-built PDFs instead:
letter-by-letter text (one text operation per glyph, as SongSelect's
generator writes), stroke widths set through a graphics-state dictionary
(`/LW` via `gs`, which pdfminer ignores on its own), and `re` rectangles.
`test_real_lead_sheets` reads the real sheets when they are on this machine.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from auto_presenter.leadsheet import parse_lead_sheet
from auto_presenter.leadsheet.pdfread import read_pages
from auto_presenter.protocol import ParseLeadSheetRequest
from auto_presenter.server_leadsheet import _handle_parse_lead_sheet


def make_pdf(content: str, *, extgstate: str = "", height: int = 792) -> bytes:
    """A one-page PDF with Helvetica as /F1 and the given content stream."""
    stream = content.encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 {height}] /Contents 4 0 R "
            f"/Resources << /Font << /F1 5 0 R >> /ExtGState << {extgstate} >> >> >>"
        ).encode(),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_letters_drawn_one_by_one_join_into_words():
    # "Let" written as three separate text operations whose glyphs touch, then
    # "the" after a word gap: two spans, as PyMuPDF would report them.
    helv = {"L": 556, "e": 556, "t": 278}
    x = 100.0
    ops = []
    for ch in "Let":
        ops.append(f"BT /F1 10 Tf {x:.3f} 700 Td ({ch}) Tj ET")
        x += helv[ch] / 100
    ops.append(f"BT /F1 10 Tf {x + 12:.3f} 700 Td (the) Tj ET")
    spans = read_pages(make_pdf("\n".join(ops)))[0].spans
    assert [s["text"] for s in spans] == ["Let", "the"]
    assert spans[0]["font"] == "Helvetica"
    # y runs down from the top of the page, as in PyMuPDF
    assert spans[0]["bbox"][1] < 792 - 700 < spans[0]["bbox"][3]


def test_line_width_from_a_graphics_state_dictionary():
    pdf = make_pdf("/G1 gs 100 500 m 300 500 l S", extgstate="/G1 << /Type /ExtGState /LW 0.75 >>")
    (d,) = read_pages(pdf)[0].drawings
    assert d["items"][0][0] == "l"
    assert d["width"] == pytest.approx(0.75)
    assert d["fill"] is None
    assert d["rect"].height < 0.01 and d["rect"].width == pytest.approx(200)


def test_rectangles_stay_rectangles_and_fills_are_marked():
    (d,) = read_pages(make_pdf("10 20 30 2 re f"))[0].drawings
    assert [it[0] for it in d["items"]] == ["re"]
    assert d["fill"] is True and d["width"] is None
    assert d["rect"].width == pytest.approx(30) and d["rect"].height == pytest.approx(2)


def test_a_closed_curve_path_closes_with_a_line():
    pdf = make_pdf("100 100 m 110 110 120 110 130 100 c 120 90 l h f")
    (d,) = read_pages(pdf)[0].drawings
    assert "".join(it[0] for it in d["items"]) == "cll"


def test_text_only_pdf_is_not_a_lead_sheet():
    sheet = parse_lead_sheet(make_pdf("BT /F1 12 Tf 72 700 Td (Amazing grace how sweet) Tj ET"))
    assert not sheet.is_lead_sheet
    assert sheet.reason
    assert sheet.score_json() == ""


def test_staff_lines_without_notes_are_not_a_lead_sheet():
    staff = "\n".join(f"72 {600 + 6 * i} m 540 {600 + 6 * i} l S" for i in range(5))
    sheet = parse_lead_sheet(make_pdf("/G1 gs\n" + staff, extgstate="/G1 << /LW 0.6 >>"))
    assert not sheet.is_lead_sheet


def test_garbage_is_not_a_lead_sheet_and_does_not_raise():
    sheet = parse_lead_sheet(b"%PDF-1.4 this is not really a pdf")
    assert not sheet.is_lead_sheet
    assert sheet.reason


@pytest.fixture()
def fake_ws():
    ws = MagicMock()
    sent: list[dict] = []

    async def _send(data):
        sent.append(json.loads(data))

    ws.send = MagicMock(side_effect=_send)
    ws.sent = sent
    return ws


@pytest.mark.asyncio
async def test_dispatch_answers_not_a_lead_sheet_for_a_chart_pdf(fake_ws):
    pdf = make_pdf("BT /F1 12 Tf 72 700 Td (G C D) Tj ET")
    await _handle_parse_lead_sheet(
        fake_ws,
        ParseLeadSheetRequest(id=7, pdf_base64=base64.b64encode(pdf).decode(), file_name="x.pdf"),
    )
    reply = fake_ws.sent[-1]
    assert reply["kind"] == "parse_lead_sheet_response" and reply["id"] == 7
    assert reply["is_lead_sheet"] is False
    assert reply["score_json"] == ""


@pytest.mark.asyncio
async def test_dispatch_rejects_bad_base64_without_raising(fake_ws):
    await _handle_parse_lead_sheet(fake_ws, ParseLeadSheetRequest(id=8, pdf_base64="***"))
    assert fake_ws.sent[-1]["is_lead_sheet"] is False


#: Bar counts read from the SongSelect sheets used in the music-only spike.
#: The reader agrees with the PyMuPDF original bar for bar on all nine.
REAL_SHEETS = {
    "Awesome God-lead-Cm.pdf": 41,
    "Build My Life-lead-G.pdf": 34,
    "Doxology-lead-F#.pdf": 85,
    "I Surrender All-lead-D.pdf": 16,
    "King Of My Heart-lead-Bb.pdf": 63,
    "Nothing But The Blood (Plainfield)-lead-G.pdf": 16,
    "O Come All Ye Faithful (Adeste Fideles)-lead-G.pdf": 21,
    "Reckless Love-lead-F#.pdf": 109,
    "What A Beautiful Name-lead-D.pdf": 47,
}


@pytest.mark.parametrize("name,bars", sorted(REAL_SHEETS.items()))
def test_real_lead_sheets(name, bars):
    path = Path.home() / "Downloads" / name
    if not path.exists():
        pytest.skip("SongSelect sheets are not committed; present only on the dev machine")
    sheet = parse_lead_sheet(path.read_bytes(), name)
    assert sheet.is_lead_sheet
    assert sheet.bar_count == bars
    assert sheet.problem_bars <= 3
    assert sheet.title and sheet.title.lower().split()[0] in name.lower()
    assert sheet.key == name.rsplit("-lead-", 1)[1].removesuffix(".pdf")
    score = json.loads(sheet.score_json())
    assert score["reader_version"] >= 1 and len(score["bars"]) == bars
