"""Ground-truth chord times from the chart, and BTC's accuracy against them.

The chart binds each chord to a WORD; Whisper gives every word a TIME. Compose
the two and the chart becomes a timed chord score — the ground truth this
project has never had for harmony, built from data we already hold.

Repeats are handled by working inside each golden cue interval: the cues give
the performance order, so a chorus sung four times is aligned four times against
the one chart line, rather than asking a global alignment to cope with reorder.

Reported against the null of the same comparison at the eleven wrong rotations,
because a diatonic song scores respectably against its own transpositions.
"""
import difflib
import json
import os
import re
import sys

import numpy as np
import torch

SC = os.path.dirname(os.path.abspath(__file__))
BTC = os.path.join(SC, "btc")
sys.path.insert(0, BTC)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spike_chord_charts import (GOLDEN_MAP, MAJOR_DEGREE, FLAT_DEGREE, PITCH,  # noqa: E402
                                find_sheet, parse_chart)

REC = os.path.expanduser("~/Library/Application Support/com.churchslides.app/recordings")
device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
os.chdir(BTC)
from btc_model import BTC_model, HParams  # noqa: E402
from utils.mir_eval_modules import audio_file_to_features, idx2chord  # noqa: E402

config = HParams.load("run_config.yaml")
btc = BTC_model(config=config.model).to(device)
ck = torch.load("./test/btc_model.pt", map_location=device, weights_only=False)
mean, std = ck["mean"], ck["std"]
btc.load_state_dict(ck["model"])
btc.eval()


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


def norm(w):
    return re.sub(r"[^a-z']", "", w.lower())


def deg_name(deg, minor):
    n = MAJOR_DEGREE.get(deg) or FLAT_DEGREE.get(deg)
    return None if n is None else n + ("m" if minor else "")


def btc_to_deg(lab, rot):
    if lab in ("N", "X"):
        return None
    r = PITCH.get(lab.split(":")[0].upper())
    return None if r is None else deg_name((r - rot) % 12, lab.endswith(":min"))


print(f"{'recording':26s} {'cues':>5s} {'anchors':>8s} {'cover':>6s} | "
      f"{'BTC acc':>8s} {'null':>6s} {'best rot':>9s}")
print("-" * 78)
allacc, allnull, per_rec = [], [], {}
for rid, name, needles in GOLDEN_MAP:
    p = find_sheet(*needles)
    wav = f"{REC}/{rid}/audio.wav"
    if not p or not os.path.exists(wav):
        continue
    chart = parse_chart(p)
    if chart.key is None:
        continue
    rj = json.load(open(f"{REC}/{rid}/recording.json"))
    gj = json.load(open(f"{REC}/{rid}/golden.json"))
    tj = json.load(open(f"{REC}/{rid}/transcript.json"))
    words = tj.get("words", [])
    if not words:
        continue
    slides = {}
    for item in rj.get("setlist_content", []):
        for i, sl in enumerate(item.get("slides", [])):
            slides[i] = [norm(w) for w in sl]

    # Each chart line as (normalised words, placed chords).
    lines = []
    for ln in chart.lines:
        lw = [norm(w) for w in ln.lyric.split() if norm(w)]
        if lw:
            lines.append((lw, ln.placed(chart.key)))

    cues = sorted(gj.get("ground_truth", []), key=lambda c: c["t_seconds"])
    dur = rj.get("duration_seconds", words[-1]["end"])
    anchors = []                       # (time, nashville)
    for ci, cue in enumerate(cues):
        t0 = cue["t_seconds"]
        t1 = cues[ci + 1]["t_seconds"] if ci + 1 < len(cues) else dur
        sw = slides.get(cue["slide_idx"], [])
        if not sw:
            continue
        # Which chart lines make up this slide?
        best, bl = 0.0, None
        for k in range(len(lines)):
            for span in (1, 2, 3):
                cand = [w for j in range(k, min(k + span, len(lines))) for w in lines[j][0]]
                if not cand:
                    continue
                r = difflib.SequenceMatcher(None, cand, sw).ratio()
                if r > best:
                    best, bl = r, (k, span)
        if best < 0.55 or bl is None:
            continue
        k, span = bl
        cw, cc = [], []
        for j in range(k, min(k + span, len(lines))):
            base = len(cw)
            cw += lines[j][0]
            for d in lines[j][1]:
                if d["nashville"] and d["word_index"] is not None:
                    cc.append((base + d["word_index"], d["nashville"]))
        tw = [(norm(w["word"]), w["start"]) for w in words
              if t0 <= w["start"] < t1 and norm(w["word"])]
        if len(tw) < 3:
            continue
        sm = difflib.SequenceMatcher(None, cw, [w for w, _ in tw])
        pos = {}
        for a, b, size in sm.get_matching_blocks():
            for o in range(size):
                pos[a + o] = tw[b + o][1]
        for widx, nash in cc:
            # Nearest aligned word, so a chord over an unmatched word still anchors.
            if not pos:
                continue
            j = min(pos, key=lambda q: abs(q - widx))
            if abs(j - widx) <= 2:
                anchors.append((pos[j], nash))
    anchors.sort()
    if len(anchors) < 20:
        print(f"{rid:26s} {len(cues):5d} {len(anchors):8d}   too few anchors")
        continue

    labels, times = btc_frames(wav)
    # Expected chord holds from its anchor until the next.
    at = np.array([a for a, _ in anchors])
    an = [n for _, n in anchors]
    idx = np.searchsorted(at, times, side="right") - 1
    inside = (idx >= 0) & (times <= at[-1] + 5.0)
    scores = []
    for rot in range(12):
        got = [btc_to_deg(labels[i], rot) for i in range(len(labels))]
        ok = [got[i] == an[idx[i]] for i in range(len(times)) if inside[i]]
        scores.append(float(np.mean(ok)) if ok else 0.0)
    scores = np.array(scores)
    best_rot = int(np.argmax(scores))
    null = float(np.delete(scores, best_rot).mean())
    allacc.append(scores[best_rot]); allnull.append(null)
    per_rec[rid] = (anchors, labels, times, best_rot, an, at)
    print(f"{rid:26s} {len(cues):5d} {len(anchors):8d} {inside.mean():5.0%} | "
          f"{scores[best_rot]:7.1%} {null:5.1%} {best_rot:9d}")

print("-" * 78)
print(f"BTC frame accuracy against chart-derived ground truth: "
      f"{np.mean(allacc):.1%}  (null {np.mean(allnull):.1%})")
print(f"literature MajMin on pop benchmarks, for scale: 79.3%")
np.save(f"{SC}/anchors.npy", np.array([1]))
import pickle
pickle.dump(per_rec, open(f"{SC}/per_rec.pkl", "wb"))
