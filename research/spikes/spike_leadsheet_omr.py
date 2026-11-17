"""Read melody, chords and lyrics out of a SongSelect lead-sheet PDF.

Isolated spike for Plan/MUSIC_ONLY_MATCHING.md, stage 0. Not wired into the
app. SongSelect lead sheets are rendered by Verovio, so nothing is scanned:
the staff lines, noteheads, stems, beams, flags, rests and dots are vector
paths at exact positions, and the lyrics, chord symbols and section labels are
real text. This reads those primitives back into notes. PyMuPDF is AGPL, so it
is a spike-only dependency and never ships.

    uv run --no-project --with pymupdf python research/spikes/spike_leadsheet_omr.py \
        "~/Downloads/What A Beautiful Name-lead-D.pdf" -o score.json

Every bar of the main voice is checked against the metre. A bar that does not
add up is reported, never silently accepted.

Two voices: the full-size noteheads are the main melody. Verovio draws a
second lyric row's different rhythm with smaller (cue-size) noteheads, which
are read as voice 2 and kept beside voice 1 rather than merged into it.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

# Path signatures (the sequence of PyMuPDF item kinds) and rough sizes of the
# Verovio/Leipzig glyphs, read off the five SongSelect sheets we have. Sizes
# are in PDF points at SongSelect's staff size (staff space ~4.55 pt).
HEAD_FILLED = "cccc"
HEAD_HOLLOW = "ccccccccc"
HEAD_WHOLE = "ccccccclccccccccc"  # whole note: hollow, wider, never stemmed
DOT = "cccccccc"
REST_8 = "cccccllccclllclllcc"
REST_4_PREFIX = ("cclccccccccccccl",)
REST_16 = "ccclcccccccccclllcllccccc"
TUPLET_2 = "cclclllccllcllcclclccllcclclcclccc"  # the "2" over a duplet
# Time-signature digits (Leipzig, drawn as paths about 7 x 9 pt).
TIMESIG_DIGITS = {
    "clclcllllllllclllclll": 4,
    "cllccllcccccclccccccclclcclccllc": 2,
    "ccccclccccclccccclccccccc": 3,
    "ccccclccccclcccccclcc": 6,
    "cclccccccclccccclccccc": 8,
}
REST_BAR = "lclclclc"  # half rest (on the middle line) or whole rest (hanging)
FLAG_8_UP = "ccccllccccccclc"
FLAG_8_DOWN = "clcccccccllcccc"
FLAG_16_UP = "ccclllcccccccclccccccc"
FLAG_16_DOWN = "clcccccccclllccccccccc"
SHARP_PREFIX = "llllllllllllllllllll"
FLAT_PREFIX = "ccclccllllclccccll"

STAFF_LINE_W = 0.329
STEM_W = 0.456

CHORD_RE = re.compile(r"^[A-G][#♯b♭]?(m|maj|min|dim|aug|sus|add|\d|\(|\)|/|[A-G]|[#♯b♭])*$")
# Music-font characters that appear as text: accidentals inside chord names,
# and the navigation marks.
SMUFL_TEXT = {0xEA66: "#", 0xEA64: "b", 0xE047: "<segno>", 0xE048: "<coda>"}
LETTERS = "CDEFGAB"
NATURAL_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP_ORDER = "FCGDAEB"
FLAT_ORDER = "BEADGCF"


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class Staff:
    page: int
    lines: list[float]  # five y values, top to bottom
    x0: float
    x1: float

    @property
    def space(self) -> float:
        return (self.lines[4] - self.lines[0]) / 4


@dataclass
class Note:
    voice: int
    onset: float  # quarter notes from the start of the bar
    dur: float  # quarter notes
    midi: int | None  # None for a rest
    x: float
    tied_from_prev: bool = False
    tie_out: bool = False  # a tie leaves the system from this note
    syllables: dict[int, str] = field(default_factory=dict)  # lyric row -> text
    hyphen_after: dict[int, bool] = field(default_factory=dict)


@dataclass
class Bar:
    number: int
    page: int
    system: int
    x0: float
    x1: float
    staff_top: float = 0.0
    staff_space: float = 0.0
    start_repeat: bool = False
    end_repeat: bool = False
    voltas: list[int] = field(default_factory=list)
    segno: bool = False
    ds: bool = False
    section: str | None = None
    chords: list[dict] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _sig(d) -> str:
    return "".join(i[0] for i in d["items"])


def _outline(d) -> list[tuple[float, float]]:
    pts = []
    for it in d["items"]:
        if it[0] == "l":
            pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
        elif it[0] == "re":
            r = it[1]
            pts += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
        elif it[0] == "qu":
            q = it[1]
            pts += [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) > 1e-3:
            out.append(p)
    return out


def _box(r) -> Box:
    return Box(r.x0, r.y0, r.x1, r.y1)


def find_staves(drawings, page: int) -> list[Staff]:
    rows: dict[float, list[Box]] = {}
    for d in drawings:
        r = d["rect"]
        if _sig(d) == "l" and r.height < 0.01 and r.width > 20 and abs((d.get("width") or 0) - STAFF_LINE_W) < 0.05:
            rows.setdefault(round(r.y0, 1), []).append(_box(r))
    ys = sorted(rows)
    staves = []
    i = 0
    while i + 4 < len(ys):
        cand = ys[i : i + 5]
        gaps = [b - a for a, b in zip(cand, cand[1:])]
        if max(gaps) - min(gaps) < 0.3 and 3 < gaps[0] < 7:
            segs = [b for y in cand for b in rows[y]]
            staves.append(Staff(page, cand, min(b.x0 for b in segs), max(b.x1 for b in segs)))
            i += 5
        else:
            i += 1
    return staves


def _in_staff_band(staff: Staff, b: Box, above: float = 30, below: float = 30) -> bool:
    return staff.lines[0] - above <= b.yc <= staff.lines[4] + below


def diatonic_step(staff: Staff, y: float) -> int:
    """Steps above E4 (the bottom line of a treble staff)."""
    return round((staff.lines[4] - y) / (staff.space / 2))


def step_to_letter_octave(step: int) -> tuple[str, int]:
    idx = 2 + step  # E is index 2 in CDEFGAB
    return LETTERS[idx % 7], 4 + idx // 7


def letter_midi(letter: str, octave: int, alter: int) -> int:
    return 12 * (octave + 1) + NATURAL_PC[letter] + alter


def read_page_symbols(page, page_no: int):
    drawings = page.get_drawings()
    staves = find_staves(drawings, page_no)
    sym: dict[str, list] = {k: [] for k in (
        "head", "hollow", "dot", "rdot", "stem", "beam", "flag", "rest", "bar", "acc", "curve", "hline", "tuplet", "hyphen", "tsig", "other"
    )}
    for d in drawings:
        s = _sig(d)
        r = d["rect"]
        b = _box(r)
        fill = d.get("fill") is not None
        width = d.get("width") or 0
        if s == "l":
            if r.width < 0.01:
                if abs(width - STEM_W) < 0.05:
                    sym["stem"].append(b)
                elif width > 0.6:
                    sym["bar"].append((b, width))
                else:
                    sym["other"].append((s, b))
            elif r.height < 0.01:
                if abs(width - STAFF_LINE_W) >= 0.05:
                    sym["hline"].append(b)  # ledger lines, volta brackets
            continue
        if not fill:
            continue
        small = b.h < 4.2
        if s in TIMESIG_DIGITS and 6.0 < b.w < 8.6 and 8.4 < b.h < 9.8:
            sym["tsig"].append((b, TIMESIG_DIGITS[s]))
            continue
        if s == HEAD_FILLED and b.w > 3.5 and b.h > 3:
            sym["head"].append((b, small))
        elif s == HEAD_FILLED and b.w < 2.2:
            sym["rdot"].append(b)
        elif s == HEAD_WHOLE and b.w > 5.0:
            sym["hollow"].append((b, b.h < 4.2))
        elif s == HEAD_HOLLOW and b.w > 3.5:
            sym["hollow"].append((b, small))
        elif s == DOT:
            sym["dot"].append(b)
        elif s == "re" and b.h < 1.0 and b.w < 6:
            sym["hyphen"].append(b)  # lyric hyphen between two syllables
        elif s in ("re", "llll", "qu") and b.h > 1.5 and b.w > 2.0 and b.h < 12:
            sym["beam"].append((b, _outline(d)))
        elif s == FLAG_8_UP or s == FLAG_8_DOWN:
            sym["flag"].append((b, 1, s == FLAG_8_UP))
        elif s == FLAG_16_UP or s == FLAG_16_DOWN:
            sym["flag"].append((b, 2, s == FLAG_16_UP))
        elif s == REST_8:
            sym["rest"].append((b, 0.5, b.h < 7))
        elif s == REST_16:
            sym["rest"].append((b, 0.25, False))
        elif s.startswith(TUPLET_2):
            sym["tuplet"].append((b, 2))
        elif s.startswith(REST_4_PREFIX):
            sym["rest"].append((b, 1.0, b.h < 13))
        elif s == REST_BAR:
            sym["rest"].append((b, "bar", False))
        elif s.startswith(SHARP_PREFIX):
            sym["acc"].append((b, +1))
        elif s.startswith(FLAT_PREFIX):
            sym["acc"].append((b, -1))
        elif s == "cc":
            sym["curve"].append(b)
        else:
            sym["other"].append((s, b))
    return staves, sym, drawings


def read_text(page):
    """Words with font info. Lyrics, chords, labels and marks all come from here."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                t = span["text"]
                if "Leipzig" in span["font"]:
                    t = "".join(SMUFL_TEXT.get(ord(c), "") for c in t)
                if not t.strip():
                    continue
                out.append({
                    "text": t.strip(),
                    "font": span["font"],
                    "size": round(span["size"], 1),
                    "box": Box(*span["bbox"]),
                })
    return out


FOOTER = re.compile(r"©|CCLI|SongSelect|www\.|Terms of Use|All rights reserved|Publishing|Music By|Words (and Music )?by", re.I)


def _chordlike(t: str) -> bool:
    t = t.replace("♯", "#").replace("♭", "b")
    return bool(CHORD_RE.match(t)) or bool(re.fullmatch(r"\(\d+\)|N\.?C\.?", t))


def assign_text(staves: list[Staff], text) -> dict[int, list]:
    """Give every text item to the staff it belongs to.

    Lyrics hang below their staff; chord symbols, section labels, endings and
    navigation marks sit above theirs. In the gap between two systems, a row
    that is all chord symbols, or bold or italic, belongs to the staff below,
    and plain text to the staff above. Footer and title text belongs to none."""
    out: dict[int, list] = {id(st): [] for st in staves}
    if not staves:
        return out
    staves = sorted(staves, key=lambda st: st.lines[0])
    merged = _merge_chord_spans([(staves[0], w) for w in text])
    rows: dict[tuple[int, int], list] = {}
    for _, w in merged:
        if FOOTER.search(w["text"]):
            continue
        yc = w["box"].yc
        k = next((i for i, st in enumerate(staves) if st.lines[0] > yc), len(staves))
        rows.setdefault((k, round(w["box"].y0 / 3)), []).append(w)
    for (k, _), ws in rows.items():
        above = staves[k - 1] if k > 0 else None
        below = staves[k] if k < len(staves) else None
        if above and above.lines[0] - 5 <= ws[0]["box"].yc <= above.lines[4] + 5:
            owner = above  # inside a staff: marks drawn over it
        else:
            chord_row = sum(_chordlike(w["text"]) for w in ws) >= 0.7 * len(ws) and not any(
                w["text"][:1].islower() for w in ws
            )
            styled = all(("Bold" in w["font"] or "Italic" in w["font"] or "Leipzig" in w["font"]) for w in ws)
            if below is None:
                owner = above
            elif above is None:
                owner = below if ws[0]["box"].yc > below.lines[0] - 70 else None
            else:
                owner = below if (chord_row or styled) else above
        if owner is not None:
            out[id(owner)].extend(ws)
    return out


def key_signature(staff: Staff, sym) -> tuple[dict[str, int], int]:
    """Alterations from the accidentals clustered right after the clef."""
    heads = [b for b, _ in sym["head"] + sym["hollow"] if _in_staff_band(staff, b, 15, 15)]
    first_head = min((b.x0 for b in heads), default=staff.x1)
    accs = sorted(
        [(b, a) for b, a in sym["acc"] if _in_staff_band(staff, b, 10, 10) and b.x0 < min(first_head, staff.x0 + 60)],
        key=lambda t: t[0].x0,
    )
    alters: dict[str, int] = {}
    if not accs:
        return alters, 0
    kind = accs[0][1]
    order = SHARP_ORDER if kind > 0 else FLAT_ORDER
    for i, _ in enumerate(accs):
        alters[order[i]] = kind
    return alters, kind * len(accs)


def stem_for(head: Box, stems: list[Box]) -> tuple[Box | None, bool]:
    best = None
    for s in stems:
        up = abs(s.x0 - head.x1) < 0.9 and abs(s.y1 - head.yc) < 3.5 and s.y0 < head.yc
        down = abs(s.x0 - head.x0) < 0.9 and abs(s.y0 - head.yc) < 3.5 and s.y1 > head.yc
        if up or down:
            if best is None or s.h > best[0].h:
                best = (s, up)
    return best if best else (None, False)


def beam_y_at(beam, x: float) -> float:
    """Centre of a beam's outline at x. A slanted beam's box is much taller
    than the beam, so the box centre would put it at the wrong level."""
    box, pts = beam
    ys = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        if min(x0, x1) - 0.01 <= x <= max(x0, x1) + 0.01 and abs(x1 - x0) > 0.01:
            ys.append(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return (min(ys) + max(ys)) / 2 if ys else box.yc


def beams_on(stem: Box, up: bool, beams, space: float) -> int:
    """Beam levels reaching this stem. Verovio splits a secondary beam into
    segments that meet at a stem, so count distinct levels, not segments."""
    tip = stem.y0 if up else stem.y1
    levels: set[int] = set()
    for beam in beams:
        box = beam[0]
        if not (box.x0 - 0.8 <= stem.x0 <= box.x1 + 0.8):
            continue
        x = min(max(stem.x0, box.x0), box.x1)
        off = (beam_y_at(beam, x) - tip) * (1 if up else -1)
        if -2.0 <= off <= 3.0 * space:
            levels.add(round(off / (0.75 * space)))
    return len(levels)


def flags_on(stem: Box, up: bool, flags) -> int:
    n = 0
    for b, count, fup in flags:
        if abs(b.x0 - stem.x0) < 1.0 and fup == up and (b.y0 <= stem.y1 + 1 and b.y1 >= stem.y0 - 1):
            n = max(n, count)
    return n


def is_dotted(b: Box, dots: list[Box], used: set) -> bool:
    for i, d in enumerate(dots):
        if i in used:
            continue
        if 0.5 < d.x0 - b.x1 < 4.0 and abs(d.yc - b.yc) < 3.0:
            used.add(i)
            return True
    return False


def extract(pdf: Path) -> dict:
    doc = pymupdf.open(pdf)
    bars: list[Bar] = []
    system_no = 0
    key_alters: dict[str, int] = {}
    key_fifths = 0
    words_all = []
    hyphens_all = []
    tsigs: list[tuple[int, float, float]] = []
    marks = []
    for page_no, page in enumerate(doc):
        staves, sym, _ = read_page_symbols(page, page_no)
        text = read_text(page)
        owners = assign_text(staves, text)
        for staff in staves:
            ka, kf = key_signature(staff, sym)
            if ka:
                key_alters, key_fifths = ka, kf
            sp = staff.space
            top, bot = staff.lines[0], staff.lines[4]
            # barlines that span this staff
            blines = sorted(
                [(b, w) for b, w in sym["bar"] if abs(b.y0 - top) < 1.5 and abs(b.y1 - bot) < 1.5],
                key=lambda t: t[0].x0,
            )
            xs = [b.x0 for b, _ in blines]
            # merge thick+thin pairs into one boundary
            bounds: list[dict] = []
            for b, w in blines:
                if bounds and b.x0 - bounds[-1]["x1"] < 4:
                    bounds[-1]["x1"] = b.x0
                    bounds[-1]["thick"] |= w > 1.5
                else:
                    bounds.append({"x0": b.x0, "x1": b.x0, "thick": w > 1.5})
            rdots = [d for d in sym["rdot"] if top < d.yc < bot]
            for bd in bounds:
                bd["dots_left"] = any(0 < bd["x0"] - d.x1 < 4 for d in rdots)
                bd["dots_right"] = any(0 < d.x0 - bd["x1"] < 4 for d in rdots)
            heads_here = [(b, sm, False) for b, sm in sym["head"] if _in_staff_band(staff, b, 25, 25)]
            heads_here += [(b, sm, True) for b, sm in sym["hollow"] if _in_staff_band(staff, b, 25, 25)]
            first_x = min((b.x0 for b, _, _ in heads_here), default=staff.x0)
            rests_here = [r for r in sym["rest"] if _in_staff_band(staff, r[0], 5, 5)]
            first_x = min([first_x] + [r[0].x0 for r in rests_here])
            # bar regions: from the first event (or a start-repeat) to each barline
            edges = [b for b in bounds if b["x0"] > first_x - 8]
            start_rep = [b for b in bounds if b["x0"] <= first_x - 8 and b["dots_right"]]
            left = first_x - 6
            stems = [s for s in sym["stem"] if _in_staff_band(staff, s, 30, 30)]
            dots = [d for d in sym["dot"] if _in_staff_band(staff, d, 25, 25)]
            curves = [c for c in sym["curve"] if _in_staff_band(staff, c, 3 * sp, 3 * sp)]
            used_dots: set = set()
            pending_start_repeat = bool(start_rep)
            for e in edges:
                if e["x0"] - left < 6:
                    left = e["x1"]
                    if e["dots_right"]:
                        pending_start_repeat = True
                    continue
                bar = Bar(len(bars) + 1, page_no, system_no, left, e["x0"], top, sp)
                bar.start_repeat = pending_start_repeat
                bar.end_repeat = e["dots_left"]
                pending_start_repeat = e["dots_right"]
                inbar = lambda b: left - 1 <= b.xc <= e["x0"] + 1
                alters = dict(key_alters)
                local: dict[tuple[str, int], int] = {}
                events = []
                for b, small, hollow in heads_here:
                    if not inbar(b):
                        continue
                    step = diatonic_step(staff, b.yc)
                    letter, octv = step_to_letter_octave(step)
                    for ab, a in sym["acc"]:
                        if 0 < b.x0 - ab.x1 < 3.5 and abs(ab.yc - b.yc) < sp and ab.x0 > staff.x0 + 40:
                            local[(letter, octv)] = a
                    alter = local.get((letter, octv), alters.get(letter, 0))
                    stem, up = stem_for(b, stems)
                    if hollow:
                        base = 2.0 if stem else 4.0
                    elif stem is None:
                        base = 1.0
                        bar.problems.append(f"head at x={b.x0:.1f} has no stem")
                    else:
                        nb = beams_on(stem, up, sym["beam"], sp) or flags_on(stem, up, sym["flag"])
                        base = 1.0 / (2 ** nb)
                    dur = base * (1.5 if is_dotted(b, dots, used_dots) else 1.0)
                    events.append(("n", b, small, dur, letter_midi(letter, octv, alter)))
                for b, kind, small in rests_here:
                    if not inbar(b):
                        continue
                    if kind == "bar":
                        mid = staff.lines[2]
                        dur = 2.0 if abs(b.y1 - mid) < 1.5 else "whole"
                    else:
                        dur = kind
                    if dur != "whole" and is_dotted(b, dots, used_dots):
                        dur *= 1.5
                    events.append(("r", b, small, dur, None))
                events.sort(key=lambda ev: ev[1].x0)
                events = _apply_tuplets(events, [t for t in sym["tuplet"] if inbar(t[0]) and _in_staff_band(staff, t[0], 25, 5)], sym["beam"])
                _assign_voices(bar, events, meter_hint=4.0)
                _mark_ties(bar.notes, curves, bars[-1] if bars and bars[-1].system == system_no else None)
                bars.append(bar)
                left = e["x1"]
            # a tie leaving the system: curve after the last head of the system
            sys_bars = [b for b in bars if b.system == system_no]
            if sys_bars:
                last = [n for n in sys_bars[-1].notes if n.voice == 1 and n.midi is not None]
                if last:
                    for cv in curves:
                        if cv.x0 > last[-1].x - 2 and cv.x1 > sys_bars[-1].x1 - 2 and _in_staff_band(staff, cv, 10, 10):
                            last[-1].tie_out = True
            # text in this staff's band
            # time signatures on this staff: a digit above the middle line over one below
            digits = [(b, v) for b, v in sym["tsig"] if top - 2 <= b.yc <= bot + 2]
            for b, v in digits:
                if b.yc < staff.lines[2]:
                    under = [v2 for b2, v2 in digits if abs(b2.x0 - b.x0) < 3 and b2.yc > staff.lines[2]]
                    if under:
                        tsigs.append((system_no, b.x0, v * 4.0 / under[0]))
            for w in owners.get(id(staff), []):
                words_all.append((system_no, staff, w))
            lo = top - 10
            nxt = [st.lines[0] for st in staves if st.lines[0] > bot]
            hi = min(nxt) if nxt else bot + 80
            for hb in sym["hyphen"]:
                if bot < hb.yc < hi and hb.yc > lo:
                    hyphens_all.append((system_no, hb))
            system_no += 1
    for a, c in zip(bars, bars[1:]):
        la = [n for n in a.notes if n.voice == 1]
        fc = [n for n in c.notes if n.voice == 1]
        if c.system != a.system and la and fc and la[-1].tie_out and la[-1].midi == fc[0].midi:
            fc[0].tied_from_prev = True
    # each bar's metre: the last time signature at or before its start
    tsigs.sort()
    for bar in bars:
        cur = [m for sysno, x, m in tsigs if sysno < bar.system or (sysno == bar.system and x <= bar.x0 + 6)]
        if cur:
            bar.__dict__["ts"] = cur[-1]
    _attach_text(bars, words_all, hyphens_all)
    _fix_whole_rests(bars)
    _rows_follow_cue_voice(bars)
    for bar in bars:
        for n in bar.notes:
            if n.tied_from_prev and 0 in n.syllables:
                bar.problems.append(f"syllable {n.syllables} sits on a tied note")
    return {
        "source": str(pdf),
        "key_fifths": key_fifths,
        "bars": [asdict(b) for b in bars],
    }


def _is_tie(cv: Box, a: Note, c: Note) -> bool:
    """A curve starting at a and ending at c (closer to each than to the other)."""
    if not (a.x - 3 <= cv.x0 and cv.x1 <= c.x + 3):
        return False
    return abs(cv.x0 - a.x) < abs(cv.x0 - c.x) and abs(cv.x1 - c.x) < abs(cv.x1 - a.x)


def _mark_ties(notes: list[Note], curves: list[Box], prev_bar: "Bar | None") -> None:
    for v in (1, 2):
        vn = [n for n in notes if n.voice == v and n.midi is not None]
        for a, c in zip(vn, vn[1:]):
            if a.midi == c.midi and any(_is_tie(cv, a, c) for cv in curves):
                c.tied_from_prev = True
        if prev_bar and vn:
            first = [n for n in notes if n.voice == v][0]
            pv = [n for n in prev_bar.notes if n.voice == v]
            if first.midi is not None and pv and pv[-1].midi == first.midi:
                if any(_is_tie(cv, pv[-1], first) for cv in curves):
                    first.tied_from_prev = True


def _apply_tuplets(events, digits, beams):
    """A "2" over a beam group in compound time is a duplet: two notes in the
    time of three, so each lasts 3/2 of its written value ("3": 2/3)."""
    if not digits:
        return events
    out = list(events)
    for d, n in digits:
        under = [bm[0] for bm in beams if bm[0].x0 - 1 <= d.xc <= bm[0].x1 + 1 and bm[0].y0 > d.y0]
        if not under:
            continue
        beam = min(under, key=lambda bx: bx.y0 - d.y1)
        factor = {2: 1.5, 3: 2 / 3}[n]
        for i, (kind, b, small, dur, midi) in enumerate(out):
            if kind == "n" and beam.x0 - 7 <= b.xc <= beam.x1 + 1 and dur != "whole":
                out[i] = (kind, b, small, dur * factor, midi)
    return out


def _durs(evs) -> float:
    return sum(4.0 if ev[3] == "whole" else ev[3] for ev in evs)


def _assign_voices(bar: Bar, events, meter_hint: float) -> None:
    """Full-size notes are the melody; cue-size notes are either a second
    verse's alternative rhythm (when the full-size notes already fill the
    bar) or the melody itself where the full-size notes leave a gap — an
    instrumental line, or a pickup only some verses sing."""
    full = [ev for ev in events if not ev[2]]
    small = [ev for ev in events if ev[2]]
    if small and abs(_durs(full) - meter_hint) > 1e-6:
        # cue notes fill the gaps: melody is everything, except a cue note
        # stacked on a full-size one at the same x
        v1 = [ev for ev in events if not ev[2] or all(abs(ev[1].x0 - f[1].x0) > 1.5 for f in full)]
        v2 = []
    else:
        v1, v2 = full, small
    t = 0.0
    onset_at_x = []
    for kind, b, sm, dur, midi in v1:
        dur = 4.0 if dur == "whole" else dur
        bar.notes.append(Note(1, t, dur, midi, b.xc))
        onset_at_x.append((b.xc, t))
        t += dur
    t = 0.0
    for kind, b, sm, dur, midi in v2:
        dur = 4.0 if dur == "whole" else dur
        # a cue note under a melody note starts with it
        for x, on in onset_at_x:
            if abs(x - b.xc) < 2.0:
                t = on
        bar.notes.append(Note(2, t, dur, midi, b.xc))
        t += dur


def _merge_chord_spans(items):
    """A chord like A/C# arrives as text, a music-font sharp, and maybe more
    text ("F", "#", "m7"). Join spans that touch and overlap vertically."""
    out = []
    for staff, w in sorted(items, key=lambda it: it[1]["box"].x0):
        joined = False
        for i, (ps, pw) in enumerate(out):
            pb, wb = pw["box"], w["box"]
            music = re.match(r"[A-G]", pw["text"]) and "Bold" not in pw["font"]
            if 0 <= wb.x0 - pb.x1 < 0.8 and wb.y0 < pb.y1 and pb.y0 < wb.y1 and music:
                pw = dict(pw)
                pw["text"] = pw["text"] + w["text"]
                if "Leipzig" in pw["font"]:
                    pw["font"] = w["font"]
                # keep the text span's box, not the music font's tall one
                tb = pb if "Leipzig" not in pw["font"] else wb
                pw["box"] = Box(pb.x0, tb.y0, wb.x1, tb.y1)
                out[i] = (ps, pw)
                joined = True
                break
        if not joined:
            out.append((staff, w))
    return out


def _attach_text(bars: list[Bar], words_all, hyphens_all) -> None:
    by_sys: dict[int, list] = {}
    for sysno, staff, w in words_all:
        by_sys.setdefault(sysno, []).append((staff, w))
    for sysno, items in by_sys.items():
        sb = [b for b in bars if b.system == sysno]
        if not sb:
            continue
        staff = items[0][0]
        top, bot = staff.lines[0], staff.lines[4]
        lyric_ys = sorted({round(w["box"].y0) for _, w in items if w["box"].y0 > bot + 2 and "Times" in w["font"] and "Bold" not in w["font"] and "Italic" not in w["font"]})
        rows: list[float] = []
        for y in lyric_ys:
            if not rows or y - rows[-1] > 5:
                rows.append(y)
        for _, w in items:
            t, b, font = w["text"], w["box"], w["font"]

            def bar_at(x):
                for bar in sb:
                    if bar.x0 - 2 <= x <= bar.x1 + 2:
                        return bar
                return sb[0] if x < sb[0].x0 else sb[-1]

            if t in ("<segno>", "<coda>"):
                bar = bar_at(b.x0 + 2)
                if t == "<segno>":
                    bar.segno = True
                else:
                    bar.__dict__["coda"] = True
                continue
            if b.y1 < top:
                if "Bold" in font and t.isupper():
                    bar_at(b.x0 + 5).section = (bar_at(b.x0 + 5).section or "") + t
                elif "Italic" in font and t.startswith("D.S"):
                    bar_at(b.x0).ds = True
                elif "Italic" in font and "to Coda" in t:
                    bar_at(b.x1).__dict__["to_coda"] = True
                elif re.fullmatch(r"\d(,\d)*\.?", t) and "Italic" not in font and b.y1 < top - 2:
                    bar_at(b.x0 + 2).voltas = [int(v) for v in t.strip(".").split(",")]
                elif CHORD_RE.match(t.replace("♯", "#").replace("♭", "b")) and "Italic" not in font:
                    bar = bar_at(b.x0)
                    v1 = [n for n in bar.notes if n.voice == 1]
                    near = min(v1, key=lambda n: abs(n.x - (b.x0 + 3)), default=None)
                    bar.chords.append({"name": t.replace("♯", "#").replace("♭", "b"), "beat": near.onset if near else 0.0, "x": b.x0})
            elif b.y0 > bot + 2 and rows and "Times" in font and "Italic" not in font and "Bold" not in font:
                row = min(range(len(rows)), key=lambda i: abs(rows[i] - round(b.y0)))
                bar = bar_at(b.xc)
                if re.fullmatch(r"\(?\d+\.", t):  # "1." / "(2." labels the verse on this row
                    bar.__dict__.setdefault("verse_labels", {})[row] = int(t.strip("(.")[:3])
                    continue
                small_font = w["size"] < 9
                # a syllable cannot start on a tied continuation; later lyric
                # rows can, because another verse may re-sing a held note
                pool = [n for n in bar.notes if n.midi is not None and (row >= 1 or not n.tied_from_prev)]
                v2 = [n for n in pool if n.voice == 2]
                cands = [n for n in pool if n.voice == 1]
                # the second lyric row follows the cue voice where one exists
                if row >= 1 and v2:
                    near2 = min(v2, key=lambda n: abs(n.x - b.xc))
                    near1 = min(cands, key=lambda n: abs(n.x - b.xc), default=None)
                    if near1 is None or abs(near2.x - b.xc) <= abs(near1.x - b.xc) + 0.5:
                        cands = v2
                if not cands:
                    bar.problems.append(f"syllable {t!r} has no note")
                    continue
                n = min(cands, key=lambda n: abs(n.x - b.xc))
                if row in n.syllables:
                    bar.problems.append(f"two syllables on one note: {n.syllables[row]!r} + {t!r}")
                n.syllables[row] = (n.syllables.get(row, "") + " " + t).strip()
                # a hyphen just right of this syllable, on its row, joins it to the next
                n.hyphen_after[row] = any(
                    hs == sysno and 0 <= hb.x0 - b.x1 < 25 and b.y0 - 1 <= hb.yc <= b.y1 + 1
                    and not any(0 < w2["box"].x0 - b.x1 < hb.x0 - b.x1 and abs(w2["box"].y0 - b.y0) < 4 for _, w2 in items)
                    for hs, hb in hyphens_all
                )
            elif "Bold" in font and t.isupper() and b.y0 < top:
                pass


def _rows_follow_cue_voice(bars: list[Bar]) -> None:
    """A later lyric row sings the cue voice where one is drawn. Where a cue
    note and a main-voice note start together, a row-2 syllable may have
    landed on the main-voice one; move it across."""
    for bar in bars:
        v2 = [n for n in bar.notes if n.voice == 2 and n.midi is not None]
        if not v2:
            continue
        for n in bar.notes:
            if n.voice != 1:
                continue
            for row in [r for r in n.syllables if r >= 1]:
                twin = next((m for m in v2 if abs(m.onset - n.onset) < 1e-6), None)
                if twin is not None and row not in twin.syllables:
                    twin.syllables[row] = n.syllables.pop(row)
                    twin.hyphen_after[row] = n.hyphen_after.pop(row, False)


def _fix_whole_rests(bars: list[Bar]) -> None:
    sums = []
    for bar in bars:
        v1 = [n for n in bar.notes if n.voice == 1]
        sums.append(sum(n.dur for n in v1))
    default = statistics.mode([round(s, 3) for s in sums if s]) if sums else 4.0
    for bar, s in zip(bars, sums):
        # the printed time signature where there is one (mixed metre), else
        # the song's commonest bar length
        meter = bar.__dict__.get("ts", default)
        bar.__dict__["meter"] = meter
        v1 = [n for n in bar.notes if n.voice == 1]
        if len(v1) == 1 and v1[0].midi is None:
            v1[0].dur = meter
        elif bar is bars[0] and 0 < s < meter:
            # a pickup: shift it so it ends on the downbeat
            bar.__dict__["pickup"] = s
            for n in bar.notes:
                n.onset += meter - s
        elif abs(s - meter) > 1e-6:
            bar.problems.append(f"voice 1 sums to {s} not {meter}")
        v2 = [n for n in bar.notes if n.voice == 2]
        s2 = sum(n.dur for n in v2)
        if v2 and s2 - meter > 1e-6:
            bar.problems.append(f"voice 2 overruns: {s2} > {meter}")


NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def show(score: dict, bars: list[int] | None = None) -> str:
    out = []
    for bar in score["bars"]:
        if bars and bar["number"] not in bars:
            continue
        head = f"bar {bar['number']:2d} p{bar['page']+1} s{bar['system']}"
        flags = []
        if bar["section"]:
            flags.append(f"[{bar['section']}]")
        if bar["start_repeat"]:
            flags.append("|:")
        if bar["end_repeat"]:
            flags.append(":|")
        if bar["voltas"]:
            flags.append("volta " + ",".join(map(str, bar["voltas"])))
        if bar["ds"]:
            flags.append("D.S.")
        chords = " ".join(f"{c['name']}@{c['beat']:g}" for c in bar["chords"])
        out.append(f"{head} {' '.join(flags)}  chords: {chords}")
        for v in (1, 2):
            notes = [n for n in bar["notes"] if n["voice"] == v]
            if not notes:
                continue
            parts = []
            for n in notes:
                p = "r" if n["midi"] is None else NAMES[n["midi"] % 12] + str(n["midi"] // 12 - 1)
                tie = "~" if n["tied_from_prev"] else ""
                syl = "/".join(n["syllables"][k] for k in sorted(n["syllables"], key=int))
                parts.append(f"{tie}{p}:{n['dur']:g}" + (f"({syl})" if syl else ""))
            out.append(f"   v{v}: " + " ".join(parts))
        for pr in bar["problems"]:
            out.append(f"   !! {pr}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--bars", type=str, default="")
    args = ap.parse_args()
    score = extract(args.pdf.expanduser())
    if args.out:
        args.out.write_text(json.dumps(score, indent=1))
    if args.show or not args.out:
        sel = [int(x) for x in args.bars.split(",") if x] or None
        print(show(score, sel))
    bad = [b for b in score["bars"] if b["problems"]]
    print(f"\n{len(score['bars'])} bars, key {score['key_fifths']:+d} fifths, {len(bad)} with problems", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
