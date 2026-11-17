"""Fast loop: replay live follower traces with the music evidence swapped.

    uv run --project sidecar python research/spikes/music_only/music_replay.py ARMS [CASES]

Each arm is `name:key=value,...` with keys
  chords = live | mix | stems | none      (live = the run's own BTC frames)
  melody = none | mix | stems             (offline Basic Pitch, pitch-class salience)
  cw, mw = multipliers on CHORD_WEIGHT / MELODY_WEIGHT
  words  = 1 | 0, scored = 1 | 0         (drop the run's free words / scored spans)
  key    = live | offline                 (offline: the live algorithm on offline chords)
  hold   = seconds | none                 (LYRIC_HOLD_S)
  wait   = enter share | none             (WAIT_ENTER: the band-waits states)
  wl     = WAIT_LEAVE
  wait_any = 1: wait states even with no vocal source (else only with one)
  vocal  = none | stems | scored          (vocal activity: Demucs vocal-stem
           RMS, or the scorer's nothing-heard steps with no free words)
  vlat   = seconds the vocal activity arrives after its audio (stems: 3)
  vw, vhit, vfalse, vdb = VOCAL_WEIGHT, VOCAL_HIT, VOCAL_FALSE, stem dB threshold
Knobs for experiments refuted and removed from the follower (blank-when-lost,
chords within a slide, per-slide jump shares, the "line" channel) are rejected:
research/dead_ends/follower_switches/README.md has their numbers.
Song changes come from the trace (what the production engine did live), so
only the follower varies. Minutes, not the 90 of a real-time run; confirm the
winner with research/eval/run_follower_eval.py.
"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag_live_trace as d
import follow as fl
import features as ft
import multi_replay as mr
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.metrics import anticipation_error_ms, identical_slide_groups, wrong_slide_rate
from auto_presenter.eval.types import Decision
from auto_presenter.follower import online, sheet
from auto_presenter.follower import score as fsc
from auto_presenter.follower.online import OnlineFollower
from auto_presenter.follower.reference import SegmentReference
from auto_presenter.follower.scorer import spans_for

SPK = mr.SPK
FEATS = {"wabn": "wabn", "rl": "rl", "komh": "komh", "isa": "isa", "since": "neg_214054_974",
         "bml": "bml2", "medley": "nbtb", "set": "set1002"}
CHORD_LAT, MEL_LAT, FPS = 1.0, 1.0, 10.0
_feat_cache = {}
BASE_CW, BASE_MW = online.CHORD_WEIGHT, online.MELODY_WEIGHT


def feats(name, src, dur):
    k = (name, src)
    if k not in _feat_cache:
        F = ft.load(SPK / f"{FEATS[name]}_feats.npz")
        if f"{src}_pitch" not in F:
            raise SystemExit(f"{name}: no {src} features")
        _t, pcs, ch = fl.frame_evidence(F, src, FPS, min(dur, float(F["duration"])))
        _feat_cache[k] = (pcs, ch)
    return _feat_cache[k]


def vocal_activity(name, dur, db_thr=-45.0):
    """P(singing) per 0.1 s frame from the offline Demucs vocal stem's RMS:
    the loudest frame within +-0.5 s (breaths are not pauses), soft threshold."""
    k = (name, "vocal", db_thr)
    if k not in _feat_cache:
        F = ft.load(SPK / f"{FEATS[name]}_feats.npz")
        if "stems_vocal_rms" not in F:
            _feat_cache[k] = None
        else:
            db = 20 * np.log10(F["stems_vocal_rms"].astype(np.float64) + 1e-6)
            n = len(db)
            pad = np.pad(db, 5, constant_values=-120)
            mx = np.max(np.stack([pad[i : i + n] for i in range(11)]), axis=0)
            _feat_cache[k] = 1 / (1 + np.exp(-(mx - db_thr) / 3.0))
    return _feat_cache[k]


def scored_activity(rows, item, owner, spans, null=-3.0, words=True, hi=0.8, lo=0.2):
    """P(singing) per 0.1 s frame from the live scorer alone: a step whose
    best span reached the null level, or any free word in its window, says
    singing; a step with neither says (weakly) nobody sings."""
    word_t = sorted(r["t"] for r in rows if r["e"] == "word" and r.get("item") in (None, item))
    out = []  # (t_audio, t_arrival, p)
    for r in rows:
        if r["e"] != "scored" or r.get("item") not in (None, item):
            continue
        sc_ = np.asarray(r["scores"])
        best = max((sc_[spans.index(sp)] for sw, sp in owner.items() if sw[1] > 0), default=-99)
        a, b = r["win_end"] - 6.0, r["win_end"]
        heard_w = words and any(a <= t < b for t in word_t)
        p = hi if best >= null or heard_w else lo
        # the newest 2 s of the window (older parts are covered by earlier steps)
        for fr in range(int((b - 2.0) * FPS), int(b * FPS)):
            out.append((fr / FPS, r["arr"], p))
    return out


def _slide_text(owner) -> dict:
    """slide -> " its words " (from the spans' owner map: the longest span ending at each word)."""
    words: dict[int, dict[int, str]] = {}
    for (sl, j), sp in owner.items():
        words.setdefault(sl, {})[j] = sp.split()[-1]
    return {sl: " " + " ".join(w[k] for k in sorted(w)) + " " for sl, w in words.items()}


#: (follower, read-out, t, case name) -> None, called after every read-out:
#: the next-line check (scripts set it; it needs the recording's audio).
NEXT_LINE_HOOK = None


def replay_item(rows, ref, owner, spans, ident, item, arm, name, dur, slides=None):
    va = vocal_activity(name, dur, float(arm["vdb"])) if arm["vocal"] == "stems" else None
    has_vocal = va is not None or arm["vocal"] == "scored" or arm["wait_any"]
    f = OnlineFollower(ref, identical=ident, vocal_source=has_vocal, lookahead_s=float(arm["la"]))
    evidence, control = [], []
    if arm["vocal"] == "stems":
        if va is not None:
            for fr in range(len(va)):
                evidence.append((fr / FPS + float(arm["vlat"]), -1, "vocal", (fr / FPS, float(va[fr]))))
    elif arm["vocal"] == "scored":
        for t, arr, p in scored_activity(rows, item, owner, spans):
            evidence.append((arr, -1, "vocal", (t, p)))
    def lm_bias(window, n, sps):
        """span text -> its silence/noise score minus the song's median (Whisper's LM preference)."""
        import scorer_prior as spr
        v = np.asarray(spr.load(name, window, n)[item], float)
        assert len(v) == len(sps), (len(v), len(sps))
        return dict(zip(sps, v - np.median(v)))
    bias1 = lm_bias(6.0, 6, spans) if float(arm.get("prior", 0)) else None
    if arm.get("dual"):
        # a second, short-window scorer (scorer_cache.py rows), arriving
        # `dlat` after its window ends
        import scorer_cache as scc
        dspan = int(arm.get("dspan", 6))
        own2 = spans_for(slides, dspan) if dspan != 6 else owner
        sp2 = sorted(set(own2.values()))
        for r in scc.load_rows(name, float(arm["dual"]), float(arm.get("dstep", 1.0)), dspan):
            if r.get("item") != item:
                continue
            arr2 = r["t"] if arm.get("zl") else r["win_end"] + float(arm.get("dlat", 0.4))
            sc2 = list(r["scores"])
            if float(arm.get("dprior", 0)):
                b2 = lm_bias(float(arm["dual"]), dspan, sp2)
                sc2 = [x - float(arm["dprior"]) * b2[t] for x, t in zip(sc2, sp2)]
            evidence.append((arr2, -2, "scored2", (r["t"], {sw: sc2[sp2.index(v)] for sw, v in own2.items()})))
    for k, r in enumerate(rows):
        if r.get("item") not in (None, item) and r["e"] != "enter":
            continue
        e = r["e"]
        # zero lag ("zl"): every piece of evidence known the moment its audio happened
        if e == "word" and arm["words"]:
            evidence.append((r["t"] if arm.get("zl") else r["arr"], k, "word", (r["t"], r["text"])))
        elif e == "scored" and arm["scored"]:
            # "sdel": the scorer's evidence arrives this much sooner (never before its audio)
            arr_s = r["t"] if arm.get("zl") else max(r["t"], r["arr"] - float(arm.get("sdel", 0.0)))
            evidence.append((arr_s, k, "scored", (r["t"], r["scores"])))
        elif e == "chords" and arm["chords"] == "live":
            for i in range(r["n"]):
                tc = r["t"] - (r["n"] - 1 - i) * 0.1
                evidence.append((tc if arm.get("zl") else r["arr"], k, "chord", (tc, np.asarray(r["probs"], np.float32))))
        elif e in ("enter", "key", "shown", "readout"):
            control.append((r["t"], k, r))
    if arm["chords"] in ("mix", "stems") or arm["melody"] in ("mix", "stems"):
        t0 = min(r["t"] for r in rows if r["e"] == "readout" and r.get("item") == item) if any(
            r["e"] == "readout" and r.get("item") == item for r in rows) else 0.0
        t1 = max(r["t"] for r in rows if r["e"] == "readout" and r.get("item") == item) if any(
            r["e"] == "readout" and r.get("item") == item for r in rows) else 0.0
        lo, hi = int(t0 * FPS), int(t1 * FPS) + 1
        if arm["chords"] in ("mix", "stems"):
            _pcs, ch = feats(name, arm["chords"], dur)
            for fr in range(lo, min(hi, len(ch))):
                evidence.append((fr / FPS + CHORD_LAT, -1, "chord", (fr / FPS, ch[fr])))
        if arm["melody"] in ("mix", "stems"):
            pcs, _ch = feats(name, arm["melody"], dur)
            for fr in range(lo, min(hi, len(pcs))):
                evidence.append((fr / FPS + MEL_LAT, -1, "mel", (fr / FPS, pcs[fr])))
    evidence.sort(key=lambda x: (x[0], x[1]))
    control.sort(key=lambda x: (x[0], x[1]))
    off_ch = feats(name, "mix", dur)[1] if arm["key"] == "offline" else None
    out, ei, last = [], 0, None
    for t, _k, r in control:
        e = r["e"]
        if e == "enter":
            if r["item"] == item:
                # "mid": a song entered mid-way (t > 0, onto a sung slide) spreads
                # the belief over that slide (`OnlineFollower.anchor(mid_slide=)`)
                mid = bool(int(arm.get("mid", 0))) and r["t"] > 0 and r["slide"] not in (None, 0)
                f.anchor(r["slide"], r["t"], mid_slide=mid)
            continue
        if e == "key":
            k = r["k"]
            if off_ch is not None:
                a, b = max(0, int((t - 60) * FPS)), int((t - CHORD_LAT) * FPS)
                k, _ = sheet.estimate_key_shift(off_ch[a:b], ref.chord_sheet)
            ref.set_key(int(k))
            f.music_enabled = True
            continue
        if e == "shown":
            f.shown = r["slide"]
            continue
        while ei < len(evidence) and evidence[ei][0] <= t + 1e-9:
            arr, _o, kind, p = evidence[ei]
            if kind == "word":
                f.push_word(p[0], arr, p[1])
            elif kind in ("scored", "scored2"):
                score = (p[1] if kind == "scored2" else
                         {sw: p[1][i] for sw, sp in owner.items() for i in [spans.index(sp)]})
                if kind == "scored" and bias1 is not None:
                    score = {sw: v - float(arm["prior"]) * bias1[owner[sw]] for sw, v in score.items()}
                if kind == "scored2" and int(arm.get("dcand", 0)) and last is not None:
                    # only the slides holding belief and the ones after them
                    m = last.marginal_now
                    sl = f._slide_idx[: len(m)]
                    mass = np.bincount(sl[sl >= 0], weights=m[sl >= 0], minlength=f._n_slides)
                    held = {int(x) for x in np.where(mass >= float(arm.get("deps", 0.05)) * mass.sum())[0]}
                    cands = held | {x + 1 for x in held}
                    cands |= {y for x in list(cands) for y in ident.get(x, ())}
                    score = {sw: v for sw, v in score.items() if sw[0] in cands}
                vec = ref.scored(score)
                heard = ref.heard(score)
                if vec is not None:
                    w = arm["sw"] * online.SCORED_WEIGHT * (ref.reliability(score) if arm["rel"] == "" else 1.0)
                    if kind == "scored2":
                        w *= float(arm.get("dw", 1.0))
                    if arm["rel"] not in ("", "off"):
                        w = online.SCORED_WEIGHT
                        lo_, hi_ = (float(x) for x in arm["rel"].split("~"))
                        best = max((v for (_si, j), v in score.items() if j > 0), default=-99)
                        w *= float(np.clip((best - lo_) / (hi_ - lo_), 0.0, 1.0))
                    ent = ref.step_entropy(score)
                    if kind == "scored2" and not int(arm.get("djump", 1)):
                        ent = None  # evidence only: proposes no jump
                    f.push_scored(p[0], arr, vec, weight=w, heard=heard, entropy=ent)
                    if heard:
                        best = max(score, key=score.__getitem__)
                        # every slide whose words contain the heard words, not only the
                        # slide the span was built from
                        f.note_heard_line(arr, {sl for sl, ws in _slide_text(owner).items() if f" {owner[best]} " in ws})
                elif heard:
                    f.push_heard(arr)
            elif kind == "chord":
                f.push_chords(p[0], arr, p[1])
            elif kind == "vocal":
                f.push_vocal(p[0], arr, p[1])
            else:
                f.push_melody(p[0], arr, p[1])
            ei += 1
        res = last = f.readout(t)
        if NEXT_LINE_HOOK is not None:
            NEXT_LINE_HOOK(f, res, t, name)
        if res.slide is not None and res.slide != f.shown:
            f.shown = res.slide
        out.append((t, res.slide, r.get("slide")))
    return out


def run(name, arm):
    dec, rec = decisions(name, arm)
    w = wrong_slide_rate(dec, rec.ground_truth, None, forgive_unpredictable=True,
                         setlist_content=rec.setlist_content, forgive_identical_slides=True)
    a = anticipation_error_ms(dec, rec.ground_truth)
    ch = [(x.t_seconds, (x.item_idx, x.slide_idx)) for x in dec]
    flick = sum(1 for i in range(2, len(ch)) if ch[i][1] == ch[i-2][1] and ch[i][0] - ch[i-2][0] <= 5.0)
    return w, float(np.median(a)) if a else float("nan"), len(dec), flick


def decisions(name, arm):
    """The follower's screen decisions for a recording (song changes from the trace)."""
    rdir, trace, sheets = mr.CASES[name]
    rows = d.load_trace(trace)
    rec = load_recording(rdir)
    dur = max(r["t"] for r in rows) + 5
    ig = identical_slide_groups(rec.setlist_content)
    enters = sorted((r["t"], r["item"], r["slide"]) for r in rows if r["e"] == "enter")
    end = max(r["t"] for r in rows)
    spans_on = [(t, enters[i + 1][0] if i + 1 < len(enters) else end + 1, it) for i, (t, it, _s) in enumerate(enters)]
    decisions = []
    for item, pdf in sheets.items():
        snap = [x for x in rec.setlist_content if x.item_idx == item][0]
        ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(snap.slides))}
        owner = spans_for(snap.slides); spans = sorted(set(owner.values()))
        ref = SegmentReference(fsc.from_extract(mr.sheet(pdf)), snap.slides, list(snap.slide_sections))
        prev = None
        for t, slide, _ in replay_item(rows, ref, owner, spans, ident, item, arm, name, dur, snap.slides):
            if slide is None or slide == prev:
                continue
            prev = slide
            if any(a <= t < b and it == item for a, b, it in spans_on):
                decisions.append((t, item, int(slide)))
    decisions += [(t, it, s) for t, it, s in enters if t > 0]
    decisions.sort()
    dec, shown = [], None
    for t, it, s in decisions:
        if (it, s) != shown:
            shown = (it, s); dec.append(Decision(t_seconds=t, item_idx=it, slide_idx=s, source="ai"))
    return dec, rec


DEFAULT = {"hhl": online.HOLD_HEARD_LINE_S, "mid": 1, "la": OnlineFollower.lookahead_s, "fl": int(online.FIRST_LINE_UNHELD), "col": online.COLLAPSE_SHARE, "cl": online.COLLAPSE_LEAD, "tag": online.TAG_REPEAT, "tj": online.TARGETED_JUMP, "js": online.JUMP_SHARPNESS, "aj": 0.0, "mode": "legacy", "eps": 0.01, "beta": 2.0, "sw": 1.0, "wait_any": 0,
           "wait": online.WAIT_ENTER, "wl": online.WAIT_LEAVE, "vocal": "none", "vlat": 3.0,
           "vw": online.VOCAL_WEIGHT, "vhit": online.VOCAL_HIT, "vfalse": online.VOCAL_FALSE, "vdb": -45.0,
           "hms": online.HOLD_MIN_SHARE, "rel": "", "chords": "live", "melody": "none", "cw": 1.0, "mw": 1.0, "words": 1, "scored": 1, "key": "live", "hold": online.LYRIC_HOLD_S}


def apply(arm):
    from auto_presenter.follower import reference as _ref
    online.ANYWHERE_JUMP = arm["aj"]
    online.TAG_REPEAT = arm["tag"]
    online.FIRST_LINE_UNHELD = bool(arm["fl"])
    online.HOLD_HEARD_LINE_S = arm["hhl"]
    online.COLLAPSE_SHARE = arm["col"]
    online.COLLAPSE_LEAD = arm["cl"]
    online.TARGETED_JUMP = arm["tj"]
    online.JUMP_SHARPNESS = arm["js"]
    _ref.SCORED_MODE = arm["mode"]
    _ref.ENTROPY_EPS_MIN = arm["eps"]
    _ref.ENTROPY_BETA = arm["beta"]
    online.WAIT_ENTER = arm["wait"]
    online.WAIT_LEAVE = arm["wl"]
    online.VOCAL_WEIGHT = arm["vw"]
    online.VOCAL_HIT = arm["vhit"]
    online.VOCAL_FALSE = arm["vfalse"]


def env_arm():
    """The arm in $ARM (a music_replay arm body, e.g. "wait=0.3,vocal=stems"),
    applied to the follower's constants; the default arm when unset."""
    import os
    _n, arm = parse("env:" + os.environ.get("ARM", ""))
    online.LYRIC_HOLD_S = arm["hold"]
    online.HOLD_MIN_SHARE = arm["hms"]
    online.CHORD_WEIGHT = BASE_CW * arm["cw"]
    online.MELODY_WEIGHT = BASE_MW * arm["mw"]
    apply(arm)
    return arm


#: Knobs whose follower code was removed (research/dead_ends/follower_switches).
REMOVED = {"blost", "bbest", "cws", "jps", "line", "lw", "lhit", "lfalse"}


def parse(spec):
    name, _, body = spec.partition(":")
    arm = dict(DEFAULT)
    for kv in filter(None, body.split(",")):
        k, v = kv.split("=")
        if k in REMOVED:
            raise SystemExit(f"{k}: that experiment was removed from the follower "
                             "(research/dead_ends/follower_switches/README.md)")
        arm[k] = (None if v == "none" and k in ("hold", "wait") else
                  float(v) if k in ("hhl", "prior", "dprior", "deps", "dual", "dstep", "dlat", "dw", "sdel", "la", "col", "cl", "tag", "tj", "js", "eps", "beta", "sw", "aj", "cw", "mw", "hold", "hms", "wait", "wl", "vlat", "vw", "vhit", "vfalse", "vdb") else int(v) if k in ("mid", "dspan", "dcand", "djump", "fl", "words", "scored", "wait_any") else v)
    return name, arm


if __name__ == "__main__":
    arms = [parse(s) for s in sys.argv[1].split(";")]
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else list(mr.CASES)
    for aname, arm in arms:
        online.CHORD_WEIGHT = BASE_CW * arm["cw"]
        online.MELODY_WEIGHT = BASE_MW * arm["mw"]
        online.LYRIC_HOLD_S = arm["hold"]
        online.ANYWHERE_JUMP = arm["aj"]
        online.TARGETED_JUMP = arm["tj"]
        online.JUMP_SHARPNESS = arm["js"]
        from auto_presenter.follower import reference as _ref
        _ref.SCORED_MODE = arm["mode"]
        _ref.ENTROPY_EPS_MIN = arm["eps"]
        _ref.ENTROPY_BETA = arm["beta"]
        online.HOLD_MIN_SHARE = arm["hms"]
        apply(arm)
        res = {n: run(n, arm) for n in names}
        tune = [res[n][0] for n in names if n not in mr.HELD_OUT]
        held = [res[n][0] for n in names if n in mr.HELD_OUT]
        f = lambda xs: f"{np.mean(xs):.3f}" if xs else "  -  "
        print(f"{aname:14s} tuning {f(tune)}  held-out {f(held)} | "
              + "  ".join(f"{n} {r[0]:.3f}/{r[3]}fl" for n, r in res.items()), flush=True)
