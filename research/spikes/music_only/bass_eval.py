"""Bass spike, step 2: how well can we read the bass note off the recording?

    uv run --project sidecar python research/spikes/music_only/bass_eval.py [case ...]

Ground truth = the lead sheet's chord at the golden-derived sheet position
(gt.timeline: slide cues + Whisper word anchors, about +-0.5 s), its bass being
the slash note or else the root, transposed by the key shift that best fits BTC.

Scored per CHORD SPAN (one played chord on the sheet, e.g. two bars of G/B),
with 0.5 s trimmed off each end to absorb alignment error, and only spans with
>= 0.6 s left: the method's evidence summed over the span, argmax = its bass.
Methods (all on the room mix unless 'stem'):
  stft_h_off   centred 0.51 s STFT, 41-330 Hz, harmonic sum of octaves   (look-back)
  stft_h_live  trailing 0.26 s STFT, same                               (live, ~0.15 s lag)
  stft_f_off   centred 0.51 s STFT, fundamentals 41-196 Hz only
  stft_f_live  trailing 0.26 s STFT, fundamentals 41-196 Hz only        (live; best in bass_variants.py)
  bp_low       Basic Pitch contour (cached), MIDI 28-52, max per pitch class
  bp_lowest    Basic Pitch: the lowest semitone with salience >= 0.3
  btc_root     BTC chord distribution folded to its root ("bass = root")
  stem_h/f     the stft_h_off / stft_f_off on the Demucs bass stem (upper bound only)
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bass_lib as bl  # noqa: E402
import features as ft  # noqa: E402

TRIM, MIN_LEN = 0.5, 0.6
BASS = {"rl", "komh", "since"}


def methods(case: str, t: np.ndarray) -> dict[str, np.ndarray]:
    rdir = bl.S / bl.CASES[case][0]
    F = ft.load(bl.S / f"{bl.FEATS[case]}_feats.npz")
    x = bl.load_audio(rdir / "audio.wav")
    st = bl.load_audio(rdir / "stems" / "bass.wav")
    out = {
        "stft_h_off": bl.stft_bass_chroma(x, t, causal=False),
        "stft_h_live": bl.stft_bass_chroma(x, t, causal=True, n_fft=4096),
        "stft_f_off": bl.stft_bass_chroma(x, t, causal=False, harmonic=False),
        "stft_f_live": bl.stft_bass_chroma(x, t, causal=True, n_fft=4096, harmonic=False),
        "bp_low": bl.bp_bass_chroma(F, "mix", t),
        "bp_lowest": bl.bp_bass_chroma(F, "mix", t, lowest=True),
        "btc_root": bl.btc_root_chroma(F, "mix", t),
        "stem_h": bl.stft_bass_chroma(st, t, causal=False),
        "stem_f": bl.stft_bass_chroma(st, t, causal=False, harmonic=False),
    }
    # low-band level check: how much sub-100 Hz energy the room mic keeps vs the stem
    ts, P = bl.spectrogram(x[: bl.SR * 120], 8192, 1600, False)
    ts2, P2 = bl.spectrogram(st[: bl.SR * 120], 8192, 1600, False)
    f = np.arange(P.shape[1]) * bl.SR / 8192
    band = lambda Q, a, b: float(Q[:, (f >= a) & (f < b)].sum())
    lvl = {"stem_rms_over_mix_rms_dB": float(20 * np.log10(np.sqrt(np.mean(st ** 2)) / np.sqrt(np.mean(x ** 2)))),
           "mix_40_100_over_100_300_dB": 10 * np.log10(band(P, 40, 100) / band(P, 100, 300)),
           "stem_40_100_over_100_300_dB": 10 * np.log10(band(P2, 40, 100) / band(P2, 100, 300))}
    return out, F, lvl


def norm_rows(X: np.ndarray) -> np.ndarray:
    Y = X - X.min(axis=1, keepdims=True)
    return Y / (Y.sum(axis=1, keepdims=True) + 1e-9)


def span_instances(g: bl.SongGT) -> list[tuple[int, int, int]]:
    """(frame a, frame b, chord index) runs; trimmed."""
    out = []
    i, n = 0, len(g.span)
    while i < n:
        if g.span[i] < 0:
            i += 1
            continue
        j = i
        while j < n and g.span[j] == g.span[i] and g.slide[j] == g.slide[i]:
            j += 1
        a, b = i + int(TRIM * bl.FPS), j - int(TRIM * bl.FPS)
        if b - a >= int(MIN_LEN * bl.FPS):
            out.append((a, b, int(g.span[i])))
        i = j
    return out


def auc(pos, neg) -> float:
    pos, neg = np.asarray(pos), np.asarray(neg)
    if not len(pos) or not len(neg):
        return float("nan")
    return float(np.mean([(p > neg).mean() + 0.5 * (p == neg).mean() for p in pos]))


def main(cases: list[str]) -> None:
    bl.OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    levels = {}
    offsets = defaultdict(lambda: defaultdict(list))
    for case in cases:
        rdir = bl.S / bl.CASES[case][0]
        dur = float(ft.load(bl.S / f"{bl.FEATS[case]}_feats.npz")["duration"])
        t = np.arange(0, dur, 1.0 / bl.FPS)
        M, F, lvl = methods(case, t)
        levels[case] = lvl
        btc25 = bl.btc_chord_frames(F, "mix", t)
        for sheet, item in bl.CASES[case][1].items():
            g = bl.song_gt(case, sheet, item, dur)
            k = bl.key_shift(g, btc25)
            inst = span_instances(g)
            print(f"{case:7s} {sheet[:34]:34s} item {g.item} key shift +{k}  aligned frames {int((g.span >= 0).sum())}  spans {len(inst)}", flush=True)
            NM = {m: norm_rows(X) for m, X in M.items()}
            for a, b, ci in inst:
                ch = g.chords[ci][2]
                root, bass = (ch.root + k) % 12, (ch.bass + k) % 12
                third = (root + (3 if ch.minor else 4)) % 12
                fifth = (root + 7) % 12
                r = dict(case=case, sheet=sheet, chord=ch.name, slash=ch.slash, root=root, bass=bass,
                         third=third, fifth=fifth, t0=float(g.t[a]), t1=float(g.t[b - 1]), ci=ci,
                         slide=int(g.slide[a]), pos=float(g.pos[a]), btc_ok=None)
                bt = btc25[a:b].sum(axis=0)
                r["btc_ok"] = int(np.argmax(bt[:24])) == 2 * root + int(ch.minor)
                for m, X in NM.items():
                    v = X[a:b].sum(axis=0)
                    v = v / (v.sum() + 1e-9)
                    r[m] = dict(est=int(np.argmax(v)), p=[round(float(q), 4) for q in v])
                    # alignment check: bass accuracy with the audio evidence shifted by d s
                    if ch.slash is False:
                        for d in (-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0):
                            o = int(d * bl.FPS)
                            aa, bb = a + o, b + o
                            if aa >= 0 and bb <= len(X):
                                offsets[m][d].append(int(np.argmax(X[aa:bb].sum(axis=0))) == bass)
                rows.append(r)
    (bl.OUT / "spans.json").write_text(json.dumps(rows))
    (bl.OUT / "levels.json").write_text(json.dumps(levels, indent=1))
    report(rows, levels, offsets)


def report(rows, levels, offsets) -> None:
    ms = [m for m in rows[0] if isinstance(rows[0][m], dict)]
    L = []
    L.append(f"spans: {len(rows)} ({sum(r['slash'] for r in rows)} slash); BTC chord (root+maj/min) right on {np.mean([r['btc_ok'] for r in rows]):.0%} of spans (alignment sanity: BTC is ~70% per chord)")
    L.append("")
    L.append("method | bass acc (all) | non-slash: =root | slash: =slash note | slash: =root (fooled) | slash-vs-root AUC")
    L.append("---|---|---|---|---|---")
    for m in ms:
        allacc = np.mean([r[m]["est"] == r["bass"] for r in rows])
        ns = [r for r in rows if not r["slash"]]
        sl = [r for r in rows if r["slash"]]
        ns_acc = np.mean([r[m]["est"] == r["bass"] for r in ns])
        sl_acc = np.mean([r[m]["est"] == r["bass"] for r in sl]) if sl else float("nan")
        sl_root = np.mean([r[m]["est"] == r["root"] for r in sl]) if sl else float("nan")
        # does the span's evidence favour the slash note over the root?  positives: slash spans
        # (bass - root); negatives: plain chords, (best of 3rd/5th) - root
        pos = [r[m]["p"][r["bass"]] - r[m]["p"][r["root"]] for r in sl]
        neg = [max(r[m]["p"][r["third"]], r[m]["p"][r["fifth"]]) - r[m]["p"][r["root"]] for r in ns]
        L.append(f"{m} | {allacc:.0%} | {ns_acc:.0%} | {sl_acc:.0%} | {sl_root:.0%} | {auc(pos, neg):.2f}")
    L.append("")
    L.append("alignment check (non-slash spans, bass accuracy with the evidence shifted; peak at 0 = aligned):")
    for m in ms:
        L.append(f"  {m:12s} " + "  ".join(f"{d:+.1f}s:{np.mean(v):.0%}" for d, v in sorted(offsets[m].items())))
    L.append("")
    L.append("per song (stft_h_off / bp_low / stem_h bass accuracy; slash spans n):")
    by = defaultdict(list)
    for r in rows:
        by[r["sheet"]].append(r)
    for sh, rs in by.items():
        sl = [r for r in rs if r["slash"]]
        f = lambda m, rr: f"{np.mean([r[m]['est'] == r['bass'] for r in rr]):.0%}" if rr else "-"
        L.append(f"  {sh[:40]:40s} n={len(rs):3d} all {f('stft_h_off', rs)}/{f('bp_low', rs)}/{f('stem_h', rs)}"
                 f"   slash n={len(sl)}: {f('stft_h_off', sl)}/{f('bp_low', sl)}/{f('stem_h', sl)}")
    L.append("")
    L.append("low-band level (energy 40-100 Hz relative to 100-300 Hz, first 2 min): " + json.dumps(
        {k: {a: round(b, 1) for a, b in v.items()} for k, v in levels.items()}))
    L.append("")
    L.append("by recording group: BASS = rl, komh, since (a bass guitar reaches the room mic); NOBASS = the rest")
    L.append("method | BASS: bass acc | BASS: slash=slash (n) | NOBASS: bass acc | NOBASS: slash=slash (n) | NOBASS slash=root")
    L.append("---|---|---|---|---|---")
    for m in ms:
        cells = []
        for grp in (True, False):
            rs = [r for r in rows if (r["case"] in BASS) == grp]
            sl = [r for r in rs if r["slash"]]
            cells.append(f"{np.mean([r[m]['est'] == r['bass'] for r in rs]):.0%}")
            cells.append(f"{np.mean([r[m]['est'] == r['bass'] for r in sl]):.0%} ({len(sl)})" if sl else "-")
        sl = [r for r in rows if r["case"] not in BASS and r["slash"]]
        cells.append(f"{np.mean([r[m]['est'] == r['root'] for r in sl]):.0%}")
        L.append(f"{m} | " + " | ".join(cells))
    txt = "\n".join(L)
    print(txt)
    (bl.OUT / "eval.md").write_text(txt + "\n")


if __name__ == "__main__":
    main(sys.argv[1:] or list(bl.CASES))
