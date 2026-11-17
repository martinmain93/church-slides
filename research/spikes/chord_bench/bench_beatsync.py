"""The proposed architecture, end to end: beats gate chord changes.

BTC gives frame-level chord labels every 92.9 ms. Beat This! gives beat times.
Beat-synchronous decoding takes the modal BTC label within each beat interval,
so a chord may only change on a beat. This is the "beats corroborate chord
transitions" layer, and it is the standard remedy for over-segmentation.

Scored with the same cross-match control throughout: every recording against
every other song's chart, best over all 12 rotations. Three front ends compared
on identical charts and identical scoring.
"""
import difflib
import os
import sys
import time

import numpy as np
import soundfile as sf
import torch

SC = os.path.dirname(os.path.abspath(__file__))
BTC = os.path.join(SC, "btc")
sys.path.insert(0, BTC)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spike_chord_charts import (GOLDEN_MAP, MAJOR_DEGREE, FLAT_DEGREE, PITCH,  # noqa: E402
                                find_sheet, parse_chart)
from spike_chord_downbeat import chord_sequence, chromagram, load_wav  # noqa: E402
from spike_chord_downbeat import CHORD_NAMES  # noqa: E402

REC = os.path.expanduser("~/Library/Application Support/com.churchslides.app/recordings")
device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

os.chdir(BTC)
from btc_model import BTC_model, HParams  # noqa: E402
from utils.mir_eval_modules import audio_file_to_features, idx2chord  # noqa: E402

config = HParams.load("run_config.yaml")
btc = BTC_model(config=config.model).to(device)
ck = torch.load("./test/btc_model.pt", map_location=device, weights_only=False)
mean, std, _ = ck["mean"], ck["std"], btc.load_state_dict(ck["model"])
btc.eval()

from beat_this.inference import Audio2Beats  # noqa: E402
a2b = Audio2Beats(checkpoint_path="final0", device=str(device))


def btc_frames(path):
    feat, fps, _ = audio_file_to_features(path, config)
    feat = (feat.T - mean) / std
    n = config.model["timestep"]
    feat = np.pad(feat, ((0, n - (feat.shape[0] % n)), (0, 0)))
    x = torch.tensor(feat, dtype=torch.float32).unsqueeze(0).to(device)
    out = []
    with torch.no_grad():
        for t in range(x.shape[1] // n):
            attn, _ = btc.self_attn_layers(x[:, n * t:n * (t + 1), :])
            pred, _ = btc.output_layer(attn)
            out += pred.squeeze().cpu().numpy().tolist()
    return [idx2chord[int(i)] for i in out], np.arange(len(out)) * fps


def degree_name(deg, minor):
    n = MAJOR_DEGREE.get(deg) or FLAT_DEGREE.get(deg)
    return None if n is None else n + ("m" if minor else "")


def labels_to_nash(labels, rotation):
    out = []
    for lab in labels:
        if lab in ("N", "X", None):
            continue
        r = PITCH.get(lab.split(":")[0].upper())
        if r is None:
            continue
        n = degree_name((r - rotation) % 12, lab.endswith(":min"))
        if n and (not out or out[-1] != n):
            out.append(n)
    return out


def beat_sync(labels, times, beats):
    """Modal BTC label inside each beat interval — a change may only occur on a beat."""
    out = []
    idx = np.searchsorted(times, beats)
    for a, b in zip(idx[:-1], idx[1:]):
        seg = [x for x in labels[a:b] if x not in ("N", "X")]
        out.append(max(set(seg), key=seg.count) if seg else None)
    return out


charts, seqs = {}, {}
for rid, name, needles in GOLDEN_MAP:
    p = find_sheet(*needles)
    if not p:
        continue
    c = parse_chart(p)
    if c.key is not None:
        charts[rid] = name
        seqs[name] = labels_to_nash(
            [f"{['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'][(d + c.key) % 12]}"
             f"{':min' if m else ''}" for ln in c.lines for _, d, m in ln.degrees(c.key)],
            c.key)

variants = {"template (mine)": {}, "BTC frame-level": {}, "BTC beat-synchronous": {}}
for rid in charts:
    wav = f"{REC}/{rid}/audio.wav"
    if not os.path.exists(wav):
        continue
    labels, times = btc_frames(wav)
    x, sr = sf.read(wav, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    beats, downs = a2b(x, sr)
    variants["BTC frame-level"][rid] = labels
    variants["BTC beat-synchronous"][rid] = beat_sync(labels, times, np.asarray(beats))
    ch, _ = chromagram(load_wav(wav))
    variants["template (mine)"][rid] = [
        CHORD_NAMES[int(i)].replace("m", ":min") if CHORD_NAMES[int(i)].endswith("m")
        else CHORD_NAMES[int(i)] for i in chord_sequence(ch)]

names = list(seqs)
print(f"{'front end':24s} {'correct chart':>14s} {'correct-pair':>13s} "
      f"{'wrong-pair':>11s} {'gap':>7s} {'changes':>8s}")
print("-" * 82)
for vname, dec in variants.items():
    diag, off, hits, chgs = [], [], 0, []
    for rid, name in charts.items():
        if rid not in dec:
            continue
        lab = dec[rid]
        chgs.append(sum(1 for i in range(1, len(lab)) if lab[i] != lab[i - 1]))
        sc = [max(difflib.SequenceMatcher(None, labels_to_nash(lab, r), seqs[m]).ratio()
                  for r in range(12)) for m in names]
        hits += (names[int(np.argmax(sc))] == name)
        for i, s in enumerate(sc):
            (diag if names[i] == name else off).append(s)
    print(f"{vname:24s} {f'{hits}/{len(charts)}':>14s} {np.mean(diag):13.3f} "
          f"{np.mean(off):11.3f} {np.mean(diag) - np.mean(off):+7.3f} {np.mean(chgs):8.0f}")
