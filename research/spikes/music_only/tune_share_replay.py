"""Ceiling of learning a song's tunes *during* the service, without a lead sheet.

The follower starts on the library slides alone (no rhythm, no chords). A
look-back a few seconds behind live hears a slide sung; from then on the
follower's reference carries that slide's music (here: the lead sheet's, the
ideal of what the look-back could record) — and, with tune sharing, so does
every other slide sung to the same tune (`tune_groups`), before the band
reaches them. The live follower's reference is rebuilt each time something
is learned, its belief carried across by each slide's share.

    TUNE=melody|bars|naive|none|full DELAY=5 uv run --project sidecar python \\
        research/spikes/music_only/error_buckets.py   (with DECIDE=tune_share_replay)

* `none`   library slides only, never updated (the shipped no-sheet follower)
* `naive`  a slide's own music once it has been heard (no sharing)
* `bars`   ... and every slide on the same printed bars (hymn verses, chorus copies)
* `melody` ... and every slide with the same printed melody
* `full`   the lead sheet's music from the start (no roadmap: library order)

A slide counts as heard `DELAY` seconds after the band left it (golden), the
look-back's lag. Order stays library order in every arm: only music and rhythm
are learned, so the comparison isolates them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import music_replay as M  # noqa: E402
from music_replay import d, identical_slide_groups, load_recording, mr, online, spans_for  # noqa: E402
from tune_groups import tune_groups  # noqa: E402

from auto_presenter.eval.types import Decision  # noqa: E402
from auto_presenter.follower import score as fsc  # noqa: E402
from auto_presenter.follower.online import OnlineFollower  # noqa: E402
from auto_presenter.follower.reference import SegmentReference, sheet_spec  # noqa: E402
from auto_presenter.follower.spec import ReferenceSpec, library_spec  # noqa: E402

TUNE = os.environ.get("TUNE", "melody")
#: Where learned music comes from: "sheet" (the ideal) or "perf" (the
#: look-back's confident passes over the performance itself).
LEARN = os.environ.get("LEARN", "sheet")
#: The look-back's offline chords: "stems" (BTC on Demucs' `other` stem) or "mix".
CHORD_SRC = os.environ.get("CHORD_SRC", "stems")
DELAY = float(os.environ.get("DELAY", "5"))
#: The follower's fastest tempo (`SegmentReference` default 130 bpm).
BPM = (40.0, float(os.environ.get("BPM_MAX", "130")))
FPS = online.FPS
if os.environ.get("CHORDLESS_AT_MEAN"):
    import auto_presenter.follower.reference as _R0

    _R0.CHORDLESS_AT_MEAN = os.environ["CHORDLESS_AT_MEAN"] == "1"
if os.environ.get("CHORDLESS_Q"):
    import auto_presenter.follower.reference as _R1

    _R1.CHORDLESS_QUANTILE = float(os.environ["CHORDLESS_Q"])
if os.environ.get("PRIOR_COUNT"):
    # how many observed moves the library order counts as (reference.LEARNED_PRIOR_COUNT)
    import auto_presenter.follower.reference as _R

    _R.LEARNED_PRIOR_COUNT = float(os.environ["PRIOR_COUNT"])


#: "library": learned segments rescaled (one factor per song) so their cells
#: per second match the library slides' around them — one tempo state has to
#: fit both. "": as learned.
SCALE = os.environ.get("SCALE", "library")


def rescale(g, f: float):
    """A segment stretched by `f`: its cells, word places and chords."""
    from auto_presenter.follower.spec import SegmentSpec

    cells = max(len(g.words) + 1, int(round(g.cells * f)))
    src = np.minimum((np.arange(cells) / f).astype(int), g.cells - 1)
    offs = np.maximum.accumulate([min(cells - 1, int(round(o * f))) for _w, o in g.words])
    return SegmentSpec(slide=g.slide, cells=cells, words=[(w, int(o)) for (w, _o), o in zip(g.words, offs)],
                       midi=g.midi[src].copy(), chord=g.chord[src].copy())


#: Learning the band's order in service: "oracle" (every move of the golden,
#: DELAY s after it), "lookback" (moves between the look-back's sure passes),
#: or "" (library order only).
ORDER = os.environ.get("ORDER", "")


def oracle_order(rec, item: int) -> list[tuple[float, list]]:
    """[(time, learned sequences)]: each band move a -> b (blanks skipped)
    known DELAY s after the band reached b."""
    gt = [g for g in sorted(rec.ground_truth, key=lambda g: g.t_seconds) if g.item_idx == item]
    out, seqs, prev = [], [], None
    for g in gt:
        if g.slide_idx == 0:
            continue
        if prev is not None and prev != g.slide_idx:
            seqs = seqs + [([prev, g.slide_idx], 1.0)]
            out.append((g.t_seconds + DELAY, list(seqs)))
        prev = g.slide_idx
    return out


def merge(music: list, order: list) -> list[tuple[float, dict, list]]:
    """Two schedules into one: [(time, known segments, learned sequences)]."""
    events = sorted([(t, 0, k) for t, k in music] + [(t, 1, q) for t, q in order], key=lambda x: (x[0], x[1]))
    out, known, seqs = [], {}, []
    for t, kind, v in events:
        if kind == 0:
            known = v
        else:
            seqs = v
        out.append((t, known, seqs))
    return out


def hybrid(lib: ReferenceSpec, known: dict, seqs: list | None = None, loops=()) -> ReferenceSpec:
    """Library slides, with the learned segment (`known`: slide -> SegmentSpec)
    where there is one, on the library's scale (`SCALE`)."""
    by = {g.slide: g for g in lib.segments}
    if SCALE == "library" and known:
        f = float(np.median([by[s].cells / k.cells for s, k in known.items() if s in by]))
        known = {s: rescale(k, f) for s, k in known.items()}
    segs = [known.get(g.slide, g) for g in lib.segments]
    return ReferenceSpec(source="library", segments=segs, covered=lib.covered, learned=list(seqs or []),
                         loops=list(loops))


def sheet_known(sheet: ReferenceSpec, slides) -> dict:
    """The lead sheet's segments, for the ideal-learning arms
    (SHEET_PARTS=rhythm: without its chords)."""
    from dataclasses import replace as _r

    by = {g.slide: g for g in sheet.segments}
    out = {s: by[s] for s in slides if s in by}
    parts = os.environ.get("SHEET_PARTS", "")
    if parts in ("rhythm", "length"):
        out = {s: _r(g, chord=np.full(g.cells, -1, np.int16)) for s, g in out.items()}
    if parts in ("length", "length+chords"):
        # the sheet's line length only: its words evenly over the sung part
        def even(g):
            last = g.words[-1][1] if g.words else 0
            pos = np.round(np.linspace(0, last, len(g.words))).astype(int)
            return _r(g, words=[(w, int(o)) for (w, _o), o in zip(g.words, pos)])

        out = {s: even(g) for s, g in out.items()}
    return out


def carry(old: OnlineFollower, new_ref: SegmentReference) -> np.ndarray:
    """The old belief (rates x cells) on the new reference's cells, by each
    slide's share: a place 40% through a slide stays 40% through it."""
    o, n = old.ref, new_ref
    a = np.zeros((len(n.rates), n.P), np.float32)
    a[:, n.intro_cell] += old.alpha[:, o.intro_cell]
    for s in set(int(x) for x in o.slide_of if x is not None and x >= 0):
        oc = np.where(o.slide_of == s)[0]
        nc = np.where(n.slide_of == s)[0]
        oc = oc[oc != o.intro_cell]
        nc = nc[nc != n.intro_cell]
        if not len(oc) or not len(nc):
            continue
        k = np.round(np.arange(len(oc)) * (len(nc) - 1) / max(1, len(oc) - 1)).astype(int)
        np.add.at(a, (slice(None), nc[k]), old.alpha[:, oc])
    return a / max(a.sum(), 1e-12)


def schedule(rec, item: int, groups: list[set[int]], mode: str) -> list[tuple[float, set[int]]]:
    """[(time, slides known from then on)]: a slide is heard DELAY s after the
    band left it; the tune arms then know its whole group."""
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    group_of = {s: g for g in groups for s in g}
    heard_at: dict[int, float] = {}
    for i, g in enumerate(gt):
        if g.item_idx != item or g.slide_idx == 0:
            continue
        end = gt[i + 1].t_seconds if i + 1 < len(gt) else g.t_seconds + 10
        heard_at.setdefault(g.slide_idx, end + DELAY)
    events: dict[float, set[int]] = {}
    for s, t in heard_at.items():
        events.setdefault(t, set()).update(group_of.get(s, {s}) if mode != "naive" else {s})
    out, known = [], set()
    for t in sorted(events):
        known = known | events[t]
        out.append((t, set(known)))
    return out


def sheet_schedule(rec, item, groups, mode, sheet):
    return [(t, sheet_known(sheet, known)) for t, known in schedule(rec, item, groups, mode)]


def _slide_text(owner) -> dict:
    """slide -> " its words " (from the spans' owner map: the longest span ending at each word)."""
    words: dict[int, dict[int, str]] = {}
    for (sl, j), sp in owner.items():
        words.setdefault(sl, {})[j] = sp.split()[-1]
    return {sl: " " + " ".join(w[k] for k in sorted(w)) + " " for sl, w in words.items()}


def replay_item(rows, lib, sheet, slides, sections, owner, spans, ident, item, arm, sched):
    """`music_replay.replay_item` (shipped arm: words, scored, live chords,
    live key), with the reference rebuilt at each `sched` step."""
    zl = arm.get("zl")
    known = sheet_known(sheet, range(len(slides))) if TUNE == "full" else {}
    ref = SegmentReference(hybrid(lib, known) if known else lib, slides, sections, bpm=BPM)
    f = OnlineFollower(ref, identical=ident)
    evidence, control = [], []
    for k, r in enumerate(rows):
        if r.get("item") not in (None, item) and r["e"] != "enter":
            continue
        e = r["e"]
        if e == "word":
            evidence.append((r["t"] if zl else r["arr"], k, "word", (r["t"], r["text"])))
        elif e == "scored":
            evidence.append((r["t"] if zl else r["arr"], k, "scored", (r["t"], r["scores"])))
        elif e == "chords":
            for i in range(r["n"]):
                tc = r["t"] - (r["n"] - 1 - i) * 0.1
                evidence.append((tc if zl else r["arr"], k, "chord", (tc, np.asarray(r["probs"], np.float32))))
        elif e in ("enter", "key", "shown", "readout"):
            control.append((r["t"], k, r))
    evidence.sort(key=lambda x: (x[0], x[1]))
    control.sort(key=lambda x: (x[0], x[1]))
    pushed: list = []  # raw evidence already pushed, to replay onto a rebuilt reference
    key_k = None

    def push(fo, kind, p, arr):
        if kind == "word":
            fo.push_word(p[0], arr, p[1])
        elif kind == "scored":
            score = {sw: p[1][spans.index(sp)] for sw, sp in owner.items()}
            vec, heard = fo.ref.scored(score), fo.ref.heard(score)
            if vec is not None:
                w = online.SCORED_WEIGHT * fo.ref.reliability(score)
                fo.push_scored(p[0], arr, vec, weight=w, heard=heard, entropy=fo.ref.step_entropy(score))
                if heard:
                    best = max(score, key=score.__getitem__)
                    # every slide whose words contain the heard words, not only the
                    # slide the span was built from
                    fo.note_heard_line(arr, {sl for sl, ws in _slide_text(owner).items() if f" {owner[best]} " in ws})
            elif heard:
                fo.push_heard(arr)
        elif kind == "chord":
            fo.push_chords(p[0], arr, p[1])

    out, ei, si = [], 0, 0
    for t, _k, r in control:
        # what the look-back has learned by now: rebuild, carry the belief
        while si < len(sched) and sched[si][0] <= t:
            _t, known, seqs = sched[si]
            si += 1
            if TUNE == "full":
                known = sheet_known(sheet, range(len(slides)))
            import tune_learn as _tl

            marks = [sl for tm, sl in _tl.MARKS.get(item, []) if tm <= _t]
            new_ref = SegmentReference(hybrid(lib, known, seqs, marks), slides, sections, bpm=BPM)
            if key_k is not None and LEARN == "sheet":
                new_ref.set_key(int(key_k))  # the sheet's chords are in its own key
            nf = OnlineFollower(new_ref, identical=ident)
            nf.alpha, nf.ck_frame = carry(f, new_ref), f.ck_frame
            nf.shown, nf._heard_at, nf.music_enabled = f.shown, f._heard_at, f.music_enabled
            for arr, kind, p in pushed:
                if int(p[0] * FPS) > f.ck_frame:
                    push(nf, kind, p, arr)
            f = nf
        e = r["e"]
        if e == "enter":
            if r["item"] == item:
                mid = r["t"] > 0 and r["slide"] not in (None, 0)
                f.anchor(r["slide"], r["t"], mid_slide=mid)
            continue
        if e == "key":
            key_k = r["k"]
            if LEARN == "sheet":
                f.ref.set_key(int(key_k))  # learned chords are in the band's key already
            f.music_enabled = True
            continue
        if e == "shown":
            f.shown = r["slide"]
            continue
        while ei < len(evidence) and evidence[ei][0] <= t + 1e-9:
            arr, _o, kind, p = evidence[ei]
            push(f, kind, p, arr)
            pushed.append((arr, kind, p))
            ei += 1
        horizon = t - f.refilter_s - 1.0
        if pushed and pushed[0][2][0] < horizon - 5:
            pushed = [x for x in pushed if x[2][0] >= horizon]
        res = f.readout(t)
        if res.slide is not None and res.slide != f.shown:
            f.shown = res.slide
        out.append((t, res.slide, r.get("slide")))
    return out


def decisions(name, arm):
    rdir, trace, sheets = mr.CASES[name]
    rows = d.load_trace(trace)
    rec = load_recording(rdir)
    ig = identical_slide_groups(rec.setlist_content)
    enters = sorted((r["t"], r["item"], r["slide"]) for r in rows if r["e"] == "enter")
    end = max(r["t"] for r in rows)
    spans_on = [(t, enters[i + 1][0] if i + 1 < len(enters) else end + 1, it) for i, (t, it, _s) in enumerate(enters)]
    out = []
    for item, pdf in sheets.items():
        snap = [x for x in rec.setlist_content if x.item_idx == item][0]
        slides = [list(s) for s in snap.slides]
        sections = list(snap.slide_sections)
        ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(slides))}
        owner = spans_for(snap.slides)
        spans = sorted(set(owner.values()))
        score = fsc.from_extract(mr.sheet(pdf))
        lib, sheet = library_spec(slides), sheet_spec(score, slides, sections)
        if TUNE == "metre":
            from tune_groups import metre_groups

            groups = metre_groups(slides, syl_tol=int(os.environ.get("SYL_TOL", "1")),
                                  chain=os.environ.get("CHAIN", "1") == "1")
        elif TUNE == "text":
            # what a live session can know without a sheet: identical-text slides only
            norm = [" ".join(fsc.norm(w) for w in sw if fsc.norm(w)) for sw in slides]
            by: dict = {}
            for si, t in enumerate(norm):
                if t:
                    by.setdefault(t, set()).add(si)
            groups = list(by.values())
        else:
            groups = tune_groups(score, slides, sections, "bars" if TUNE == "bars" else "melody")
        if LEARN == "perf":
            import tune_learn

            chords = M.feats(name, CHORD_SRC if CHORD_SRC == "mix" or name != "bml" else "mix", end + 5)[1]
            from auto_presenter.music.inspect import load_wav

            import json as _json

            bfile = mr.SPK / f"beats_{name}.json"
            beats = np.asarray(_json.loads(bfile.read_text())["beats"]) if bfile.exists() else None
            ctx = (name, load_wav(Path(rdir) / "audio.wav"), beats)
            sched = tune_learn.perf_schedule(rows, item, slides, groups, TUNE, chords, end, ctx)
            swap = {int(x) for x in os.environ.get("SWAP_SLIDES", "").split(",") if x}
            if swap:
                # these slides' learned material replaced by the sheet's (diagnosis)
                sh = {g.slide: g for g in sheet.segments}
                sched = [(t, {s: (sh[s] if s in swap and s in sh else k) for s, k in known.items()}) for t, known in sched]
            seg_mode = os.environ.get("SEGMENTS", "")
            if seg_mode == "sheet":
                # the look-back's schedule (which slides, when), the sheet's material
                sched = [(t, sheet_known(sheet, known)) for t, known in sched]
            elif seg_mode in ("library", "chords_on_library"):
                # the same rebuilds with the library's own segments ("library": a
                # check on the rebuild itself), or with only the learned chords
                # laid over the library's word layout by each slide's share
                lib_seg = {g.slide: g for g in lib.segments}

                def chords_only(s, k):
                    from dataclasses import replace as _r

                    b = lib_seg[s]
                    if seg_mode == "library":
                        return b
                    src = np.minimum((np.arange(b.cells) * k.cells / b.cells).astype(int), k.cells - 1)
                    return _r(b, chord=k.chord[src].copy())

                sched = [(t, {s: chords_only(s, k) for s, k in known.items() if s in lib_seg}) for t, known in sched]
        else:
            sched = sheet_schedule(rec, item, groups, TUNE, sheet)
        if TUNE in ("none", "full"):
            sched = []
        order = []
        if ORDER == "oracle":
            order = oracle_order(rec, item)
        elif ORDER == "lookback":
            import tune_learn

            order = tune_learn.lookback_order(rows, item, slides, end)
        sched = merge(sched, order)
        prev = None
        for t, slide, _ in replay_item(rows, lib, sheet, slides, sections, owner, spans, ident, item, arm, sched):
            if slide is None or slide == prev:
                continue
            prev = slide
            if any(a <= t < b and it == item for a, b, it in spans_on):
                out.append((t, item, int(slide)))
    out += [(t, it, s) for t, it, s in enters if t > 0]
    out.sort()
    dec, shown = [], None
    for t, it, s in out:
        if (it, s) != shown:
            shown = (it, s)
            dec.append(Decision(t_seconds=t, item_idx=it, slide_idx=s, source="ai"))
    return dec, rec
