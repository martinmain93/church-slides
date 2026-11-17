"""Bass spike: low-band STFT variants scored on the spans bass_eval.py wrote.

    uv run --project sidecar python research/spikes/music_only/bass_variants.py

Each variant: window length, top of the fundamental band, compression, and
whether harmonics are summed. Reports bass accuracy (all spans / slash spans)
per recording group: 'bass' = rl, komh, since (a bass guitar is audible,
low band within ~2 dB of 100-300 Hz) vs 'nobass' = the rest.
"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bass_lib as bl

BASS = {"rl", "komh", "since"}


def chroma(x, t, n_fft, top, comp, harm, causal=False):
    ts, P = bl.spectrogram(x, n_fft, 800, causal)
    semis = bl.semitone_map(P, n_fft, 28, top + 24)
    if comp == "log":
        semis = np.log1p(semis / (np.median(semis) + 1e-9))
    elif comp == "sqrt":
        semis = np.sqrt(semis)
    if harm:
        sal = np.zeros((len(semis), top - 28 + 1), np.float32)
        for j in range(top - 28 + 1):
            sal[:, j] = semis[:, j] + 0.5 * semis[:, j + 12] + 0.25 * semis[:, j + 24]
    else:
        sal = semis[:, : top - 28 + 1]
    pcs = np.zeros((len(sal), 12), np.float32)
    for j in range(sal.shape[1]):
        pcs[:, (28 + j) % 12] += sal[:, j]
    pcs = pcs / (pcs.sum(axis=1, keepdims=True) + 1e-9)
    return bl.to_fps(ts, pcs, t)


def main():
    rows = json.loads((bl.OUT / "spans.json").read_text())
    variants = [(n, top, comp, harm, src, causal)
                for src in ("mix", "stem")
                for n in (4096, 8192, 16384)
                for top in (43, 50, 55)
                for comp in ("log", "sqrt", "lin")
                for harm in (0, 1)
                for causal in ((False, True) if (src == "mix" and n <= 8192) else (False,))]
    res = {}
    for case in bl.CASES:
        rs = [r for r in rows if r["case"] == case]
        if not rs:
            continue
        dur = float(np.load(bl.S / f"{bl.FEATS[case]}_feats.npz")["duration"])
        t = np.arange(0, dur, 1 / bl.FPS)
        audio = {"mix": bl.load_audio(bl.S / bl.CASES[case][0] / "audio.wav"),
                 "stem": bl.load_audio(bl.S / bl.CASES[case][0] / "stems/bass.wav")}
        for v in variants:
            n, top, comp, harm, src, causal = v
            C = chroma(audio[src], t, n, top, comp, harm, causal)
            for r in rs:
                a, b = int(round(r["t0"] * bl.FPS)), int(round(r["t1"] * bl.FPS)) + 1
                est = int(np.argmax(C[a:b].sum(axis=0)))
                res.setdefault(v, []).append((case in BASS, r["slash"], est == r["bass"], est == r["root"]))
        print("done", case, flush=True)
    L = ["src | n_fft | top MIDI | comp | harm | causal | bass recs: all | bass recs: slash=slash | no-bass recs: all | all recs: slash=slash (n)", "---|---|---|---|---|---|---|---|---|---"]
    out = []
    for v, xs in res.items():
        xs = np.array(xs)
        B, NB = xs[xs[:, 0] == 1], xs[xs[:, 0] == 0]
        sl = xs[xs[:, 1] == 1]
        bsl = B[B[:, 1] == 1]
        out.append((B[:, 2].mean(), v, B[:, 2].mean(), bsl[:, 2].mean() if len(bsl) else np.nan, NB[:, 2].mean(), sl[:, 2].mean(), len(sl)))
    out.sort(key=lambda z: -z[0])
    for _, v, a, b, c, d, n in out:
        L.append(" | ".join(map(str, v)) + f" | {a:.0%} | {b:.0%} | {c:.0%} | {d:.0%} ({n})")
    txt = "\n".join(L)
    print(txt)
    (bl.OUT / "variants.md").write_text(txt + "\n")


if __name__ == "__main__":
    main()
