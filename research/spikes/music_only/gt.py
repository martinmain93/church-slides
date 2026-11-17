"""Ground truth: where in the lead sheet the band is, at every instant.

Built from the golden's slide cues and the stored Whisper transcript, and
used **only for scoring** — no matcher in this spike reads it. Each cue says
which slide is up; the transcript words inside that cue are aligned to the
slide's words, and each matched word is an anchor: (audio time, played
position of that word in the sheet). Positions between anchors are linear in
time; a cue with no matched word has no position, only a slide.

Whisper's word starts are stitched to the previous word's end, so they run
early by roughly 0.25-0.75 s (Plan/CHORD_DOWNBEAT.md, audit). Anchors are
therefore approximate to about half a second, which is why everything that
scores against this uses windows rather than instants.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

import score as sc


@dataclass
class Cue:
    t0: float
    t1: float
    slide: int
    span: sc.Span | None
    anchors: list[tuple[float, float]]  # (audio seconds, played quarter notes)


OTHER_SONG = -2  # a cue for a different setlist item (a medley's other song)


def load_cues(rec_dir: Path, item: int = 0) -> list[tuple[float, int]]:
    g = rec_dir / "golden.json"
    data = json.loads(g.read_text()) if g.exists() else json.loads((rec_dir / "recording.json").read_text())
    return [(c["t_seconds"], c["slide_idx"] if c.get("item_idx", 0) == item else OTHER_SONG) for c in data["ground_truth"]]


def load_words(rec_dir: Path) -> list[dict]:
    return json.loads((rec_dir / "transcript.json").read_text())["words"]


def prune_spans(s: sc.Score, spans: dict[int, list[sc.Span]]) -> dict[int, list[sc.Span]]:
    """Drop a span that begins inside another slide's span when the slide has
    a span that does not: a chorus line sung again
    straddling two other slides is not where that slide is."""
    out = {}
    for si, sps in spans.items():
        def inside(sp):
            return any(o.w0 < sp.w0 <= o.w1 for sj, os_ in spans.items() if sj != si for o in os_)
        clean = [sp for sp in sps if not inside(sp)]
        out[si] = clean or sps
    return out


def build(s: sc.Score, slides: list[list[str]], rec_dir: Path, duration: float, item: int | None = None) -> list[Cue]:
    import stage1

    spans = stage1.song_spans(s, rec_dir, slides)
    cues = load_cues(rec_dir, stage1.song_item(s, rec_dir) if item is None else item)
    words = load_words(rec_dir)
    out: list[Cue] = []
    prev_end = -1.0
    for i, (t0, slide) in enumerate(cues):
        t1 = cues[i + 1][0] if i + 1 < len(cues) else duration
        sps = spans.get(slide, []) if slide >= 0 else []
        if not sps or not slides[slide]:
            out.append(Cue(t0, t1, slide, None, []))
            continue
        # the next occurrence after the last one used, wrapping round: bands
        # do not follow the sheet's order, but they mostly move forward
        after = [sp for sp in sps if sp.start > prev_end]
        span = min(after or sps, key=lambda sp: sp.start)
        prev_end = span.start
        # transcript words in (or just around) the cue, aligned to the sheet's words
        tw = [w for w in words if t0 - 1.5 <= w["start"] < t1 + 0.5]
        sheet = s.words[span.w0 : span.w1 + 1]
        a = [sc.norm(w["word"]) for w in tw]
        b = [w.text for w in sheet]
        anchors = []
        for blk in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
            for k in range(blk.size):
                anchors.append((tw[blk.a + k]["start"], sheet[blk.b + k].onset))
        # one word per sheet position, monotone in both
        anchors.sort()
        mono = []
        for t, p in anchors:
            if not mono or (p > mono[-1][1] and t > mono[-1][0]):
                mono.append((t, p))
        out.append(Cue(t0, t1, slide, span, mono))
    return out


def song_rate(cues: list[Cue]) -> float:
    """Median tempo implied by the transcript, quarter notes per second."""
    rates = []
    for c in cues:
        if len(c.anchors) >= 4:
            (t0, p0), (t1, p1) = c.anchors[0], c.anchors[-1]
            if t1 - t0 > 2:
                rates.append((p1 - p0) / (t1 - t0))
    return float(np.median(rates)) if rates else 1.0


def fit(c: Cue, rate: float, lead: float, slack: float = 0.15) -> tuple[float, float] | None:
    """(intercept, slope) of played position against audio time for one cue.

    Whisper sometimes stamps a sung line in a burst, which makes the
    implied tempo absurd (one bridge line reads 132 bpm). Use the cue's own
    slope only when it is within `slack` of the song's tempo; otherwise keep
    the song's tempo and take the anchors' median offset."""
    if c.span is None:
        return None
    if len(c.anchors) >= 3:
        at = np.array([a[0] for a in c.anchors])
        ap = np.array([a[1] for a in c.anchors])
        slope, icpt = np.polyfit(at, ap, 1)
        if abs(slope / rate - 1) <= slack:
            return float(icpt), float(slope)
        return float(np.median(ap - rate * at)), rate
    if c.anchors:
        at = np.array([a[0] for a in c.anchors])
        ap = np.array([a[1] for a in c.anchors])
        return float(np.median(ap - rate * at)), rate
    # nothing transcribed: the slide's first word sung `lead` after its cue
    return c.span.start - rate * (c.t0 + lead), rate


def timeline(cues: list[Cue], fps: float = 10.0, duration: float | None = None, s: "sc.Score | None" = None) -> dict[str, np.ndarray]:
    """GT sampled at `fps`: slide, and played position (NaN where unknown).

    Position runs from the slide's first word to the word after its last,
    and is NaN in instrumental cues and before the first word of a cue."""
    dur = duration or cues[-1].t1
    t = np.arange(0, dur, 1.0 / fps)
    slide = np.full(len(t), -1, np.int32)
    pos = np.full(len(t), np.nan)
    rate = song_rate(cues)
    leads = [c.anchors[0][0] - c.t0 for c in cues if c.anchors and c.anchors[0][1] == c.span.start]
    lead = float(np.median(leads)) if leads else 0.0
    for c in cues:
        m = (t >= c.t0) & (t < c.t1)
        slide[m] = c.slide
        f = fit(c, rate, lead)
        if f is None:
            continue
        p = f[0] + f[1] * t[m]
        hi = sc.span_end(s, c.span) if s is not None else c.span.end + 2.0
        ok = (p >= c.span.start) & (p < hi)
        vals = np.where(ok, p, np.nan)
        pos[m] = vals
    return {"t": t, "slide": slide, "pos": pos, "rate": np.array(rate), "lead": np.array(lead)}
