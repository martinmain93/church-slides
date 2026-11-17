"""Benchmark BTC (Park et al., ISMIR 2019) against the hand-rolled decoder.

Three questions, in order of importance to the design:

  1. Does it stop over-segmenting? Mine emitted 331 chord changes where the
     chart has 48. That is the diagnosed blocker.
  2. Does the decoded sequence identify the RIGHT chart? Cross-matching every
     recording against every other song's chart is the control that failed for
     the template matcher, and it is the pass/fail here.
  3. What does it cost? 3 M parameters, but no paper reports a realtime factor.
"""
import difflib
import os
import sys
import time

import numpy as np
import torch

BTC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "btc")
sys.path.insert(0, BTC)
os.chdir(BTC)

from btc_model import BTC_model, HParams  # noqa: E402
from utils.mir_eval_modules import audio_file_to_features, idx2chord  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spike_chord_charts import (GOLDEN_MAP, MAJOR_DEGREE, FLAT_DEGREE, PITCH,  # noqa: E402
                                find_sheet, parse_chart)

REC = os.path.expanduser("~/Library/Application Support/com.churchslides.app/recordings")
device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

config = HParams.load("run_config.yaml")
model = BTC_model(config=config.model).to(device)
ck = torch.load("./test/btc_model.pt", map_location=device, weights_only=False)
mean, std = ck["mean"], ck["std"]
model.load_state_dict(ck["model"])
model.eval()


def decode(path):
    feature, fps, _ = audio_file_to_features(path, config)
    feature = ((feature.T - mean) / std)
    n = config.model["timestep"]
    pad = n - (len(feature) % n)
    feature = np.concatenate([feature, np.zeros((pad, feature.shape[1]))])
    x = torch.tensor(feature, dtype=torch.float32).unsqueeze(0).to(device)
    out = []
    with torch.no_grad():
        for t in range(x.shape[1] // n):
            attn, _ = model.self_attn_layers(x[:, n * t:n * (t + 1), :])
            pred, _ = model.output_layer(attn)
            out += pred.squeeze().cpu().numpy().tolist()
    times = np.arange(len(out)) * fps
    return [idx2chord[int(i)] for i in out], times


def degree_name(deg, minor):
    n = MAJOR_DEGREE.get(deg) or FLAT_DEGREE.get(deg)
    return None if n is None else n + ("m" if minor else "")


def to_nash(labels, rotation):
    out = []
    for lab in labels:
        if lab in ("N", "X"):
            continue
        root = lab.split(":")[0]
        minor = lab.endswith(":min")
        r = PITCH.get(root.upper())
        if r is None:
            continue
        n = degree_name((r - rotation) % 12, minor)
        if n and (not out or out[-1] != n):
            out.append(n)
    return out


def chart_seq(chart):
    out = []
    for ln in chart.lines:
        for _, deg, minor in ln.degrees(chart.key):
            n = degree_name(deg, minor)
            if n and (not out or out[-1] != n):
                out.append(n)
    return out


charts, seqs = {}, {}
for rid, name, needles in GOLDEN_MAP:
    p = find_sheet(*needles)
    if not p:
        continue
    c = parse_chart(p)
    if c.key is None:
        continue
    charts[rid] = name
    seqs[name] = chart_seq(c)

print(f"device={device}  BTC majmin, 25 classes, hop {config.feature['hop_length']} "
      f"@ {config.mp3['song_hz']} Hz\n")
print(f"{'recording':26s} {'dur':>7s} {'sec':>6s} {'xRT':>7s} {'BTC chgs':>9s} "
      f"{'mine':>6s} {'chart':>6s}")
print("-" * 74)
decoded = {}
for rid, name in charts.items():
    wav = f"{REC}/{rid}/audio.wav"
    if not os.path.exists(wav):
        continue
    t0 = time.time()
    labels, times = decode(wav)
    el = time.time() - t0
    dur = times[-1]
    changes = sum(1 for i in range(1, len(labels)) if labels[i] != labels[i - 1])
    decoded[rid] = labels
    print(f"{rid:26s} {dur:6.0f}s {el:5.1f}s {dur / el:6.1f}x {changes:9d} "
          f"{'':6s} {len(seqs[name]):6d}")

print()
names = list(seqs)
print("Cross-match control: rows = recording, columns = chart, * = correct\n")
print(f"{'recording':24s} " + " ".join(f"{n[:9]:>10s}" for n in names))
correct, diag, off = 0, [], []
for rid, name in charts.items():
    if rid not in decoded:
        continue
    best_by_chart = []
    for m in names:
        best = max(difflib.SequenceMatcher(None, to_nash(decoded[rid], r), seqs[m]).ratio()
                   for r in range(12))
        best_by_chart.append(best)
    win = names[int(np.argmax(best_by_chart))]
    correct += (win == name)
    for i, s in enumerate(best_by_chart):
        (diag if names[i] == name else off).append(s)
    cells = " ".join(("*" if names[i] == name else " ") + f"{s:9.3f}"
                     for i, s in enumerate(best_by_chart))
    print(f"{name[:24]:24s} {cells}  {'HIT' if win == name else ''}")
print(f"\ncorrect chart: {correct}/{len(diag)} (chance {1 / len(names):.0%})")
print(f"correct-pair {np.mean(diag):.3f} vs wrong-pair {np.mean(off):.3f} "
      f"(gap {np.mean(diag) - np.mean(off):+.3f})")
