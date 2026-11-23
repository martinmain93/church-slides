"""Control for idea 2: the same closed-set next-line test, scored by Whisper.

Re-scores the exact check points `nextline_eval.py run` wrote (same songs,
times, windows and candidates) with Whisper small.en, the model behind the
app's span scorer and next-line check, so the Qwen numbers have a like-for-like
baseline. Same quantities (sums of token log-probabilities, end-of-text for
"stay"), written in the same row format so `nextline_eval.py report` reads it.

Runs in the app's sidecar venv (mlx_whisper):

    cd sidecar && PYTHONPATH=. ~/Development/church-slides/sidecar/.venv/bin/python \
        ../research/spikes/qwen_improve/whisper_nextline.py --checks <qwen run>/checks.jsonl --out <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from common import SR, audio_of, load_clip, song_lines  # noqa: E402
from nextline_eval import TAIL, candidates  # noqa: E402


class WhisperLP:
    def __init__(self, size: str, window: float) -> None:
        import mlx.core as mx
        from auto_presenter.asr.mlx_backend import local_snapshot, repo_for
        from auto_presenter.follower.scorer import MLXSpanScorer

        self.mx = mx
        self.sc = MLXSpanScorer(local_snapshot(repo_for(size)), audio_ctx=int(window * 50))
        self.model = self.sc._load()
        self.tok = self.sc._tok
        self.sot = list(self.sc._sot)
        self.eot = self.tok.eot

    def prime(self, audio: np.ndarray):
        xa = self.sc._encode(audio)
        dec = self.model.decoder
        cross = [(b.cross_attn.key(xa), b.cross_attn.value(xa)) for b in dec.blocks]
        return xa, cross

    def logprob(self, primed, text: str, eos: bool) -> tuple[float, list[float]]:
        mx = self.mx
        xa, cross = primed
        words = text.split()
        ends, toks = [], []
        for i in range(len(words)):
            toks = self.tok.encode(" " + " ".join(words[: i + 1]))
            ends.append(len(toks))
        seq = self.sot + toks + ([self.eot] if eos else [])
        dec = self.model.decoder
        x = mx.array(np.asarray([seq], np.int32))
        h = dec.token_embedding(x) + dec.positional_embedding[: x.shape[1]]
        for blk, kv in zip(dec.blocks, cross, strict=True):
            h, _, _ = blk(h, xa, mask=dec._mask, kv_cache=(None, kv))
        n0 = len(self.sot)
        h = dec.ln(h[:, n0 - 1 : len(seq) - 1, :])
        lg = dec.token_embedding.as_linear(h).astype(mx.float32)[0]
        tgt = mx.array(np.asarray(seq[n0:], np.int32))
        lp = np.array(mx.take_along_axis(lg, tgt[:, None], axis=-1)[:, 0] - mx.logsumexp(lg, axis=-1))
        cum = np.cumsum(lp)
        per_word = [float(cum[e - 1]) for e in ends]
        return float(cum[-1]), per_word


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="small.en")
    ap.add_argument("--window", type=float, default=4.0)
    args = ap.parse_args()
    w = WhisperLP(args.size, args.window)
    rows = [json.loads(ln) for ln in Path(args.checks).read_text().splitlines()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache: dict[str, tuple] = {}
    with (out / "checks.jsonl").open("w") as f:
        for r in rows:
            if r["song"] not in cache:
                cache[r["song"]] = (song_lines(r["song"]), audio_of(load_clip(r["song"])))
            lines, audio = cache[r["song"]]
            t = r["t"]
            win = audio[int((t - args.window) * SR) : int(t * SR)]
            a = time.monotonic()
            primed = w.prime(win)
            cl = lines[r["cur"]]
            tail = " ".join(cl.words[-TAIL:])
            stay_eos, _ = w.logprob(primed, tail, eos=True)
            runs = [" ".join(cl.words[j : j + TAIL]) for j in range(max(1, len(cl.words) - TAIL + 1))]
            stay_run = max(w.logprob(primed, x, eos=True)[0] for x in runs)
            n_tail = len(tail.split())
            cand = []
            for j in candidates(lines, r["next"]):
                _, pw = w.logprob(primed, tail + " " + lines[j].opening(3), eos=False)
                cand.append({"line": j, "tail": pw[n_tail - 1], "k": pw[n_tail:]})
            keep = {k: r[k] for k in ("song", "kind", "d", "t", "cur", "next", "win_start", "true_next_start")}
            keep["scorer"] = {"stay_eos": stay_eos, "stay_run": stay_run, "cand": cand}
            keep["scorer_s"] = time.monotonic() - a
            f.write(json.dumps(keep) + "\n")
    print(f"{len(rows)} checks scored -> {out / 'checks.jsonl'}")


if __name__ == "__main__":
    main()
