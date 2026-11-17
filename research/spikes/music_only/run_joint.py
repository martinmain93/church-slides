"""Run the joint follower on the goldens and score it like the app is scored.

    uv run --project sidecar python research/spikes/music_only/run_joint.py [--songs wabn,rl] [--arms ...]

Sources (each placed at its audio time, arriving when it really would):
  words     Whisper small.en, live-windowed (MLX), each word at its t_start,
            arriving at its event's measured `available_at`
  mel_stem  Basic Pitch on the Demucs vocal stem, ~3.0 s late
  mel_mix   Basic Pitch on the mix, ~1.0 s late
  chd_mix   BTC on the mix, ~0.74 s late
  chd_stem  BTC on the `other` stem, ~2.74 s late

Scored two ways:
  tracking  exact sheet position within one beat of the ground truth at the
            moment of each read-out (no musical-equivalence forgiveness: with
            words in the model, chorus 1 and chorus 3 are different places)
  screen    the slide under the follower's position, shown from the read-out
            on, through the harness's own `wrong_slide_rate` (forgiving mode)
            and median anticipation — directly comparable with production.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

import cv
import follow as fl
import fusion
import gt
import joint
import lead
import segref
import score as sc
from auto_presenter.eval.metrics import anticipation_error_ms, wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision

LAT = {"mel_stem": 3.0, "mel_mix": 1.0, "chd_mix": 0.74, "chd_stem": 2.74}
W_MUSIC = 0.4  # the tempering leave-one-song-out chose for music on every song
#: Per-frame weight that counts each *observation* once: 1 / the number of
#: frames a detector's output stays the same (time-weighted mean, measured
#: over the goldens). A chord lasts ~39 frames and a sung note ~6, so a flat
#: per-frame weight counted every chord ~39 times against a word's once — the
#: bug that made adding chords hurt. Set WEIGHTING = "observation" to use.
OBS_RUN = {"chd_mix": 39.2, "chd_stem": 44.7, "mel_stem": 6.3, "mel_mix": 7.4, "chd_oracle": 39.2}
WEIGHTING = "flat"
SCORE_LATENCY = 1.0  # window end -> scores ready; MLX-plausible, CPU int8 measured slower
SCORE_SHIFT = 0.0  # seconds before the window end the scored text was sung (measured median 3.0)
WIN_S = 6.0
ALIGNED = False  # use per-step alignment times (wscore2_*.json) instead of SCORE_SHIFT
SCORER = "ct2"
GEOM = ""  # scoring shape tag, e.g. "_w3s4" (3 s window, 4-word spans)
WORD_EXTRA_LAG = 0.0  # extra delay on free words when the scorer shares the GPU (measured +0.35 s)  # or "mlx": wscore_mlx_*.json (truncated encoder, aligned ends always)
ARMS = {
    "words": ["words"],
    "mel_stem": ["mel_stem"],
    "chd_mix": ["chd_mix"],
    "words+mel_stem": ["words", "mel_stem"],
    "words+chd_mix": ["words", "chd_mix"],
    "words+mel_stem+chd_mix": ["words", "mel_stem", "chd_mix"],
    "words+mel_mix+chd_mix (no stems)": ["words", "mel_mix", "chd_mix"],
    "scored": ["scored"],
    "scored+mel_mix+chd_mix": ["scored", "mel_mix", "chd_mix"],
    "words+scored": ["words", "scored"],
    "words+scored+mel_mix+chd_mix": ["words", "scored", "mel_mix", "chd_mix"],
    "words+chd_ORACLE": ["words", "chd_oracle"],
    "words+mel_stem+chd_ORACLE": ["words", "mel_stem", "chd_oracle"],
}
# production (half-gap lead) wrong-slide on the same transcripts, from exp_scheduler.py
READOUT = "slide"  # or "cell": the slide under the single best cell
REF = "segment"  # or "sheet": the sheet's own roadmap and words
WORDS = "whisper"  # or fast_mix / fast_stem: the streaming Zipformer on the mix or the vocal stem
TEMPO = ""  # "": none; "oracle": the tracker's whole-song tempo at the right octave; "beats": trailing beats, causal
TEMPO_SIGMA = 0.06  # log-tempo spread of one tempo reading
TEMPO_WEIGHT = 0.1  # per frame: the trailing-8-beat reading changes about once a second, so count it once a second
TEMPO_LATENCY = 0.5
DRIFT_MAX = 0.1
P_RATE = 0.02
EXTEND = False  # a slide's segment runs to the next line's first word (where the operator switches), not last word + 1 bar  # per frame, the tempo state's random walk to each neighbour (8 % steps)  # a frame-to-frame pulse change larger than this is a tracking fault, not drift  # Beat This holds the last 0.5 s back until its peaks are final
LOOKAHEAD = 0.0
COMMIT = 0.0  # show the looked-ahead slide only when it holds this share of the projected belief; else the slide at "now"
SPONTANEOUS = ""  # "": always anticipate; "now": while the band is off-arrangement, show the slide at "now"; "heard": show what has been heard
SPONT_LOG = False
SPONT_KW: dict = {}
CLUSTERS = True  # hold only at section ends (False: at every slide end)
SPONT_STATS: dict = {}
ORDER = "prior"  # "oracle": the order prior is the operator's own slide order (an upper bound on knowing the arrangement)
P_REPEAT = 0.0  # at a slide's end, the chance the band sings the same slide again (a vamp)
PRODUCTION = {"wabn": 0.270, "rl": 0.414, "komh": 0.426, "isa": 0.209}


def sources_for(song: cv.Song, ref: lead.LeadSheetReference, events: list[dict], names: list[str],
                word_events: list[tuple[float, float, str]] | None = None, word_latency: float = 3.5):
    out = []
    for n in names:
        if n.startswith("scored"):
            out.append(scored_words(song, ref, SCORE_LATENCY))
            continue
        if n == "words":
            evs = []
            items = word_events if word_events is not None else [
                (w["t_start"], e["available_at"] + WORD_EXTRA_LAG, w["text"]) for e in events for w in e["words"]]
            for t, arr, text in items:
                v = ref.word(text)
                if v is not None:
                    evs.append((t, arr, v))
            # window = the longest real delay from a word's start to its arrival
            span = max([word_latency] + [arr - t for t, arr, _ in evs]) + 0.1
            out.append(joint.Source("words", span, 1.0, events=evs))
        else:
            src = "stems" if n.endswith("stem") else "mix"
            _, pcs, ch = song.ev[src]
            if n == "chd_oracle":
                dense = ref.chords(oracle_chords(song, ref))
            else:
                dense = ref.melody(pcs) if n.startswith("mel") else ref.chords(ch)
            w = W_MUSIC if WEIGHTING == "flat" else 1.0 / OBS_RUN[n]
            out.append(joint.Source(n, LAT.get(n, 0.74), w, dense=dense))
    return out


def tempo_source(song: cv.Song, ref, T: int) -> joint.Source:
    """The beat tracker's pulse as evidence on the follower's tempo state.

    `beats`: at each frame, the median inter-beat interval over the trailing
    8 beats that exist by then. A tracker's beat may be the quarter, half or
    eighth note, so a reading supports tempo x0.5, x1 and x2 equally and the
    follower's own position evidence picks between them. `oracle`: one
    tempo for the whole song, at the octave the transcript implies."""
    d_ = json.loads((cv.S / f"beats_{song.key}.json").read_text())
    b = np.array(d_["beats"])
    if TEMPO in ("bars", "bardrift"):
        # tempo from bar length, not beat spacing: a tracker that splits some
        # bars into 6 beats and others into 4 (Reckless Love's triplet feel)
        # reads a 1.5x tempo jump from beats, while its bar length holds.
        # Synthesise one pseudo-beat per sheet quarter note from each bar.
        db = np.array(d_["downbeats"])
        pseudo = []
        for x, y in zip(db, db[1:]):
            pseudo.extend(np.linspace(x, y, int(round(song.s.meter)), endpoint=False))
        b = np.array(pseudo)
    q_rates = ref.rates * fl.CELL  # quarter notes per second
    out = np.zeros((T, len(q_rates)), np.float32)
    if TEMPO == "oracle":
        cues = gt.build(song.s, song.slides, cv.S / song.key, float(song.F["duration"]))
        g = gt.song_rate(cues)
        tb = 1 / np.median(np.diff(b))
        tb = tb / 2 ** np.round(np.log2(tb / g))
        out[:] = -0.5 * (np.log(q_rates / tb) / TEMPO_SIGMA) ** 2
        return joint.Source("tempo", 0.0, TEMPO_WEIGHT, tempo=out)
    if TEMPO in ("drift", "bardrift"):
        # the pulse's change from frame to frame, octave-corrected (a tracker
        # that flips between counting quarters and eighths is not a tempo
        # change), and only small steps (a jump is a tracking fault, not drift)
        d = np.zeros(T, np.float32)
        prev = None
        for i in range(T):
            past = b[b <= i / 10][-9:]
            if len(past) < 5:
                continue
            p = 1 / np.median(np.diff(past))
            if prev is not None:
                p *= 2.0 ** np.round(np.log2(prev / p))
                step = np.log(p / prev)
                if abs(step) <= DRIFT_MAX:
                    d[i] = step
            prev = p
        return joint.Source("tempo drift", TEMPO_LATENCY, 1.0, drift=d)
    for i in range(T):
        past = b[b <= i / 10][-9:]
        if len(past) < 5:
            continue
        tb = 1 / np.median(np.diff(past))
        ll = [-0.5 * (np.log(q_rates / (tb * m)) / TEMPO_SIGMA) ** 2 for m in (0.5, 1.0, 2.0)]
        out[i] = np.max(ll, axis=0)
    return joint.Source("tempo", TEMPO_LATENCY, TEMPO_WEIGHT, tempo=out)


def oracle_order(song, rec, ref) -> np.ndarray:
    """The order prior as the operator actually went: what knowing this
    band's arrangement in advance would give. Exact slides, not identical-word
    groups — copies of a chorus are told apart only by what follows them, so
    pooling them would scramble exactly the order this is meant to supply.
    A slide with no segment of its own (an identical copy merged away)
    stands for its group's segment."""
    from auto_presenter.eval.metrics import identical_slide_groups
    item = cv.stage1.song_item(song.s, cv.S / song.key)
    ig = identical_slide_groups(rec.setlist_content)
    segs = list(ref.seg_slides)
    idx = {sl: i for i, sl in enumerate(segs)}

    def seg(sl):
        if sl in idx:
            return idx[sl]
        for x in sorted(ig.get((item, sl), ())):
            if x in idx:
                return idx[x]
        return None

    seq = [c.slide_idx for c in sorted(rec.ground_truth, key=lambda c: c.t_seconds) if c.item_idx == item and c.slide_idx > 0]
    S = len(segs)
    obs = np.zeros((S, S))
    for a, b in zip(seq, seq[1:]):
        i, j = seg(a), seg(b)
        if i is not None and j is not None and i != j:
            obs[i, j] += 1
    out = ref.succ.copy()
    for i in range(S):
        if obs[i].sum() > 0:
            out[i] = obs[i] / obs[i].sum()
    return out


TEMPO_REPORT = True


def tempo_report(song, key, walls):
    """How well the follower knows the tempo: its belief against the beat
    tracker's (octave-matched) pulse, over sung read-outs."""
    f = joint.follow
    rm = f.rate_marg / f.rate_marg.sum(axis=1, keepdims=True)
    q = np.geomspace(40, 130, rm.shape[1]) / 60.0
    b = np.array(json.loads((cv.S / f"beats_{key}.json").read_text())["beats"])
    sung = np.array([song.tl["slide"][min(len(song.tl["slide"]) - 1, int(w * 10))] > 0 for w in walls])
    errs, ents = [], []
    for k in np.where(sung)[0]:
        past = b[b <= walls[k]][-9:]
        if len(past) < 5:
            continue
        p = 1 / np.median(np.diff(past))
        mean = float(rm[k] @ q)
        p *= 2.0 ** np.round(np.log2(mean / p))
        errs.append(mean / p - 1)
        ents.append(-(rm[k] * np.log(rm[k] + 1e-12)).sum() / np.log(len(q)))
    e = np.array(errs)
    print(f"      tempo belief vs pulse: median {np.median(e):+.1%}, within 5% {np.mean(np.abs(e) <= 0.05):4.0%}, entropy {np.median(ents):.2f}")


def roadmap_sequence(song: cv.Song) -> list[int]:
    """The slide order the sheet's roadmap sings: every slide span, by where
    it falls in the unrolled sheet. Identical-word slides share spans, so
    each span is credited to the lowest-numbered slide that claims it."""
    seen = {}
    for si, sps in song.spans.items():
        for sp in sps:
            key = (sp.w0, sp.w1)
            if key not in seen or si < seen[key][1]:
                seen[key] = (sp.start, si)
    return [si for _, si in sorted(seen.values())]


def oracle_chords(song: cv.Song, ref) -> np.ndarray:
    """Perfect chords: the sheet's chord at the ground-truth position, as a
    confident (0.9) distribution in the band's key; uniform where unknown."""
    import reference as rfm
    T = len(song.ev["mix"][0])
    out = np.full((T, 25), 1 / 25, np.float32)
    k = getattr(ref, "key_shift", 0)
    base = fl.build(song.s, song.slides, key_shift=k)
    for i in range(min(T, len(song.tl["pos"]))):
        p = song.tl["pos"][i]
        if np.isfinite(p):
            c = base.chord[min(base.P - 1, int(p // fl.CELL))]
            if c >= 0:
                out[i] = 0.1 / 24
                out[i, c] = 0.9
    return out


def scored_words(song: cv.Song, ref, latency: float, step: float = 2.0, win: float = 6.0, tokens: float = 7.0):
    """Whisper as a scorer (wscore_cache.py): every `step` s, every candidate
    6-word span of the slides scored against the trailing 6 s of audio.
    Each score lands on the cell of its span's last word and holds until the
    next word of that slide; the vector is a log-likelihood ratio (mean
    log-probability per token x a typical span's token count, against the
    average over cells). Windows overlap win/step times, so each counts
    step/win — one observation per stretch of audio."""
    if SCORER in ("mlx", "mlx_flat", "mlx_auto", "mlx_dtw"):
        data = json.loads((cv.S / f"wscore_mlx{GEOM}_{song.key}.json").read_text())
        win = float(data.get("win", 6.0))
        if SCORER == "mlx_auto":
            # this shape's own offset: where its best span typically ends, from
            # the scorer's timing (median, robust to the noisy per-span peaks)
            best_end = [row[int(np.argmax(sc_))] for sc_, row in zip(data["scores"], data["ends"])]
            global SCORE_SHIFT
            SCORE_SHIFT = win - float(np.median(best_end))
        if SCORER in ("mlx_flat", "mlx_auto"):
            data.pop("ends", None)  # place by a flat offset instead of per-span attention timing
        if SCORER == "mlx_dtw":
            # place by Whisper's own word timing of the best span (DTW over the
            # alignment heads, wscore_align_cache.py): when its last word was sung
            data["ends"] = [[p] * len(row) for p, row in zip(data["place"], data["scores"])]
        step_win = (2.0, win)
    else:
        step_win = (2.0, 6.0)
    if SCORER == "ct2":
        p2 = cv.S / f"wscore2_{song.key}.json"
        data = json.loads((p2 if (ALIGNED and p2.exists()) else cv.S / f"wscore_{song.key}.json").read_text())
        if not ALIGNED:
            data.pop("ends", None)
    idx = {sp: i for i, sp in enumerate(data["spans"])}
    owner = {tuple(int(x) for x in k.split(":")): v for k, v in data["owner"].items()}
    seg = np.asarray(ref.seg_of)
    order = sorted((c, sw) for sw, c in ref.word_cell.items())
    evs = []
    ends = data.get("ends")
    for k, (t, row) in enumerate(zip(data["steps"], data["scores"])):
        row = np.asarray(row)
        v = np.full(ref.P, np.nan)
        for c, sw in order:
            if sw in owner:
                v[c] = row[idx[owner[sw]]]
        # hold each word's score until the next word of the same segment
        last = np.nan
        for c in range(ref.P):
            if c > 0 and seg[c] != seg[c - 1]:
                last = np.nan
            if not np.isnan(v[c]):
                last = v[c]
            v[c] = last
        v = np.where(np.isnan(v), np.nanmin(v), v)
        e = tokens * (v - v.mean())
        # stamp at the audio time the span was sung, not the window's end
        if ends is None:
            t_ev = t - SCORE_SHIFT
        else:
            # when the best-matching span's last word was sung, from its alignment
            e_best = ends[k][int(np.argmax(row))]
            t_ev = t - step_win[1] + (e_best if e_best >= 0 else step_win[1] - SCORE_SHIFT)
        evs.append((t_ev, t + latency, joint.robust(e, 0.2).astype(np.float32)))
    # the re-filter window must cover the longest gap between the audio an
    # event describes and its arrival, or late events land before the
    # checkpoint and are silently dropped
    span = max((arr - t_a for t_a, arr, _ in evs), default=latency) + 0.5
    return joint.Source("scored", span, step_win[0] / step_win[1] if step_win[1] >= step_win[0] else 1.0, events=evs)


def slide_map(song: cv.Song, P: int) -> list[int | None]:
    """The slide under each cell (any one, where identical slides overlap)."""
    out: list[int | None] = [None] * P
    for si, sps in song.spans.items():
        for sp in sps:
            a = int(sp.start // fl.CELL)
            b = int(np.ceil(sc.span_end(song.s, sp) / fl.CELL))
            for c in range(max(0, a), min(P, b)):
                out[c] = si
    return out


def slide_readout(marg: np.ndarray, slides_at: list[int | None], identical: dict) -> list[int | None]:
    """Which slide to show: the slide with the most belief summed over its
    cells (slides with identical words pooled), not the slide under the
    single best cell. Music spreads belief over every copy of a repeated
    tune; the copy-by-copy argmax then flips between passes on noise, while
    the per-slide total is decided by what differs between them — the words."""
    idx = np.array([-1 if x is None else x for x in slides_at])
    n = max(1, idx.max() + 1)
    out = []
    for m in marg:
        mass = np.bincount(idx[idx >= 0], weights=m[idx >= 0], minlength=n)
        pooled = mass.copy()
        for a in range(n):
            for b in identical.get(a, ()):
                if b != a and b < n:
                    pooled[a] += mass[b]
        out.append(int(np.argmax(pooled)) if pooled.max() > 0 else None)
    return out


def spontaneous_gate(song, ref, ident, key):
    """The spontaneity tracker (spontaneous.py) as the follower's gate, and
    the sheet's clusters (clusters.py) on the reference: while the band is
    off the arrangement, the follower anticipates inside a section but waits
    at a section's end until it hears where the band went."""
    import clusters
    import spontaneous as sp
    gof = sp.groups(len(song.slides), ident)
    ends = clusters.sheet_cluster_ends(song.s, song.spans)
    gend: dict[int, bool] = {}
    for sl, e in ends.items():
        gend[gof[sl]] = gend.get(gof[sl], False) or e
    ref.cluster_end = np.array([gend.get(gof[sl], True) for sl in ref.seg_slides])
    tr = sp.SpontaneityTracker(np.asarray(ref.seg_of), np.asarray(ref.slide_of), np.asarray(ref.jump_targets),
                               np.asarray(ref.end_cells), ref.succ, gof, sp.run_lengths(roadmap_sequence(song), gof),
                               cluster_end=gend if CLUSTERS else None, **SPONT_KW)
    if not CLUSTERS:
        ref.cluster_end = np.ones(len(ref.seg_slides), bool)
    gate = lambda wall, m, r: tr.update(float(wall), m, float(r @ ref.rates / r.sum()))
    if SPONTANEOUS == "always":
        # never guess past a section's end: anticipate inside sections only
        gate = lambda wall, m, r: (tr.update(float(wall), m, float(r @ ref.rates / r.sum())), True)[1]
    return gate, tr


def operator_deviations(song, rec, ident) -> list[float]:
    """Times the operator left the written order (roadmap or library order)."""
    item = cv.stage1.song_item(song.s, cv.S / song.key)
    grp = lambda sl: min({sl} | set(ident.get(sl, ())))
    road = roadmap_sequence(song)
    lib = [i for i in range(1, len(song.slides)) if song.slides[i]]
    written = {(grp(a), grp(b)) for q in (road, lib) for a, b in zip(q, q[1:])}
    cues = [(c.t_seconds, c.slide_idx) for c in sorted(rec.ground_truth, key=lambda c: c.t_seconds) if c.item_idx == item and c.slide_idx > 0]
    return [t for (_, a), (t, b) in zip(cues, cues[1:]) if grp(a) != grp(b) and (grp(a), grp(b)) not in written]


def spontaneous_report(tr, key, devs=()):
    trig = [e.wall for e in tr.events if e.spontaneous and e.kind != "expected"]
    # a trigger is right if the band really left the arrangement in the 10 s before it
    # (the tracker sees the band ~3 s late, then debounces)
    hits = sum(1 for t in trig if any(0 <= t - d <= 10 for d in devs))
    caught = sum(1 for d in devs if any(0 <= t - d <= 10 for t in trig))
    SPONT_STATS[key] = (float(np.mean(joint.follow.held)), len(trig), hits, caught, len(devs))
    if SPONT_LOG:
        for e in tr.events:
            print(f"      {e.wall:6.1f}  {e.kind:10s} {e.detail:40s} -> {'SPONTANEOUS' if e.spontaneous else 'anticipating'}")


def committed(ahead, now, marg, slides_at, identical):
    """Anticipate only what the belief is sure of: show the looked-ahead
    slide when it (with its identical copies) holds at least COMMIT of the
    projected belief; otherwise the slide at "now". At a fork the follower
    cannot see yet — sing this line again, or move on — the projection splits
    by the order prior alone, and that split is a guess, not a prediction."""
    idx = np.array([-1 if x is None else x for x in slides_at])
    out = []
    for a, n, m in zip(ahead, now, marg):
        if a is None or a == n:
            out.append(a)
            continue
        group = {a} | set(identical.get(a, ()))
        share = m[np.isin(idx, list(group))].sum() / max(1e-12, m.sum())
        out.append(a if share >= COMMIT else n)
    return out


def evaluate(song: cv.Song, rec, item: int, walls, pos, slides_at, shown_slides=None):
    gt_pos = song.tl["pos"]
    errs = []
    for t, c in zip(walls, pos):
        i = min(int(t * song.fps), len(gt_pos) - 1)
        if np.isfinite(gt_pos[i]) and song.tl["slide"][i] > 0:
            errs.append(abs((c + 0.5) * fl.CELL - gt_pos[i]))
    e = np.array(errs)
    decisions, shown = [], None
    for k, (t, c) in enumerate(zip(walls, pos)):
        sl = shown_slides[k] if shown_slides is not None else slides_at[c]
        if sl is not None and sl != shown:
            shown = sl
            decisions.append(Decision(t_seconds=float(t), item_idx=item, slide_idx=int(sl), source="ai"))
    wrong = wrong_slide_rate(decisions, rec.ground_truth, None, forgive_unpredictable=True,
                             setlist_content=rec.setlist_content, forgive_identical_slides=True)
    ant = anticipation_error_ms(decisions, rec.ground_truth)
    return float(np.mean(e <= 1.0)) if len(e) else np.nan, wrong, float(np.median(ant)) if ant else np.nan, len(decisions)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", default="wabn,rl,komh,isa")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--readout", default="slide")
    ap.add_argument("--ref", default="segment")
    ap.add_argument("--words", default="whisper")
    ap.add_argument("--weighting", default="flat")
    ap.add_argument("--score-latency", type=float, default=1.0)
    ap.add_argument("--score-shift", type=float, default=0.0)
    ap.add_argument("--aligned", action="store_true")
    ap.add_argument("--scorer", default="ct2")
    ap.add_argument("--word-extra-lag", type=float, default=0.0)
    ap.add_argument("--geom", default="")
    ap.add_argument("--tempo", default="")
    ap.add_argument("--tempo-weight", type=float, default=0.1)
    ap.add_argument("--tempo-sigma", type=float, default=0.06)
    ap.add_argument("--lookahead", type=float, default=0.0)
    ap.add_argument("--p-repeat", type=float, default=0.0)
    ap.add_argument("--commit", type=float, default=0.0)
    ap.add_argument("--spontaneous", default="")
    ap.add_argument("--spont-log", action="store_true")
    ap.add_argument("--no-clusters", action="store_true")
    ap.add_argument("--spont-kw", default="{}")
    ap.add_argument("--order", default="prior")
    ap.add_argument("--p-rate", type=float, default=0.02)
    ap.add_argument("--extend", action="store_true")
    args = ap.parse_args()
    global TEMPO, TEMPO_WEIGHT, LOOKAHEAD, P_REPEAT, COMMIT, SPONTANEOUS, SPONT_LOG
    TEMPO, TEMPO_WEIGHT, LOOKAHEAD, P_REPEAT, COMMIT = args.tempo, args.tempo_weight, args.lookahead, args.p_repeat, args.commit
    global TEMPO_SIGMA
    TEMPO_SIGMA = args.tempo_sigma
    SPONTANEOUS, SPONT_LOG = args.spontaneous, args.spont_log
    global CLUSTERS
    CLUSTERS = not args.no_clusters
    SPONT_KW.update(json.loads(args.spont_kw))
    global ORDER, P_RATE
    ORDER, P_RATE = args.order, args.p_rate
    global EXTEND
    EXTEND = args.extend
    global READOUT, REF, WORDS, WEIGHTING, SCORE_LATENCY, SCORE_SHIFT, ALIGNED, SCORER, WORD_EXTRA_LAG, GEOM
    READOUT = args.readout
    REF = args.ref
    WORDS = args.words
    WEIGHTING = args.weighting
    SCORE_LATENCY = args.score_latency
    SCORE_SHIFT = args.score_shift
    ALIGNED = args.aligned
    SCORER = args.scorer
    WORD_EXTRA_LAG = args.word_extra_lag
    GEOM = args.geom
    events = json.loads(fusion.EVENTS.read_text())
    table = {}
    for key in args.songs.split(","):
        song = cv.Song(key)
        rec = load_recording(cv.S / key)
        item = cv.stage1.song_item(song.s, cv.S / key)
        # the key from the first minute's chords, against the UNshifted sheet
        _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
        if REF == "segment":
            ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                                          sequences=[roadmap_sequence(song)], p_repeat=P_REPEAT, extend_to_next=EXTEND)
            smap = list(ref.slide_of)
            if ORDER == "oracle":
                ref.succ = oracle_order(song, rec, ref)
        else:
            ref = lead.LeadSheetReference(song.s, song.slides, key_shift=int(np.argmax(corr)))
            smap = slide_map(song, ref.P)
        T = len(song.ev["mix"][0])
        for arm in args.arms.split(","):
            t0 = time.time()
            if WORDS == "whisper":
                srcs = sources_for(song, ref, events[rec.id], ARMS[arm])
            else:
                fw = json.loads((cv.S / "fast_words.json").read_text())[key]["mix" if WORDS == "fast_mix" else "stem"]
                srcs = sources_for(song, ref, events[rec.id], ARMS[arm], word_events=[tuple(x) for x in fw],
                                   word_latency=1.0 if WORDS == "fast_mix" else 3.6)
            if TEMPO:
                srcs = srcs + [tempo_source(song, ref, T)]
            from auto_presenter.eval.metrics import identical_slide_groups
            ig = identical_slide_groups(rec.setlist_content)
            ident = {sl: {b for b in ig.get((item, sl), ())} for sl in range(len(song.slides))}
            gate = tr = None
            if SPONTANEOUS:
                gate, tr = spontaneous_gate(song, ref, ident, key)
            walls, pos, rate, marg = joint.follow(ref, srcs, T, lookahead_s=LOOKAHEAD, gate=gate, p_rate=P_RATE)
            if TEMPO_REPORT:
                tempo_report(song, key, walls)
            if tr is not None:
                spontaneous_report(tr, key, operator_deviations(song, rec, ident))
            shown = slide_readout(marg, smap, ident) if READOUT == "slide" else None
            if COMMIT and shown is not None:
                shown = committed(shown, slide_readout(joint.follow.marg_now, smap, ident), marg, smap, ident)
            table[(key, arm)] = evaluate(song, rec, item, walls, pos, smap, shown)
            r = table[(key, arm)]
            if SPONTANEOUS:
                fr, n, hits, caught, nd = SPONT_STATS[key]
                print(f"      spontaneous {fr:4.0%} of the time; {n} triggers, {hits} after a real deviation; caught {caught}/{nd} deviations")
            print(f"{key:5s} {arm:34s} within-beat {r[0]:5.1%}  wrong-slide {r[1]:.3f} (production {PRODUCTION.get(key, float('nan')):.3f})"
                  f"  anticip {r[2]:+6.0f} ms  {r[3]:3d} changes  [{time.time() - t0:.0f}s]", flush=True)
    keys = args.songs.split(",")
    print("\nmean over songs:")
    for arm in args.arms.split(","):
        rs = [table[(k, arm)] for k in keys]
        print(f"  {arm:34s} within-beat {np.nanmean([r[0] for r in rs]):5.1%}  wrong-slide {np.mean([r[1] for r in rs]):.3f}"
              f"  (production {np.mean([PRODUCTION.get(k, np.nan) for k in keys]):.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
