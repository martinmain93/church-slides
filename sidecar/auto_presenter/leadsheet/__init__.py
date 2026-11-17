"""Lead sheets: melody, chords and lyrics read out of a notation PDF.

A lead sheet attached to a song is a *reference* for the score follower:
what each slide's line sounds like (notes, chords, the rhythm of its words)
and the order the arrangement runs in. `parse_lead_sheet` decides whether a
PDF is one at all — vector staves with notes on them — and, if so, returns
the parsed score plus what the importer needs to match it to a library song
(title, CCLI number, key).

The parsed score is stored as-is (`omr.extract`'s JSON, tagged with
`READER_VERSION`) rather than in a follower-specific shape, so the reader can
improve and stored sheets can be re-read without the follower changing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

#: Bumped whenever `omr.extract`'s output changes shape or meaning.
READER_VERSION = 1

#: Fewer bars than this is not a song's lead sheet (a stray staff on a
#: worship-order handout, a single example bar).
MIN_BARS = 4

_CCLI = re.compile(r"CCLI\s*Song\s*#?\s*(\d{3,})", re.I)
#: SongSelect names its files "<Title>-lead-<Key>.pdf".
_FILE_KEY = re.compile(r"-lead-([A-G](?:#|b)?m?)\.pdf$", re.I)
_MAJOR_BY_FIFTHS = {
    -7: "Cb",
    -6: "Gb",
    -5: "Db",
    -4: "Ab",
    -3: "Eb",
    -2: "Bb",
    -1: "F",
    0: "C",
    1: "G",
    2: "D",
    3: "A",
    4: "E",
    5: "B",
    6: "F#",
    7: "C#",
}


@dataclass
class LeadSheet:
    is_lead_sheet: bool
    reason: str = ""
    title: str | None = None
    ccli: str | None = None
    key: str | None = None
    copyright: str | None = None
    bar_count: int = 0
    problem_bars: int = 0
    sections: list[str] = field(default_factory=list)
    score: dict | None = None

    def score_json(self) -> str:
        if self.score is None:
            return ""
        return json.dumps(
            {"reader_version": READER_VERSION, **self.score}, separators=(",", ":"), default=_plain
        )


def _plain(o):
    return o.__dict__ if hasattr(o, "__dict__") else str(o)


def _metadata(
    pages, file_name: str, key_fifths: int
) -> tuple[str | None, str | None, str | None, str | None]:
    title = ccli = copyright = None
    if pages:
        first = pages[0]
        top = [
            s
            for s in first.spans
            if s["bbox"][1] < 0.2 * first.height
            and "Leipzig" not in s["font"]
            and s["text"].strip()
        ]
        if top:
            title = max(top, key=lambda s: s["size"])["text"].strip() or None
    for page in pages:
        for s in page.spans:
            t = s["text"].strip()
            m = _CCLI.search(t)
            if m and ccli is None:
                ccli = m.group(1)
            if t.startswith("©") and copyright is None:
                copyright = t
    m = _FILE_KEY.search(file_name or "")
    key = m.group(1) if m else _MAJOR_BY_FIFTHS.get(key_fifths)
    return title, ccli, key, copyright


def parse_lead_sheet(pdf: bytes, file_name: str = "") -> LeadSheet:
    """Read a PDF as a lead sheet, or say why it is not one. Never raises on
    a PDF that is merely not music: every PDF a user imports comes through
    here first, and most are chord charts or lyrics."""
    from .omr import extract
    from .pdfread import read_pages

    try:
        pages = read_pages(pdf)
    except Exception as err:  # malformed or encrypted PDF: not ours to read
        return LeadSheet(False, reason=f"unreadable PDF: {err}")
    if not any(d for p in pages for d in p.drawings):
        return LeadSheet(False, reason="no vector drawing on any page")
    try:
        score = extract(pdf, source=file_name, pages=pages)
    except Exception as err:  # the reader met notation it does not understand
        return LeadSheet(False, reason=f"could not read the notation: {err}")
    bars = score.get("bars") or []
    if len(bars) < MIN_BARS:
        return LeadSheet(False, reason=f"{len(bars)} bars of music found")
    title, ccli, key, copyright = _metadata(pages, file_name, int(score.get("key_fifths") or 0))
    as_dicts = json.loads(json.dumps(score, default=_plain))
    return LeadSheet(
        True,
        title=title,
        ccli=ccli,
        key=key,
        copyright=copyright,
        bar_count=len(bars),
        problem_bars=sum(1 for b in as_dicts["bars"] if b.get("problems")),
        sections=[b["section"] for b in as_dicts["bars"] if b.get("section")],
        score=as_dicts,
    )
