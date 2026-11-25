"""Whisper small.en and Qwen3-ASR 0.6B encoder features for the same windows.

train: the own-recording phone-head windows (hft0_own_y.json meta: rec, start).
checks: the Qwen spike's next-line checks (trailing 2.5 s of each check).
    cd sidecar && uv run --no-sync python <this> train|checks
"""
import json, sys, time
from pathlib import Path
import numpy as np, soundfile as sf
W = Path.home() / "Development/auto-presenter-research/work"
OUT = W / "qhead"
REC = Path.home() / "Library/Application Support/com.churchslides.app/recordings"
WIN = 2.5
import mlx.core as mx
from auto_presenter.follower.scorer import MLXSpanScorer
from auto_presenter.asr.mlx_backend import local_snapshot
from auto_presenter.asr.qwen_backend import QWEN_REPO
from mlx_qwen3_asr.load_models import load_model
from mlx_qwen3_asr.audio import compute_features
sc = MLXSpanScorer("mlx-community/whisper-small.en-mlx", audio_ctx=int(WIN * 50)); sc._load()
qm, _ = load_model(local_snapshot(QWEN_REPO))
def wh(x): return np.asarray(sc._encode(x)[0]).astype(np.float16)
def qw(x):
    mel, lens = compute_features(x.astype(np.float32))
    f, _ = qm.audio_tower(mel.astype(mx.float16), lens)
    return np.asarray(f[0].astype(mx.float32)).astype(np.float16)
def fix(a, n):  # pad/trim frames to n
    return a[:n] if len(a) >= n else np.concatenate([a, np.zeros((n - len(a), a.shape[1]), a.dtype)])
mode = sys.argv[1]
if mode == "train":
    Y = json.load(open(W / "hft0_own_y.json")); meta = Y["meta"]
    cache = {}; FW, FQ = [], []; st = time.time()
    for i, (rid, s) in enumerate(meta):
        if rid not in cache:
            x, _ = sf.read(REC / rid / "audio.wav", dtype="float32"); cache = {rid: x.mean(1) if x.ndim > 1 else x}
        seg = cache[rid][int(s * 16000): int((s + WIN) * 16000)]
        FW.append(fix(wh(seg), 125)); FQ.append(qw(seg))
        if i % 500 == 0: print(i, f"{time.time()-st:.0f}s", FQ[-1].shape, flush=True)
    nq = max(len(f) for f in FQ)
    np.save(OUT / "wh_own_x.npy", np.stack(FW)); np.save(OUT / "qw_own_x.npy", np.stack([fix(f, nq) for f in FQ]))
    json.dump(Y, open(OUT / "own_y.json", "w")); print("done", len(meta), "qwen frames", nq)
else:
    sys.path.insert(0, str(Path.home() / "Development/church-slides/.claude/worktrees/practical-cartwright-edb24b/research/spikes/qwen_improve"))
    from common import SR, audio_of, load_clip
    rows = [json.loads(l) for l in open(W / "qwen_improve/runs/nextline/checks.jsonl")]
    cache = {}; FW, FQ = [], []
    for r in rows:
        if r["song"] not in cache: cache = {r["song"]: audio_of(load_clip(r["song"]))}
        seg = cache[r["song"]][int((r["t"] - WIN) * SR): int(r["t"] * SR)]
        FW.append(fix(wh(seg), 125)); FQ.append(qw(seg))
    nq = max(len(f) for f in FQ)
    np.save(OUT / "wh_checks_x.npy", np.stack(FW)); np.save(OUT / "qw_checks_x.npy", np.stack([fix(f, nq) for f in FQ]))
    print("checks", len(rows), "qwen frames", nq)
