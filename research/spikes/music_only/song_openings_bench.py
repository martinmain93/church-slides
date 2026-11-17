"""MLX time for one slow-path pass vs number of spans (the openings spike's cost).

    research/spikes/music_only/py.sh research/spikes/music_only/song_openings_bench.py

Times are only indicative when other jobs share the GPU.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr  # noqa: E402
from auto_presenter.asr.mlx_backend import local_snapshot, repo_for  # noqa: E402
from auto_presenter.follower.scorer import MLXSpanScorer  # noqa: E402
from auto_presenter.music.inspect import load_wav  # noqa: E402

D = Path(__file__).resolve().parents[3] / "build" / "openings"
texts = sorted({r["text"] for r in json.loads((D / "spans.json").read_text())["spans"]})
rng = np.random.default_rng(0)
audio = load_wav(mr.CASES["set"][0] / "audio.wav")
s = MLXSpanScorer(local_snapshot(repo_for("small.en")))
s._load()
s._place = lambda *a, **k: 0.0
for n in [20, 40, 80, 160, 320, 640]:
    sub = list(rng.choice(texts, n, replace=False))
    ts = []
    for k in range(8):
        t = 300.0 + 6 * k
        a = audio[int((t - 6) * 16000): int(t * 16000)]
        t0 = time.perf_counter()
        s.score(a, sub)
        ts.append(time.perf_counter() - t0)
    print(f"{n:4d} spans: median {np.median(ts[1:]) * 1000:.0f} ms  min {min(ts[1:]) * 1000:.0f} ms", flush=True)
