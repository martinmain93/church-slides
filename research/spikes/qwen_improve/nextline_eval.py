"""Ideas 2 and 3: use the lyrics safely. Score candidate next lines, and align.

Free context broke Qwen (it wrote unsung lyrics), so this spike only ever asks
closed questions about lines we already expect. It mirrors the app's
next-line check (docs/done/NEXT_LINE_CHECK.md) and its honest "v2" test:
windows are NOT aligned to the sung words, and there are no-change controls
mid-line, so false alarms are counted.

Check points, per JamendoLyrics song (hand word timings):
- change: the next line's first word started `d` s ago (d = 0.3 ... 2.0);
- control: mid-line, at least 0.3 s before the next line starts.

At each check the trailing `--window` s of audio is scored two ways:

**Closed-set scorer (idea 2).** Teacher-forced log-likelihood under Qwen3-ASR
of short transcripts of that window:
- `cand`: the current line's last 4 words, then the first k words of each of 3
  candidate lines (the true next line + 2 other lines of the song with
  different openings). No end token: the line goes on.
- `stay_eos`: the current line's last 4 words, then end-of-transcript.
- `stay_run`: the best 4-word run anywhere in the current line, then end.
"Selection" asks which candidate wins given a change; "detection" asks if the
best candidate beats staying by a margin, with the margin set so mid-line
controls fire at most 2% / 5% of the time.

**Forced aligner (idea 3).** Qwen3-ForcedAligner places every word of the
current line + the true next line in the window. Detection: the next line's
first word is placed at least `gap` s before the window end; position: words
placed before the window end vs words truly started.

    cd sidecar && PYTHONPATH=. $V ../research/spikes/qwen_improve/nextline_eval.py run --out <dir>
    ... nextline_eval.py report --out <dir>
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from common import MAIN6, SR, WORK, all_songs, audio_of, load_clip, load_qwen, pct, song_lines, tokenizer_for

DELAYS = (0.3, 0.5, 0.7, 1.0, 1.5, 2.0)
EOS = (151643, 151645)
TAIL = 4


# ------------------------------------------------------------------ checks


def candidates(lines, i: int) -> list[int]:
    """True next line i, then 2 other lines with distinct 2-word openings."""
    seen = {lines[i].key(2), lines[i - 1].key(2)}
    out = [i]
    order = list(range(i + 1, len(lines))) + list(range(0, i - 1))
    for j in order:
        k = lines[j].key(2)
        if k and k not in seen:
            seen.add(k)
            out.append(j)
        if len(out) == 3:
            break
    return out


def checks(lines, window: float):
    """(kind, d, t, cur_idx, next_idx) check points."""
    out = []
    for i in range(1, len(lines)):
        prev, cur = lines[i - 1], lines[i]
        if cur.key(3) == prev.key(3):
            continue  # a repeated line: "new line" and "same line" sound alike
        for d in DELAYS:
            t = cur.start + d
            if t - window >= 0:
                out.append(("change", d, t, i - 1, i))
        t = prev.start + 1.0
        while t <= cur.start - 0.3 and t <= prev.end + 0.2:
            if t - window >= 0:
                out.append(("control", round(t - prev.start, 2), t, i - 1, i))
            t += 1.0
    return out


# ------------------------------------------------------------------ scorer


class Scorer:
    def __init__(self, size: str, bits: int = 16) -> None:
        import mlx.core as mx

        self.mx = mx
        self.model = load_qwen(size, bits)
        self.tok = tokenizer_for(size)

    def prime(self, audio: np.ndarray):
        """Encode + prefill the prompt once; returns (cache, last logits, position)."""
        from mlx_qwen3_asr.audio import compute_features

        mx = self.mx
        mel, lens = compute_features(audio.astype(np.float32))
        feats, _ = self.model.audio_tower(mel.astype(mx.float16), lens)
        prompt = self.tok.build_prompt_tokens(n_audio_tokens=int(feats.shape[1]), language="English", context="")
        ids = mx.array([prompt])
        pos = mx.arange(len(prompt))[None, :]
        cache = self.model.create_cache()
        logits = self.model.prefill(ids, feats, mx.stack([pos, pos, pos], axis=1), cache)
        mx.eval(logits)
        return cache, logits, len(prompt)

    def logprob(self, primed, text: str, eos: bool) -> tuple[float, list[float]]:
        """log P(text [+ end]) and the cumulative log P after each word."""
        mx = self.mx
        cache, last, start = primed
        words = text.split()
        toks: list[int] = []
        word_end_tok: list[int] = []
        for w_i in range(len(words)):
            toks = self.tok.encode(" ".join(words[: w_i + 1]))
            word_end_tok.append(len(toks))
        c = cache.fork()
        pos = mx.arange(start, start + len(toks))[None, :]
        lg = self.model.step_many(mx.array([toks]), mx.stack([pos, pos, pos], axis=1), c)
        allg = mx.concatenate([last, lg], axis=1)[0]  # (n+1, V): row j predicts token j
        lsm = allg - mx.logsumexp(allg, axis=-1, keepdims=True)
        tok_lp = np.array(lsm[mx.arange(len(toks)), mx.array(toks)].astype(mx.float32))
        cum = np.cumsum(tok_lp)
        per_word = [float(cum[e - 1]) for e in word_end_tok]
        total = float(cum[-1])
        if eos:
            e = np.array(lsm[len(toks)].astype(mx.float32))
            total += float(np.logaddexp(e[EOS[0]], e[EOS[1]]))
        return total, per_word


def score_check(sc: Scorer, primed, lines, cur: int, cands: list[int]) -> dict:
    cl = lines[cur]
    tail = " ".join(cl.words[-TAIL:])
    stay_eos, _ = sc.logprob(primed, tail, eos=True)
    runs = [" ".join(cl.words[j : j + TAIL]) for j in range(max(1, len(cl.words) - TAIL + 1))]
    stay_run = max(sc.logprob(primed, r, eos=True)[0] for r in runs)
    cand = []
    n_tail = len(tail.split())
    for j in cands:
        _, per_word = sc.logprob(primed, tail + " " + lines[j].opening(3), eos=False)
        base = per_word[n_tail - 1]
        cand.append({"line": j, "tail": base, "k": per_word[n_tail:]})
    return {"stay_eos": stay_eos, "stay_run": stay_run, "cand": cand}


# ----------------------------------------------------------------- aligner


class Aligner:
    def __init__(self) -> None:
        import mlx_qwen3_asr as q

        self.fa = q.ForcedAligner()
        self.fa._ensure_loaded()
        self.be = self.fa._backend

    def align(self, audio: np.ndarray, words: list[str]):
        """Word (start, end) in window seconds plus the start token's confidence."""
        import mlx.core as mx
        from mlx_qwen3_asr.audio import compute_features
        from mlx_qwen3_asr.forced_aligner import ForcedAlignTextProcessor as P

        be = self.be
        mel, lens = compute_features(audio.astype(np.float32))
        feats, _ = be.model.audio_tower(mel.astype(be.dtype), lens)
        ids = mx.array([be.tokenizer.encode(be._build_prompt(words, int(feats.shape[1])), add_special_tokens=False)])
        pos = mx.arange(ids.shape[1])[None, :]
        emb = be.model.model.embed_tokens(ids)
        emb = be.model._inject_audio_features(emb, feats.astype(emb.dtype), ids == be.model.audio_token_id)
        hid = be.model.model(inputs_embeds=emb, position_ids=mx.stack([pos, pos, pos], axis=1), attention_mask=None, cache=None)
        lg = be.model.lm_head(hid)[0]
        mask = np.array(ids[0]) == int(be.timestamp_token_id)
        probs = mx.softmax(lg.astype(mx.float32), axis=-1)
        pred = np.array(mx.argmax(lg, axis=-1))[mask]
        conf = np.array(mx.max(probs, axis=-1))[mask]
        ms = pred.astype(np.float32) * float(be.timestamp_segment_time)
        aligned = P.parse_timestamp_ms(words, ms)
        return [(a.start_time, a.end_time) for a in aligned], conf[0::2].tolist()


def align_check(al: Aligner, audio: np.ndarray, lines, cur: int, nxt: int) -> dict:
    from mlx_qwen3_asr.forced_aligner import ForcedAlignTextProcessor as P

    a = P.tokenize_space_lang(lines[cur].text)
    b = P.tokenize_space_lang(lines[nxt].text)
    spans, conf = al.align(audio, a + b)
    return {"n_cur": len(a), "spans": spans, "conf": conf}


# --------------------------------------------------------------------- run


def cmd_run(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "checks.jsonl"
    done = set()
    if path.exists():
        for ln in path.read_text().splitlines():
            r = json.loads(ln)
            done.add((r["song"], r["kind"], r["t"]))
    sc = Scorer(args.model, args.bits) if "scorer" in args.parts else None
    al = Aligner() if "aligner" in args.parts else None
    songs = all_songs() if args.songs == "all" else (MAIN6 if args.songs == "main6" else args.songs.split(","))
    with path.open("a") as f:
        for name in songs:
            lines = song_lines(name)
            audio = audio_of(load_clip(name))
            pts = checks(lines, args.window)
            t0 = time.monotonic()
            for kind, d, t, cur, nxt in pts:
                if (name, kind, round(t, 3)) in done:
                    continue
                win = audio[int((t - args.window) * SR) : int(t * SR)]
                row = {"song": name, "kind": kind, "d": d, "t": round(t, 3), "cur": cur, "next": nxt,
                       "win_start": t - args.window, "true_next_start": lines[nxt].start}
                if sc:
                    a = time.monotonic()
                    primed = sc.prime(win)
                    row["scorer"] = score_check(sc, primed, lines, cur, candidates(lines, nxt))
                    row["scorer_s"] = time.monotonic() - a
                if al:
                    a = time.monotonic()
                    row["aligner"] = align_check(al, win, lines, cur, nxt)
                    row["aligner_s"] = time.monotonic() - a
                f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"{name}: {len(pts)} checks ({time.monotonic() - t0:.0f}s)", flush=True)


# ------------------------------------------------------------------ report


def kv(c: dict, k: int) -> float:
    """Cumulative log P of the first k opening words (fewer if the line is shorter)."""
    return c["k"][min(k, len(c["k"])) - 1]


def _det_table(rows, margin_fn, label: str, fa_targets=(0.02, 0.05)) -> list[str]:
    ctrl = [margin_fn(r)[0] for r in rows if r["kind"] == "control"]
    out = [f"\n{label}: {len(ctrl)} mid-line controls"]
    hdr = "FA cap  thr     " + "".join(f"+{d:<6}" for d in DELAYS)
    out.append(hdr)
    for cap in fa_targets:
        thr = float(np.quantile(ctrl, 1 - cap)) if ctrl else 0.0
        cells = []
        for d in DELAYS:
            ch = [margin_fn(r) for r in rows if r["kind"] == "change" and r["d"] == d]
            hit = sum(1 for m, right in ch if m > thr and right)
            cells.append(f"{hit / max(1, len(ch)):>6.0%} ")
        out.append(f"{cap:>5.0%}  {thr:>6.2f}  " + "".join(cells))
    return out


def cmd_report(args) -> None:
    rows = [json.loads(ln) for ln in (Path(args.out) / "checks.jsonl").read_text().splitlines()]
    lines_out = []
    n_ch = sum(r["kind"] == "change" for r in rows)
    lines_out.append(f"{len(rows)} checks: {n_ch} at line changes, {len(rows) - n_ch} mid-line controls, "
                     f"{len({r['song'] for r in rows})} songs")
    sr = [r for r in rows if "scorer" in r]
    if sr:
        lines_out.append(f"scorer time per check p50 {pct([r['scorer_s'] for r in sr], .5) * 1000:.0f} ms, "
                         f"p90 {pct([r['scorer_s'] for r in sr], .9) * 1000:.0f} ms")
        for k in (1, 2, 3):
            cells = []
            for d in DELAYS:
                ch = [r for r in sr if r["kind"] == "change" and r["d"] == d]
                win = sum(1 for r in ch if max(r["scorer"]["cand"], key=lambda c: kv(c, k))["line"] == r["next"])
                cells.append(f"{win / max(1, len(ch)):>6.0%} ")
            lines_out.append(f"selection, {k} opening word(s), 3 candidates:  " + "".join(cells))

        def m_eos(k):
            # both share the tail's log P; compare P(opening | tail) with P(end | tail)
            return lambda r: (max(kv(c, k) for c in r["scorer"]["cand"]) - r["scorer"]["stay_eos"],
                              max(r["scorer"]["cand"], key=lambda c: kv(c, k))["line"] == r["next"])

        def m_run(k):
            # opening after the tail vs the best run of the current line, both whole transcripts
            return lambda r: (max(kv(c, k) for c in r["scorer"]["cand"]) - r["scorer"]["stay_run"],
                              max(r["scorer"]["cand"], key=lambda c: kv(c, k))["line"] == r["next"])

        for k in (1, 2):
            lines_out += _det_table(sr, m_eos(k), f"detect (right line), scorer vs stay_eos, k={k}")
            lines_out += _det_table(sr, m_run(k), f"detect (right line), scorer vs stay_run, k={k}")
    ar = [r for r in rows if "aligner" in r]
    if ar:
        lines_out.append(f"\naligner time per check p50 {pct([r['aligner_s'] for r in ar], .5) * 1000:.0f} ms, "
                         f"p90 {pct([r['aligner_s'] for r in ar], .9) * 1000:.0f} ms")
        W = args.window

        def first_next(r):
            a = r["aligner"]
            if len(a["spans"]) <= a["n_cur"]:
                return None, 0.0
            return a["spans"][a["n_cur"]][0], a["conf"][a["n_cur"]]

        # detection margin: how far before the window end the next line's first word was placed
        def m_al(r):
            s, _ = first_next(r)
            return ((W - s) if s is not None else -9.0), True

        lines_out += _det_table(ar, m_al, "detect, aligner: next line's first word placed this far before window end (s)")

        def m_alc(r):
            s, c = first_next(r)
            return ((W - s) * c if s is not None else -9.0), True

        lines_out += _det_table(ar, m_alc, "detect, aligner, margin x start-token confidence")
        # timing of the next line's first word, when it has started
        cells = []
        for d in DELAYS:
            errs = []
            for r in ar:
                if r["kind"] == "change" and r["d"] == d:
                    s, _ = first_next(r)
                    if s is not None:
                        errs.append(r["win_start"] + s - r["true_next_start"])
            cells.append(f"{pct([abs(e) for e in errs], .5):>5.2f}/{np.median(errs) if errs else float('nan'):+.2f} ")
        lines_out.append("\naligner first-word time |err| p50 / bias (s) by delay:  " + "".join(cells))
        # where-in-line: words placed before (end - 0.1 s) vs words truly started
        from mlx_qwen3_asr.forced_aligner import ForcedAlignTextProcessor as P  # noqa: F401
        werr = []
        for r in ar:
            song_l = song_lines(r["song"])
            cur, nxt = song_l[r["cur"]], song_l[r["next"]]
            starts = [s for s, n in zip(cur.starts + nxt.starts, cur.norms + nxt.norms) if n]
            true_n = sum(1 for s in starts if s < r["t"])
            pred_n = sum(1 for s, _ in r["aligner"]["spans"] if s < W - 0.1)
            if len(starts) == len(r["aligner"]["spans"]):
                werr.append(pred_n - true_n)
        if werr:
            werr_a = np.array(werr)
            lines_out.append(f"position in current+next line (words sung so far): |err| p50 {np.median(np.abs(werr_a)):.1f} "
                             f"words, exact {np.mean(werr_a == 0):.0%}, within 1 {np.mean(np.abs(werr_a) <= 1):.0%}, "
                             f"bias {np.median(werr_a):+.1f}")
    text = "\n".join(lines_out)
    print(text)
    (Path(args.out) / "report.txt").write_text(text + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", default=str(WORK / "runs" / "nextline"))
    r.add_argument("--songs", default="main6", help="main6, all, or a comma list")
    r.add_argument("--model", default="0.6b")
    r.add_argument("--bits", type=int, default=16)
    r.add_argument("--window", type=float, default=4.0)
    r.add_argument("--parts", default="scorer,aligner")
    p = sub.add_parser("report")
    p.add_argument("--out", default=str(WORK / "runs" / "nextline"))
    p.add_argument("--window", type=float, default=4.0)
    args = ap.parse_args()
    {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
