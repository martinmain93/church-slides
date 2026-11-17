"""Cache MLX scores (truncated encoder) for every distinct span, every STEP s,
with each span's aligned end time — the MLX twin of wscore_cache.py.

    uv run --project sidecar python research/spikes/music_only/wscore_mlx_cache.py KEY [KEY ...]
"""
import json
import sys
import time

import numpy as np

import cv
import wscore_cache as wc
import wscore_mlx as wm
from auto_presenter.music.inspect import load_wav

# geometry: WIN seconds of audio, SPAN words per candidate; default = the original 6 s / 6 words
WIN = float(sys.argv[1]) if sys.argv[1].replace(".", "").isdigit() else wc.WIN
SPAN = int(sys.argv[2]) if sys.argv[1].replace(".", "").isdigit() else wc.SPAN
keys = sys.argv[3:] if sys.argv[1].replace(".", "").isdigit() else sys.argv[1:]
tag = "" if (WIN, SPAN) == (wc.WIN, wc.SPAN) else f"_w{WIN:g}s{SPAN}"
S = wm.MLXScorer(audio_ctx=int(round(WIN * 50)))
for key in keys:
    path = cv.S / f"wscore_mlx{tag}_{key}.json"
    if path.exists():
        continue
    song = cv.Song(key)
    audio = load_wav(cv.S / key / "audio.wav")
    spans = wc.spans_for(song.slides, SPAN)
    uniq = sorted(set(spans.values()))
    steps, mat, ends, tt = [], [], [], []
    t0 = time.time()
    t = WIN
    while t <= len(audio) / 16000:
        a = audio[int((t - WIN) * 16000): int(t * 16000)]
        sc_all, en_all = [], []
        for i in range(0, len(uniq), 64):
            sc_, en_ = S.score(a, uniq[i: i + 64])
            sc_all.append(sc_); en_all.append(en_)
        mat.append(np.concatenate(sc_all).round(3).tolist())
        ends.append(np.concatenate(en_all).round(2).tolist())
        steps.append(t)
        t += wc.STEP
    path.write_text(json.dumps({"steps": steps, "spans": uniq, "scores": mat, "ends": ends, "win": WIN, "span": SPAN,
                                "owner": {f"{si}:{j}": s for (si, j), s in spans.items()}}))
    print(f"{key}: {len(steps)} steps x {len(uniq)} spans in {time.time() - t0:.0f}s", flush=True)
