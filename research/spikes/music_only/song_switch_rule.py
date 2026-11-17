"""Could music switch songs on its own, safely? A margin x duration rule on song_id's rates.

Music proposes "the band is playing song X" when X's trailing-window rate beats every
other setlist song's by `margin` (nats/s) for `hold` seconds. Counted against the golden:
a proposal is *right* if X is the screen's song (or the next song within 15 s: the band
starts a song before the operator shows its lyrics), else *false*; and for every genuine
song change, how soon after the cue music first proposes the new song.
"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import song_id as si
import follow as fl
import features as ft
import multi_replay as mr
from auto_presenter.eval.replay import load_recording
from auto_presenter.follower import score as fsc
from auto_presenter.follower.reference import SegmentReference

KEYS = {}  # (name, item) -> semitones from the sheet, the band's key


def known_keys(name, rec, sheets, ch):
    """The band's key per song: from the live follower's estimates where it
    followed the song, else the chords of the song's own golden stretch."""
    import collections
    import diag_live_trace as d
    rows = d.load_trace(mr.CASES[name][1])
    votes = collections.defaultdict(collections.Counter)
    for r in rows:
        if r["e"] == "key":
            votes[r["item"]][r["k"]] += 1
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    out = {}
    for item, pdf in sheets.items():
        if votes[item]:
            out[item] = votes[item].most_common(1)[0][0]
            continue
        frames = [f for i, g in enumerate(gt) if g.item_idx == item
                  for f in range(int(g.t_seconds * si.FPS), int((gt[i + 1].t_seconds if i + 1 < len(gt) else len(ch) / si.FPS) * si.FPS))]
        snap = [x for x in rec.setlist_content if x.item_idx == item][0]
        ref = SegmentReference(fsc.from_extract(mr.sheet(pdf)), snap.slides, list(snap.slide_sections))
        from auto_presenter.follower import sheet as sh
        out[item], _ = sh.estimate_key_shift(ch[[f for f in frames if f < len(ch)]], ref.chord_sheet)
    return out


def rates(name, arm, win):
    rdir, trace, sheets = mr.CASES[name]
    words = []
    if "words" in arm:
        import diag_live_trace as d
        # placed where they arrive (~3 s after they were sung): what was known when
        words = [(r["arr"], r["text"]) for r in d.load_trace(trace) if r["e"] == "word"]
    rec = load_recording(rdir)
    F = ft.load(mr.SPK / f"{si.FEATS[name]}_feats.npz")
    _x, pcs, ch = fl.frame_evidence(F, "mix", si.FPS, float(F["duration"]))
    ll = {}
    for item, pdf in sheets.items():
        snap = [x for x in rec.setlist_content if x.item_idx == item][0]
        ref = SegmentReference(fsc.from_extract(mr.sheet(pdf)), snap.slides, list(snap.slide_sections))
        base = arm.replace("+words", "").replace("@key", "")
        key = None
        if "@key" in arm:
            if (name, item) not in KEYS:
                KEYS.update({(name, i): k for i, k in known_keys(name, rec, sheets, ch).items()})
            key = KEYS[(name, item)]
        ll[item] = si.song_loglik(ref, ch, pcs, base in ("chords", "both"), base in ("melody", "both"), 1.0, 1.0,
                                  words=words, key=key)
    items = sorted(ll); L = np.stack([ll[i] for i in items]); T = L.shape[1]
    n = int(win * si.FPS); c = np.concatenate([np.zeros((len(items), 1)), np.cumsum(L, axis=1)], axis=1)
    idx = np.arange(T); r = (c[:, idx + 1] - c[:, np.maximum(0, idx + 1 - n)]) / win
    return rec, items, r

def evaluate(rec, items, r, margin, hold):
    T = r.shape[1]; lag = 10
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    def song_at(t):
        cur = None
        for g in gt:
            if g.t_seconds <= t: cur = g.item_idx
        return cur
    srt = np.sort(r, axis=0); best = np.argmax(r, axis=0); m = srt[-1] - srt[-2]
    # share of sung time with the right song, for the record
    screen = np.array([song_at(f / si.FPS) if f % 10 == 0 else -2 for f in range(T)])
    props, run, last = [], 0, None
    for f in range(lag, T):
        b, ok = best[f - lag], m[f - lag] >= margin
        run = run + 1 if ok and b == last else (1 if ok else 0)
        last = b if ok else None
        if run == int(hold * si.FPS):
            props.append((f / si.FPS, items[b]))
    right = false = 0; fl_ = []
    for t, it in props:
        if song_at(t) == it or song_at(t + 15) == it: right += 1
        else: false += 1; fl_.append((round(t), it, song_at(t)))
    switches = [(g.t_seconds, g.item_idx) for i, g in enumerate(gt) if i and g.item_idx != gt[i - 1].item_idx and g.item_idx in items]
    got = []
    for t, it in switches:
        p = [pt - t for pt, pit in props if pit == it and -15 <= pt - t <= 120]
        got.append((round(t), it, round(p[0]) if p else None))
    return right, false, fl_, got

if __name__ == "__main__":
    for arm in sys.argv[1:] or ["chords", "both"]:
        for win in (20, 40):
            data = {n: rates(n, arm, win) for n in ("set", "medley")}
            for margin in (0.02, 0.05, 0.1, 0.2):
                for hold in (5, 10, 20):
                    out = []
                    for n, (rec, items, r) in data.items():
                        right, false, fl_, got = evaluate(rec, items, r, margin, hold)
                        out.append(f"{n}: {false} false {fl_[:2] if fl_ else ''} switches " + " ".join(f"{t}->{it}:{'-' if d is None else f'{d:+d}'}" for t, it, d in got))
                    print(f"{arm:6s} win {win} margin {margin:.2f} hold {hold:2d}s | " + " | ".join(out), flush=True)
