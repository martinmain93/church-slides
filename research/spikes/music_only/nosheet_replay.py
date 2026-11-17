"""Can the follower run with no lead sheet? Synthetic references from library words.
modes: sheet (real), libspec (follower.spec.library_spec), library (synthetic sheet in library order), oracle (this recording's operator order, in-fit ceiling),
       cross (order from a *different* recording of the same song, if any)."""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import music_replay as M
from music_replay import d, mr, load_recording, identical_slide_groups, spans_for, SegmentReference, fsc, Decision

MODE = os.environ.get("REF", "sheet")
GAP_Q = float(os.environ.get("GAP", "4"))
CROSS = {"bml": ("set", 0), "set": ("bml", 0)}  # (case, item) of the other BML performance

def synth(slides, order):
    bars, cur, t = [], [], 0.0
    def flush():
        nonlocal cur
        bars.append({"number": len(bars) + 1, "meter": 4.0, "voltas": [], "end_repeat": False, "start_repeat": False,
                     "ds": False, "segno": False, "chords": [], "notes": cur, "section": None})
        cur = []
    for si in order:
        for w in slides[si]:
            if t >= 4.0: flush(); t = 0.0
            cur.append({"voice": 1, "midi": None, "onset": t, "dur": 1.0, "syllables": {"0": w}, "hyphen_after": {"0": False}})
            t += 1.0
        g = GAP_Q
        while g > 0:
            step = min(g, 4.0 - t); t += step; g -= step
            if t >= 4.0: flush(); t = 0.0
    if cur or t > 0: flush()
    return {"bars": bars, "key_fifths": 0}

def learned_rows(case, item):
    """Performance rows for one item of one case, made the way a live session
    makes them: the trace's heard words and the golden's operator clicks
    through `learning.session.LearningSession`."""
    from auto_presenter.learning.session import LearningSession
    from auto_presenter.protocol import SetlistContent, SetlistItemContent
    from auto_presenter.asr.types import Word
    rdir, trace, _sheets = mr.CASES[case]
    rec = load_recording(rdir); rows = d.load_trace(trace)
    content = SetlistContent(items=[SetlistItemContent(item_idx=x.item_idx, song_id=x.song_id or f"song{x.item_idx}",
                                                        slides=[list(s) for s in x.slides]) for x in rec.setlist_content])
    sess = LearningSession(content, None)
    for g in sorted(rec.ground_truth, key=lambda g: g.t_seconds):
        sess.on_slide_shown(g.item_idx, g.slide_idx, g.source or "manual", t=g.t_seconds)
    sess.on_words([Word(text=r["text"], t_start=r["t"], t_end=r["t"] + 0.3, confidence=1.0)
                   for r in rows if r["e"] == "word" and r["item"] == item])
    sess.tick(final=True)
    perf = sess._perfs.get(item)
    return [perf.summary] if perf is not None and perf.summary is not None else []


def op_order(rec, item, slides):
    seq = []
    for g in rec.ground_truth:
        if g.item_idx == item and slides[g.slide_idx] and (not seq or seq[-1] != g.slide_idx):
            seq.append(g.slide_idx)
    return seq

def by_text(seq, src_slides, dst_slides):
    key = {tuple(s): i for i, s in reversed(list(enumerate(dst_slides)))}
    return [key[tuple(src_slides[i])] for i in seq if tuple(src_slides[i]) in key]

def decisions(name, arm):
    rdir, trace, sheets = mr.CASES[name]
    rows = d.load_trace(trace); rec = load_recording(rdir)
    dur = max(r["t"] for r in rows) + 5
    ig = identical_slide_groups(rec.setlist_content)
    enters = sorted((r["t"], r["item"], r["slide"]) for r in rows if r["e"] == "enter")
    end = max(r["t"] for r in rows)
    spans_on = [(t, enters[i + 1][0] if i + 1 < len(enters) else end + 1, it) for i, (t, it, _s) in enumerate(enters)]
    out = []
    for item, pdf in sheets.items():
        snap = [x for x in rec.setlist_content if x.item_idx == item][0]
        slides = [list(s) for s in snap.slides]
        ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(slides))}
        owner = spans_for(snap.slides); spans = sorted(set(owner.values()))
        lib = [i for i, s in enumerate(slides) if s]
        if MODE == "sheet":
            data = mr.sheet(pdf)
        elif MODE in ("libspec", "libself", "sheetself", "libcross", "sheetcross"):
            data = None
        elif MODE == "library":
            data = synth(slides, lib)
        elif MODE == "oracle":
            data = synth(slides, op_order(rec, item, slides) or lib)
        elif MODE == "cross":
            if name in CROSS and item == 0:
                oc, oi = CROSS[name]; orec = load_recording(mr.CASES[oc][0])
                osl = [list(s) for s in [x for x in orec.setlist_content if x.item_idx == oi][0].slides]
                seq = by_text(op_order(orec, oi, osl), osl, slides)
                print(f"  cross {name}: other order {seq}", file=sys.stderr)
                data = synth(slides, seq or lib)
            else:
                data = synth(slides, lib)
        if MODE in ("libspec", "libself", "sheetself", "libcross", "sheetcross"):
            from auto_presenter.follower.spec import library_spec
            from auto_presenter.follower.reference import sheet_spec
            from auto_presenter.learning.profile import aggregate, learned_spec
            base = (sheet_spec(fsc.from_extract(mr.sheet(pdf)), slides, list(snap.slide_sections))
                    if MODE.startswith("sheet") else library_spec(slides))
            perf_rows = []
            if MODE.endswith("self"):
                perf_rows = learned_rows(name, item)
            elif MODE.endswith("cross") and name in CROSS and item == 0:
                perf_rows = learned_rows(*CROSS[name])
            song = aggregate(perf_rows, slides)
            if os.environ.get("NO_TIMING"):
                song.timing = {}
            if os.environ.get("NO_ORDER"):
                song.sequences = []
            spec = learned_spec(base, song)
            print(f"  {name}/{item} {MODE}: rows {song.used} n_eff {song.n_eff:.2f} sequences {[q for q, _ in song.sequences]} timed lines {len(song.timing)}", file=sys.stderr)
            ref = SegmentReference(spec, snap.slides, list(snap.slide_sections))
        else:
            ref = SegmentReference(fsc.from_extract(data), snap.slides, list(snap.slide_sections))
        if MODE != "sheet":
            print(f"  {name}/{item} {MODE}: coverage {ref.coverage.bound}/{ref.coverage.sung}", file=sys.stderr)
        prev = None
        for t, slide, _ in M.replay_item(rows, ref, owner, spans, ident, item, arm, name, dur):
            if slide is None or slide == prev: continue
            prev = slide
            if any(a <= t < b and it == item for a, b, it in spans_on):
                out.append((t, item, int(slide)))
    out += [(t, it, s) for t, it, s in enters if t > 0]
    out.sort(); dec, shown = [], None
    for t, it, s in out:
        if (it, s) != shown:
            shown = (it, s); dec.append(Decision(t_seconds=t, item_idx=it, slide_idx=s, source="ai"))
    return dec, rec

M.decisions = decisions
if __name__ == "__main__":
    aname, arm = M.parse(sys.argv[1])
    M.apply(arm)
    from music_replay import online, BASE_CW, BASE_MW
    online.CHORD_WEIGHT = BASE_CW * arm["cw"]; online.MELODY_WEIGHT = BASE_MW * arm["mw"]; online.LYRIC_HOLD_S = arm["hold"]
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else list(mr.CASES)
    res = {n: M.run(n, arm) for n in names}
    tune = [res[n][0] for n in names if n not in mr.HELD_OUT]; held = [res[n][0] for n in names if n in mr.HELD_OUT]
    f = lambda xs: f"{np.mean(xs):.3f}" if xs else "-"
    print(f"{MODE}:{aname} tuning {f(tune)} held-out {f(held)} | " + "  ".join(f"{n} {r[0]:.3f}/{r[1]:+.0f}ms" for n, r in res.items()), flush=True)
