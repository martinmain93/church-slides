"""Parse chord charts into a key-agnostic, lyric-aligned chord map.

Spike, 2026-09-08. Reads the user's own PDF chord sheets (`~/Documents/Song
sheets`) with `pdftotext -layout`, which preserves the character columns that
make a chord-over-lyric chart machine-readable in the first place: the column a
chord sits in says *which syllable* it lands on.

That column is the whole point. The chart is not just "this song uses C F Am G";
it is "the chord changes to F on the word 'mountain'", which is positional
information at a far finer grain than a slide boundary.

Chords are stored as semitone offsets from the song's own key, so a band playing
in a different key produces the same map — the key-invariance requirement, met
by representation rather than by search.

    uv run --project sidecar python research/spikes/spike_chord_charts.py --verify
    uv run --project sidecar python research/spikes/spike_chord_charts.py --goldens
"""
from __future__ import annotations

import argparse
import difflib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SHEETS = Path("~/Documents/Song sheets").expanduser()

PITCH = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
         "F": 5, "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9,
         "A#": 10, "BB": 10, "B": 11, "CB": 11}

# A chord token: root, optional quality, optional bass. Deliberately generous
# about quality (ma7, sus4, add9, 5, 2, dim, +) because charts are inconsistent,
# and strict about the root, which is what the semitone map needs.
CHORD_RE = re.compile(
    r"^([A-G][#b]?)"
    r"((?:maj|Maj|MAJ|ma|M|min|mi|m|dim|aug|sus|add|°|\+|-)?"
    r"(?:\d{1,2})?(?:sus|add|maj|ma|m)?(?:\d{1,2})?)"
    r"(?:/([A-G][#b]?))?$"
)
# Rhythm slashes and bar lines carry no harmony but appear on chord lines.
FILLER = {"/", "|", "//", "///", "////", "(2x)", "(3x)", "(4x)", "x2", "x3",
          "N.C.", "NC", "-", "%"}

SECTION_RE = re.compile(
    r"^\[?\s*((?:pre[\s-]*)?chorus|verse|bridge|intro|outro|tag|refrain|"
    r"instrumental|interlude|ending|vamp|turnaround|coda|hook)\s*"
    r"([0-9]+[a-z]?)?\s*:?\]?$", re.I)

KEY_RE = re.compile(r"\bkey\s*(?:-|:|of)?\s*([A-G][#b]?)(m|min)?\b", re.I)


def _norm(tok: str) -> str:
    return tok.strip(" ,.").upper()


def is_chord(tok: str) -> bool:
    t = tok.strip()
    if not t or t in FILLER:
        return False
    # "B(E)" — an alternate key in parentheses; judge the primary.
    t = re.sub(r"\(.*?\)$", "", t)
    if not t:
        return False
    return bool(CHORD_RE.match(t))


def chord_root(tok: str) -> int | None:
    t = re.sub(r"\(.*?\)$", "", tok.strip())
    m = CHORD_RE.match(t)
    if not m:
        return None
    return PITCH.get(_norm(m.group(1)))


def chord_is_minor(tok: str) -> bool:
    t = re.sub(r"\(.*?\)$", "", tok.strip())
    m = CHORD_RE.match(t)
    if not m:
        return False
    q = m.group(2) or ""
    # "m" is minor, but "ma"/"maj" is major — the negative lookahead has to
    # exclude any following "a", not just the literal "aj". Reading Cma7 as
    # minor is silent and produces a plausible-looking wrong Nashville number.
    return bool(re.match(r"^(?:min|mi|m(?![aA])|-)", q))


def chord_line_score(line: str) -> float:
    """Share of tokens on the line that are chords or rhythm filler."""
    toks = line.split()
    if not toks:
        return 0.0
    good = sum(1 for t in toks if is_chord(t) or t in FILLER)
    return good / len(toks)


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------

def split_columns(lines: list[str]) -> list[list[str]]:
    """Two-column charts are common and destroy line pairing if not split.

    Finds a character column that is blank on nearly every line while both
    sides carry text, which is what a real gutter looks like and what a run of
    short lines does not.
    """
    width = max((len(ln) for ln in lines), default=0)
    if width < 60:
        return [lines]
    body = [ln for ln in lines if ln.strip()]
    if len(body) < 10:
        return [lines]
    best = None
    for c in range(40, width - 15):
        blank = sum(1 for ln in body if len(ln) <= c or ln[max(0, c - 1):c + 2].strip() == "")
        left = sum(1 for ln in body if ln[:c].strip())
        right = sum(1 for ln in body if len(ln) > c and ln[c:].strip())
        if blank / len(body) > 0.985 and right / len(body) > 0.30 and left / len(body) > 0.30:
            score = right / len(body)
            if best is None or score > best[1]:
                best = (c, score)
    if not best:
        return [lines]
    c = best[0]
    return [[ln[:c] for ln in lines], [ln[c:] for ln in lines]]


MAJOR_DEGREE = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}
FLAT_DEGREE = {1: "b2", 3: "b3", 6: "b5", 8: "b6", 10: "b7"}


def nashville(token: str, key: int) -> str | None:
    """Chord token -> Nashville number relative to `key`.

    The Nashville system is the representation the app wants: it is what makes
    a chart key-agnostic, so a band capoed up two frets or a leader who drops a
    song a tone still matches the stored map without any search over keys.
    """
    root = chord_root(token)
    if root is None or key is None:
        return None
    deg = (root - key) % 12
    num = MAJOR_DEGREE.get(deg) or FLAT_DEGREE.get(deg)
    if num is None:
        return None
    m = CHORD_RE.match(re.sub(r"\(.*?\)$", "", token.strip()))
    quality = m.group(2) or "" if m else ""
    bass = m.group(3) if m else None
    if chord_is_minor(token):
        # Strip the minor marker; the "m" is re-attached to the number.
        quality = re.sub(r"^(?:min|mi|m|-)", "", quality)
        num += "m"
    elif quality and not quality[0].isdigit():
        pass
    out = num + quality
    if bass:
        broot = PITCH.get(_norm(bass))
        if broot is not None:
            bdeg = (broot - key) % 12
            bnum = MAJOR_DEGREE.get(bdeg) or FLAT_DEGREE.get(bdeg)
            if bnum:
                out += "/" + bnum
    return out


def attach_to_words(lyric: str, chords: list[tuple[int, str]]) -> list[dict]:
    """Bind each chord to the word — and the character inside it — that it sits over.

    This is the part of a chord sheet that carries positional information, and
    the part a plain chord list throws away: "the 4 chord lands on the second
    syllable of 'mountain'" localises far more tightly than "this section uses
    1 4 6m 5".
    """
    words = [(m.start(), m.group()) for m in re.finditer(r"\S+", lyric)]
    out = []
    for col, tok in chords:
        idx, offset = None, 0
        for i, (start, w) in enumerate(words):
            if start <= col < start + len(w):
                idx, offset = i, col - start
                break
            if col < start:
                # Falls in the gap before this word: it belongs to this word,
                # which is how charts are read when spacing is loose.
                idx, offset = i, 0
                break
        if idx is None and words:
            idx, offset = len(words) - 1, 0
        out.append({"chord": tok, "column": col, "word_index": idx,
                    "char_offset": offset,
                    "word": words[idx][1] if idx is not None else None})
    return out


@dataclass
class ChartLine:
    """One lyric line and the chords sitting above it, by character column.

    `raw` keeps the original leading whitespace. Chord columns are absolute
    positions on the PDF line, so stripping the lyric before matching them
    shifts every chord right by the indent — which silently reports the chord
    over "King" as being over "of", four words later.
    """
    section: str
    raw: str
    chords: list[tuple[int, str]] = field(default_factory=list)

    @property
    def lyric(self) -> str:
        return self.raw.strip()

    def degrees(self, key: int) -> list[tuple[int, int, bool]]:
        """(column, semitones above the key, is_minor) — the key-agnostic form."""
        out = []
        for col, tok in self.chords:
            r = chord_root(tok)
            if r is not None:
                out.append((col, (r - key) % 12, chord_is_minor(tok)))
        return out

    def nashville(self, key: int) -> list[str]:
        return [n for _, t in self.chords if (n := nashville(t, key))]

    def placed(self, key: int) -> list[dict]:
        """Nashville numbers bound to the word each one sits over."""
        out = attach_to_words(self.raw, self.chords)
        for d in out:
            d["nashville"] = nashville(d["chord"], key)
        return out


@dataclass
class Chart:
    title: str
    key: int | None
    key_name: str
    lines: list[ChartLine]
    source: Path

    @property
    def chord_tokens(self) -> list[str]:
        return [t for ln in self.lines for _, t in ln.chords]


def parse_chart(pdf: Path) -> Chart:
    txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                         capture_output=True, text=True).stdout
    raw = txt.splitlines()

    key, key_name = None, ""
    for ln in raw[:12]:
        m = KEY_RE.search(ln)
        if m:
            key_name = m.group(1) + (m.group(2) or "")
            key = PITCH.get(_norm(m.group(1)))
            break

    lines: list[ChartLine] = []
    for col_lines in split_columns(raw):
        section = ""
        pending: tuple[int, list[tuple[int, str]]] | None = None
        for ln in col_lines:
            if not ln.strip():
                pending = None
                continue
            sm = SECTION_RE.match(ln.strip())
            if sm:
                section = " ".join(x for x in sm.groups() if x).title()
                pending = None
                continue
            score = chord_line_score(ln)
            toks = [(m.start(), m.group()) for m in re.finditer(r"\S+", ln)]
            if score >= 0.8:
                # A chord line. Keep its columns for the lyric line beneath.
                pending = (0, [(c, t) for c, t in toks if is_chord(t)])
                continue
            # A lyric line: attach whatever chord line preceded it.
            chords = pending[1] if pending else []
            lines.append(ChartLine(section=section, raw=ln, chords=chords))
            pending = None

    return Chart(title=pdf.stem, key=key, key_name=key_name, lines=lines, source=pdf)


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

def find_sheet(*needles: str) -> Path | None:
    for p in sorted(SHEETS.glob("*.pdf")):
        low = p.name.lower()
        if all(n.lower() in low for n in needles):
            return p
    return None


def verify() -> None:
    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL':4s}  {label:46s} {detail}")

    print("chord tokens")
    good = ["C", "Am", "F#m7", "Bb", "Cma7", "G5", "D/F#", "Asus4", "Ebadd9", "F#4"]
    bad = ["the", "You", "Let", "I", "and", "Verse", "Oh", "me", "A-way", "day"]
    check("recognises real chords", all(is_chord(t) for t in good),
          " ".join(t for t in good if not is_chord(t)) or "all")
    check("rejects lyric words", not any(is_chord(t) for t in bad),
          " ".join(t for t in bad if is_chord(t)) or "none")
    check("roots map to semitones",
          chord_root("C") == 0 and chord_root("Bb") == 10 and chord_root("F#m") == 6,
          f"C={chord_root('C')} Bb={chord_root('Bb')} F#m={chord_root('F#m')}")
    check("minor quality detected",
          chord_is_minor("Am") and chord_is_minor("F#m7") and not chord_is_minor("Cma7"),
          "Am/F#m7 minor, Cma7 major")

    print("\nline classification")
    check("a chord line scores high", chord_line_score("    C       F      C") == 1.0)
    check("a lyric line scores low",
          chord_line_score("Let the light of the morning be the lamp") < 0.3,
          f"{chord_line_score('Let the light of the morning be the lamp'):.2f}")
    # "A" and "I" are chords AND words; a lyric line must not flip on them.
    check("lyric line with A/I in it is still a lyric",
          chord_line_score("I am a friend of God and he calls me") < 0.8,
          f"{chord_line_score('I am a friend of God and he calls me'):.2f}")

    print("\nNashville numbers")
    check("C major scale degrees in C",
          [nashville(t, 0) for t in ("C", "Dm", "Em", "F", "G", "Am")]
          == ["1", "2m", "3m", "4", "5", "6m"],
          " ".join(str(nashville(t, 0)) for t in ("C", "Dm", "Em", "F", "G", "Am")))
    check("same shape transposed to G",
          [nashville(t, 7) for t in ("G", "Am", "Bm", "C", "D", "Em")]
          == ["1", "2m", "3m", "4", "5", "6m"],
          " ".join(str(nashville(t, 7)) for t in ("G", "Am", "Bm", "C", "D", "Em")))
    check("flat degrees and slash bass",
          nashville("Bb", 0) == "b7" and nashville("D/F#", 0) == "2m/b5".replace("2m", "2"),
          f"Bb->{nashville('Bb', 0)}  D/F#->{nashville('D/F#', 0)}")

    print("\nchord placement over the word")
    # Hand-checkable: the chord columns line up with these words by eye.
    raw_chord = "              C                        F               C"
    raw_lyric = "      Let the light of the morning be the lamp where I walk"
    toks = [(m.start(), m.group()) for m in re.finditer(r"\S+", raw_chord)]
    placed = attach_to_words(raw_lyric, toks)
    got = [(d["chord"], d["word"]) for d in placed]
    check("chords bind to the word beneath them",
          got == [("C", "King"), ("F", "mountain"), ("C", "run")], str(got))

    print("\nkey invariance, on the same song in two keys")
    for needles_a, needles_b, name in (
        (("all hail king jesus", "-g"), ("all hail king jesus", "chords-c"),
         "All Hail King Jesus"),
        (("alabaster", "-d"), ("alabaster", "-gb"), "Alabaster"),
    ):
        pa, pb = find_sheet(*needles_a), find_sheet(*needles_b)
        if not (pa and pb):
            print(f"  SKIP  {name}: sheets not found")
            continue
        ca, cb = parse_chart(pa), parse_chart(pb)
        if ca.key is None or cb.key is None:
            print(f"  SKIP  {name}: key not stated ({ca.key_name!r} / {cb.key_name!r})")
            continue
        da = [n for ln in ca.lines for n in ln.nashville(ca.key)]
        db = [n for ln in cb.lines for n in ln.nashville(cb.key)]
        # The two PDFs are different layouts of the same song and do not have
        # the same number of lines, so an index-by-index comparison misaligns
        # them and reports a real match as a failure. Align first.
        agree = difflib.SequenceMatcher(None, da, db).ratio()
        check(f"{name} ({ca.key_name} vs {cb.key_name})", agree > 0.9,
              f"{agree:.0%} of {len(da)}/{len(db)} chords agree in Nashville numbers")

    print()
    print("VERIFY:", "parser behaves as claimed" if ok else "SOMETHING IS WRONG")


def cmd_goldens() -> None:
    """Parse the charts for the seven approved goldens and report coverage."""
    wanted = [
        ("Reckless Love", ("reckless-love",)),
        ("King Of My Heart", ("king of my heart",)),
        ("You Came (Lazarus)", ("you came",)),
        ("Since Your Love", ("since your love",)),
        ("What A Beautiful Name", ("what a beautiful name", "-d")),
        ("I Surrender All", ("i surrender all", "-c")),
        ("Nothing But The Blood", ("nothing but the blood", "plainfield", "-c")),
    ]
    hdr = (f"{'song':26s} {'key':>4s} {'lines':>6s} {'w/chords':>9s} "
           f"{'chords':>7s} {'distinct':>9s} {'sections':>9s}")
    print(hdr)
    print("-" * len(hdr))
    for name, needles in wanted:
        p = find_sheet(*needles)
        if not p:
            print(f"{name:26s} NOT FOUND")
            continue
        c = parse_chart(p)
        withch = sum(1 for ln in c.lines if ln.chords)
        degs = {d for ln in c.lines for _, d, _ in ln.degrees(c.key or 0)}
        secs = sorted({ln.section for ln in c.lines if ln.section})
        print(f"{name:26s} {c.key_name or '?':>4s} {len(c.lines):6d} {withch:9d} "
              f"{len(c.chord_tokens):7d} {len(degs):9d} {len(secs):9d}   "
              f"{','.join(s[:4] for s in secs[:6])}")


# --------------------------------------------------------------------------
# --position : how much positional information does the chord map carry?
# --------------------------------------------------------------------------

GOLDEN_SONGS = [
    ("Reckless Love", ("reckless-love",)),
    ("King Of My Heart", ("king of my heart",)),
    ("You Came (Lazarus)", ("you came",)),
    ("Since Your Love", ("since your love",)),
    ("What A Beautiful Name", ("what a beautiful name", "-d")),
    ("I Surrender All", ("i surrender all", "-c")),
    ("Nothing But The Blood", ("nothing but the blood", "plainfield", "-c")),
]


def chord_stream(chart: Chart) -> list[dict]:
    """The song as one ordered list of placed chords."""
    key = chart.key if chart.key is not None else 0
    out = []
    for li, ln in enumerate(chart.lines):
        for d in ln.placed(key):
            if d["nashville"]:
                out.append({"n": d["nashville"], "section": ln.section,
                            "line": li, "word": d["word"]})
    return out


def cmd_position() -> None:
    """Ambiguity of a k-chord window: how many places in the song look like this?

    This is an upper bound measured on the chart alone, with no audio and no
    detector. If the chart itself does not distinguish one position from
    another, no amount of detection quality can — the same argument the
    shared-openings work made for lyrics.

    Reported both song-wide (could chords tell us which section?) and within a
    section (could chords tell us where in the line we are?), because the ask
    is for corroboration of a slide the words have already chosen.
    """
    print("Candidate positions matching a window of k consecutive chords.")
    print("1.0 means the window is unique; higher means that many places in the")
    print("song look identical. Median over every position in the song.")
    print()
    ks = [1, 2, 3, 4, 6, 8]
    hdr = (f"{'song':24s} {'chords':>7s} {'scope':>10s} " +
           " ".join(f"k={k}".rjust(6) for k in ks))
    print(hdr)
    print("-" * len(hdr))
    agg = {"song": {k: [] for k in ks}, "section": {k: [] for k in ks}}
    for name, needles in GOLDEN_SONGS:
        p = find_sheet(*needles)
        if not p:
            continue
        chart = parse_chart(p)
        st = chord_stream(chart)
        if len(st) < 8:
            continue
        seq = [d["n"] for d in st]
        secs = [d["section"] for d in st]
        rows = {}
        for scope in ("song", "section"):
            meds = []
            for k in ks:
                counts = []
                for i in range(len(seq) - k + 1):
                    win = tuple(seq[i:i + k])
                    if scope == "song":
                        pool = range(len(seq) - k + 1)
                    else:
                        pool = [j for j in range(len(seq) - k + 1)
                                if secs[j] == secs[i]]
                    c = sum(1 for j in pool if tuple(seq[j:j + k]) == win)
                    counts.append(c)
                    agg[scope][k].append(c)
                meds.append(float(sorted(counts)[len(counts) // 2]) if counts else float("nan"))
            rows[scope] = meds
        for scope in ("song", "section"):
            label = name if scope == "song" else ""
            cnt = f"{len(seq):7d}" if scope == "song" else " " * 7
            print(f"{label:24s} {cnt} {scope:>10s} " +
                  " ".join(f"{v:6.1f}" for v in rows[scope]))
    print("-" * len(hdr))
    for scope in ("song", "section"):
        import statistics
        print(f"{'POOLED median':24s} {'':7s} {scope:>10s} " +
              " ".join(f"{statistics.median(agg[scope][k]):6.1f}" for k in ks))
    print(f"{'unique share, song-wide':24s} {'':7s} {'':>10s} " +
          " ".join(f"{sum(1 for c in agg['song'][k] if c == 1) / len(agg['song'][k]):6.1%}"
                   for k in ks))
    print(f"{'unique within section':24s} {'':7s} {'':>10s} " +
          " ".join(f"{sum(1 for c in agg['section'][k] if c == 1) / len(agg['section'][k]):6.1%}"
                   for k in ks))


# --------------------------------------------------------------------------
# --audio : can the detector recover the chart's chords from our recordings?
# --------------------------------------------------------------------------

GOLDEN_MAP = [
    ("rec_20260818-213104_293", "Reckless Love", ("reckless-love",)),
    ("rec_20260818-213617_974", "King Of My Heart", ("king of my heart",)),
    ("rec_20260818-213825_312", "You Came (Lazarus)", ("you came",)),
    ("rec_20260818-214054_974", "Since Your Love", ("since your love",)),
    ("rec_20260818-214759_057", "What A Beautiful Name", ("what a beautiful name", "-d")),
    ("rec_20260818-215943_266", "I Surrender All", ("i surrender all", "-c")),
    ("rec_20260903-115044_425", "Nothing But The Blood",
     ("nothing but the blood", "plainfield", "-c")),
]


def cmd_audio() -> None:
    """Match the decoded chord histogram against the chart, over all 12 keys.

    The band's key is not the chart's key — leaders transpose freely, which is
    exactly why the map is stored in Nashville numbers. So the test is whether
    *some* rotation makes the audio look like the chart, and whether that
    rotation stands out from the other eleven. A detector that is producing
    noise will score its best rotation no better than its worst.
    """
    import numpy as np

    from spike_chord_downbeat import (CHORD_NAMES, RECORDINGS, chord_sequence,
                                      chromagram, load_wav)

    print("Chord histogram from audio, matched to the chart over all 12 rotations.")
    print("`margin` is the best rotation's score minus the mean of the other 11,")
    print("in standard deviations of those eleven. Above ~2 means the key is")
    print("recoverable from the audio; near 0 means the decoder is producing noise.")
    print()
    hdr = (f"{'recording':26s} {'song':22s} {'chart':>5s} {'implied':>8s} "
           f"{'margin':>7s} {'corr':>6s}")
    print(hdr)
    print("-" * len(hdr))
    margins = []
    for rid, name, needles in GOLDEN_MAP:
        p = find_sheet(*needles)
        wav = RECORDINGS / rid / "audio.wav"
        if not p or not wav.exists():
            continue
        chart = parse_chart(p)
        if chart.key is None:
            print(f"{rid:26s} {name:22s} {'?':>5s}   chart key not stated")
            continue
        # Expected: how long each Nashville degree is held, by chord count.
        want = np.zeros(24)
        for ln in chart.lines:
            for _, deg, minor in ln.degrees(chart.key):
                want[deg * 2 + (1 if minor else 0)] += 1
        if want.sum() == 0:
            continue
        want = want / want.sum()

        audio = load_wav(wav)
        ch, _ = chromagram(audio)
        path = chord_sequence(ch)
        got = np.zeros(24)
        for idx in path:
            nm = CHORD_NAMES[int(idx)]
            root = PITCH[nm.rstrip("m").upper()]
            got[root * 2 + (1 if nm.endswith("m") else 0)] += 1
        got = got / got.sum()

        scores = []
        for r in range(12):
            rot = np.zeros(24)
            for d in range(12):
                for q in range(2):
                    rot[((d + r) % 12) * 2 + q] = want[d * 2 + q]
            scores.append(float(np.dot(rot, got) /
                                (np.linalg.norm(rot) * np.linalg.norm(got) + 1e-9)))
        scores = np.array(scores)
        best = int(np.argmax(scores))
        others = np.delete(scores, best)
        margin = (scores[best] - others.mean()) / (others.std() + 1e-9)
        margins.append(margin)
        implied = PITCH_NAMES_LOCAL[(chart.key + best) % 12]
        print(f"{rid:26s} {name:22s} {chart.key_name:>5s} {implied:>8s} "
              f"{margin:7.2f} {scores[best]:6.3f}")
    print("-" * len(hdr))
    if margins:
        print(f"mean margin {np.mean(margins):.2f} sd; "
              f"recordings above 2 sd: {sum(1 for m in margins if m > 2)}/{len(margins)}")


PITCH_NAMES_LOCAL = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--goldens", action="store_true")
    ap.add_argument("--position", action="store_true")
    ap.add_argument("--audio", action="store_true")
    a = ap.parse_args()
    if a.verify:
        verify()
    if a.goldens:
        cmd_goldens()
    if a.position:
        cmd_position()
    if a.audio:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        cmd_audio()
