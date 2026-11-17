"""Fusion, on ONE canonical chart alignment.

Earlier versions derived the per-slide chord reference and the ground-truth
chord stream from two separate difflib alignments of the same chart. They agreed
only 53% of the time, so the "oracle" arm was a second noisy estimate rather
than a ceiling -- it scored *below* BTC, which no upper bound can do -- and
BTC's measured accuracy was against a target that was itself half wrong.

Here there is a single alignment: slide -> chart line -> {word: chord}. The
oracle chord at any moment is that mapping applied to the slide the golden says
is showing. That is what a perfect detector would report *if the band plays the
chart*, which is the assumption this ceiling rests on and should be read with.

    score(slide) = sum over matched words of idf(w) * (1 + LAMBDA * a(w))
    a(w) = +1 agree, -1 disagree, 0 either side undefined

Reported on word-ambiguous instants (top two slides within 2% on words alone),
because that is where a corroborating feature can act at all.
"""
import difflib
import json
import os
import pickle
import re
import sys
from collections import Counter

import numpy as np

SC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spike_chord_charts import (GOLDEN_MAP, MAJOR_DEGREE, FLAT_DEGREE, PITCH,  # noqa: E402
                                find_sheet, parse_chart)

REC = os.path.expanduser("~/Library/Application Support/com.churchslides.app/recordings")
per_rec = pickle.load(open(f"{SC}/per_rec.pkl", "rb"))
WORD_WIN, STEP, TIE = 8.0, 1.0, 0.02
DEG = ["1", "2m", "3m", "4", "5", "6m", "7", "b2", "b3", "b5", "b6", "b7"]


def norm(w):
    return re.sub(r"[^a-z']", "", str(w).lower())


def deg_name(d, m):
    n = MAJOR_DEGREE.get(d) or FLAT_DEGREE.get(d)
    return None if n is None else n + ("m" if m else "")


def btc_to_deg(lab, rot):
    if lab in ("N", "X"):
        return None
    r = PITCH.get(lab.split(":")[0].upper())
    return None if r is None else deg_name((r - rot) % 12, lab.endswith(":min"))


def build(rid, name, needles):
    p = find_sheet(*needles)
    if not p or rid not in per_rec:
        return None
    chart = parse_chart(p)
    if chart.key is None:
        return None
    rj = json.load(open(f"{REC}/{rid}/recording.json"))
    gj = json.load(open(f"{REC}/{rid}/golden.json"))
    tj = json.load(open(f"{REC}/{rid}/transcript.json"))
    slides = {}
    for item in rj.get("setlist_content", []):
        for i, sl in enumerate(item.get("slides", [])):
            w = [norm(x) for x in sl if norm(x)]
            if w:
                slides[i] = w
    if len(slides) < 3:
        return None

    lines = []
    for ln in chart.lines:
        placed = {d["word_index"]: d["nashville"] for d in ln.placed(chart.key)
                  if d["nashville"] and d["word_index"] is not None}
        cur, pairs = None, []
        for wi, w in enumerate([x for x in ln.lyric.split() if norm(x)]):
            cur = placed.get(wi, cur)
            pairs.append((norm(w), cur))
        if pairs:
            lines.append(pairs)

    ref = {}
    for si, sw in slides.items():
        best, bp = 0.0, None
        for k in range(len(lines)):
            for span in (1, 2, 3):
                cand = [x for j in range(k, min(k + span, len(lines))) for x in lines[j]]
                if cand:
                    r = difflib.SequenceMatcher(None, [w for w, _ in cand], sw).ratio()
                    if r > best:
                        best, bp = r, cand
        m = {}
        if best >= 0.5 and bp:
            sm = difflib.SequenceMatcher(None, [w for w, _ in bp], sw)
            for a, b, size in sm.get_matching_blocks():
                for o in range(size):
                    m[sw[b + o]] = bp[a + o][1]
        ref[si] = m

    _, labels, times, rot, _, _ = per_rec[rid]
    ct = np.asarray(times)
    cl = [btc_to_deg(x, rot) for x in labels]
    cues = sorted(gj.get("ground_truth", []), key=lambda c: c["t_seconds"])
    words = []
    ci = 0
    for w in tj.get("words", []):
        nw = norm(w["word"])
        if not nw:
            continue
        ts = float(w["start"])
        while ci + 1 < len(cues) and cues[ci + 1]["t_seconds"] <= ts:
            ci += 1
        truth = cues[ci]["slide_idx"] if cues else None
        i = int(np.searchsorted(ct, ts)) - 1
        words.append((nw, ts, cl[i] if 0 <= i < len(cl) else None,
                      ref.get(truth, {}).get(nw)))
    return dict(rid=rid, slides=slides, ref=ref, words=words, cues=cues,
                dur=float(rj.get("duration_seconds", times[-1])))


def evaluate(rec, lam, arm, p_correct=1.0, seed=0):
    rng = np.random.default_rng(seed)
    slides, ref = rec["slides"], rec["ref"]
    ids = sorted(slides)
    df = Counter()
    for si in ids:
        for w in set(slides[si]):
            df[w] += 1
    idf = {w: np.log(len(ids) / (1 + df[w])) + 1.0 for w in df}
    sets = {si: set(slides[si]) for si in ids}
    txt = {si: tuple(slides[si]) for si in ids}
    nrm = {si: (np.sqrt(sum(idf.get(w, 1.0) for w in sets[si])) or 1.0) for si in ids}

    out, ci = [], 0
    cues = rec["cues"]
    for t in np.arange(cues[0]["t_seconds"], rec["dur"], STEP):
        while ci + 1 < len(cues) and cues[ci + 1]["t_seconds"] <= t:
            ci += 1
        truth = cues[ci]["slide_idx"]
        if truth not in slides:
            continue
        obs = [(w, b, o) for w, ts, b, o in rec["words"] if t - WORD_WIN <= ts <= t]
        if len(obs) < 2:
            continue
        heard_of = {}
        for w, btc, orc in obs:
            if arm == "btc":
                h = btc
            elif arm == "oracle":
                h = orc
            elif arm == "sim":
                # A detector of stated accuracy: the true chord with prob p,
                # otherwise a uniformly wrong one.
                h = orc if (orc and rng.random() < p_correct) else (
                    DEG[rng.integers(len(DEG))] if orc else None)
            else:                                     # wrong-chord control
                h = DEG[(DEG.index(orc) + 1) % len(DEG)] if orc in DEG else None
            heard_of[w] = h
        wsc, fsc = {}, {}
        for si in ids:
            b_s = f_s = 0.0
            for w, _, _ in obs:
                if w not in sets[si]:
                    continue
                iw = idf.get(w, 1.0)
                b_s += iw
                want, heard = ref[si].get(w), heard_of.get(w)
                a = 0.0 if not (want and heard) else (1.0 if want == heard else -1.0)
                f_s += iw * (1.0 + lam * a)
            wsc[si] = b_s / nrm[si]
            fsc[si] = f_s / nrm[si]
        order = sorted(ids, key=lambda si: -wsc[si])
        top = wsc[order[0]]
        sec = wsc[order[1]] if len(order) > 1 else 0.0
        amb = top > 0 and (top - sec) / max(top, 1e-9) < TIE
        pf = max(ids, key=lambda si: fsc[si])
        out.append((txt[order[0]] == txt[truth], txt[pf] == txt[truth], amb))
    return out


recs = [r for r in (build(rid, n, nd) for rid, n, nd in GOLDEN_MAP) if r]
base = [x for r in recs for x in evaluate(r, 0.0, "btc")]
b_all = np.mean([w for w, _, _ in base])
b_amb = np.mean([w for w, _, a in base if a])
n_amb = sum(1 for _, _, a in base if a)
print(f"{len(recs)} recordings.  words only: all {b_all:.1%} (n={len(base)}), "
      f"ambiguous {b_amb:.1%} (n={n_amb})\n")
print(f"{'arm':22s} {'lambda':>7s} {'all':>8s} {'delta':>8s} "
      f"{'ambiguous':>10s} {'delta':>8s}")
print("-" * 68)
for arm in ("btc", "oracle", "wrong"):
    for lam in (0.25, 0.5, 1.0):
        S = [x for r in recs for x in evaluate(r, lam, arm)]
        aa = np.mean([f for _, f, _ in S])
        ab = np.mean([f for _, f, a in S if a])
        print(f"{'words + ' + arm:22s} {lam:7.2f} {aa:7.1%} {aa - b_all:+7.1%} "
              f"{ab:9.1%} {ab - b_amb:+7.1%}")

print("\nHow accurate would the chord detector need to be? (lambda 0.5)")
print(f"{'detector accuracy':>18s} {'all':>8s} {'delta':>8s} "
      f"{'ambiguous':>10s} {'delta':>8s}")
print("-" * 56)
for p in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
    S = [x for s in range(3) for r in recs
         for x in evaluate(r, 0.5, "sim", p_correct=p, seed=s)]
    aa = np.mean([f for _, f, _ in S])
    ab = np.mean([f for _, f, a in S if a])
    print(f"{p:17.0%} {aa:8.1%} {aa - b_all:+7.1%} {ab:9.1%} {ab - b_amb:+7.1%}")
