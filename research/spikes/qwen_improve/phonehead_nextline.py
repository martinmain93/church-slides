"""Control for idea 2, against what ships: the app's next-line phone check.

Re-scores the exact check points `nextline_eval.py run` wrote with the
shipped next-line model (Whisper small.en's encoder on the trailing 2.5 s, then
the trained phone head, `follower.next_line`), using its own CTC chain scorer
and constants (`G`, `BLANK_PUSH`). For each candidate the chain is "current
line's last 4 words, then the candidate's first k words": `k` holds the best
path ending inside the first 1, 2, 3 opening words; `stay_eos` is the best path
ending inside the tail; `stay_run` the best path ending inside any 4-word run
of the current line. Same row format, so `nextline_eval.py report` reads it.
No next-line prior, no arming: the bare scores, as the Qwen rows are.

    cd sidecar && PYTHONPATH=. .venv/bin/python \
        ../research/spikes/qwen_improve/phonehead_nextline.py --checks <qwen run>/checks.jsonl --out <dir>
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


def main() -> None:
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend
    from auto_presenter.follower import next_line as nl

    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    w = MLXWhisperBackend("small.en")
    w.warm()  # loads the model into mlx_whisper's holder and sets FIRST_PASS
    encode = nl.mlx_encoder(w._repo)
    head = nl.load_head()
    assert head is not None, "next-line model missing"
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
            a = time.monotonic()
            win = audio[int((t - nl.WINDOW_S) * SR) : int(t * SR)]
            feats = encode(win)
            sub = np.asarray(head(feats), np.float64)
            sub[:, 0] -= nl.BLANK_PUSH
            lp = sub - np.logaddexp.reduce(sub, axis=1, keepdims=True)
            cl = lines[r["cur"]]
            tail = tuple(cl.words[-TAIL:])
            tp = nl.line_phones(tail)
            runs = [tuple(cl.words[j : j + TAIL]) for j in range(max(1, len(cl.words) - TAIL + 1))]
            rp = [nl.line_phones(x) for x in runs]
            stay_run = float(nl.score_chains(lp, rp, nl.G, [len(p) for p in rp]).in_tail.max())
            stay_eos = float(nl.score_chains(lp, [tp], nl.G, [len(tp)]).in_tail[0])
            cand = []
            for j in candidates(lines, r["next"]):
                ws = lines[j].opening(3).split()
                ks = []
                for k in range(1, len(ws) + 1):
                    op = nl.line_phones(tuple(ws[:k]))
                    ch = nl.score_chains(lp, [tp + op], nl.G, [len(tp)])
                    ks.append(float(ch.in_cand[0]))
                cand.append({"line": j, "tail": stay_eos, "k": ks or [nl.NEG]})
            keep = {k: r[k] for k in ("song", "kind", "d", "t", "cur", "next", "win_start", "true_next_start")}
            keep["scorer"] = {"stay_eos": stay_eos, "stay_run": stay_run, "cand": cand}
            keep["scorer_s"] = time.monotonic() - a
            f.write(json.dumps(keep) + "\n")
    print(f"{len(rows)} checks scored -> {out / 'checks.jsonl'}")


if __name__ == "__main__":
    main()
