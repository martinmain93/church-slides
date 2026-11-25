"""Score the spike's next-line checks with a head trained by train.py, in the
nextline_eval row format (same chains as phonehead_nextline.py).
    PYTHONPATH=<repo>/sidecar:<repo>/research/spikes/qwen_improve .venv/bin/python score_checks.py wh|qw"""
import json, sys, numpy as np
from pathlib import Path
F = sys.argv[1]
import os
if sys.argv[-1] != "logits": F = os.environ.get("TAG", F)
if sys.argv[-1] == "logits":  # torch venv: just the head's outputs
    import torch
    sys.argv = [sys.argv[0], F]; from train import Head
    X = np.load(f"{F}_checks_x.npy").astype(np.float32)
    import os
    m = Head(X.shape[-1], h=int(os.environ.get("HEAD_H", "256")))
    m.load_state_dict(torch.load(os.environ.get("HEAD_PT", f"{F}_best.pt"), map_location="cpu")); m.eval()
    F = os.environ.get("TAG", F)
    with torch.no_grad():
        L = np.concatenate([m(torch.from_numpy(X[b:b + 256])).numpy() for b in range(0, len(X), 256)])
    np.save(f"{F}_checks_logits.npy", L); sys.exit()
from auto_presenter.follower import next_line as nl
from common import song_lines
from nextline_eval import TAIL, candidates
W = Path.home() / "Development/auto-presenter-research/work"
rows = [json.loads(l) for l in open(W / "qwen_improve/runs/nextline/checks.jsonl")]
out = W / f"qwen_improve/runs/nextline_head_{F}"; out.mkdir(parents=True, exist_ok=True)
L = np.load(Path(__file__).parent / f"{F}_checks_logits.npy")
cache = {}
with open(out / "checks.jsonl", "w") as f:
    for r, sub in zip(rows, L):
        if r["song"] not in cache: cache[r["song"]] = song_lines(r["song"])
        lines = cache[r["song"]]
        sub = sub.astype(np.float64); sub[:, 0] -= float(sys.argv[2]) if len(sys.argv) > 2 else nl.BLANK_PUSH
        lp = sub - np.logaddexp.reduce(sub, axis=1, keepdims=True)
        g = float(sys.argv[3]) if len(sys.argv) > 3 else nl.G
        cl = lines[r["cur"]]; tp = nl.line_phones(tuple(cl.words[-TAIL:]))
        rp = [nl.line_phones(tuple(cl.words[j:j + TAIL])) for j in range(max(1, len(cl.words) - TAIL + 1))]
        stay_run = float(nl.score_chains(lp, rp, g, [len(p) for p in rp]).in_tail.max())
        stay_eos = float(nl.score_chains(lp, [tp], g, [len(tp)]).in_tail[0])
        cand = []
        for j in candidates(lines, r["next"]):
            ws = lines[j].opening(3).split(); ks = []
            for k in range(1, len(ws) + 1):
                ks.append(float(nl.score_chains(lp, [tp + nl.line_phones(tuple(ws[:k]))], g, [len(tp)]).in_cand[0]))
            cand.append({"line": j, "tail": stay_eos, "k": ks or [nl.NEG]})
        keep = {k: r[k] for k in ("song", "kind", "d", "t", "cur", "next", "win_start", "true_next_start")}
        keep["scorer"] = {"stay_eos": stay_eos, "stay_run": stay_run, "cand": cand}; keep["scorer_s"] = 0.0
        f.write(json.dumps(keep) + "\n")
print(F, "scored", len(rows), "->", out)
