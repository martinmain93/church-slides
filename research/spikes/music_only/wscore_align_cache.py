"""Add Whisper-style DTW timing of each step's best span to an MLX score cache.

    uv run --project sidecar python research/spikes/music_only/wscore_align_cache.py [GEOM] KEY [KEY ...]

Reads wscore_mlx{GEOM}_{key}.json, aligns the best-scoring span of every
step against that step's window (one short decoder pass each), and writes
`place` (start of the span's last word) and `place_end` (end of the span),
seconds from the window's start.
"""
import json
import sys
import time

import cv
import numpy as np
import wscore_mlx as wm
from auto_presenter.music.inspect import load_wav

args = sys.argv[1:]
geom = args.pop(0) if args and args[0].startswith("_") else ""
S = wm.MLXScorer(audio_ctx=300)
for key in args:
    path = cv.S / f"wscore_mlx{geom}_{key}.json"
    data = json.loads(path.read_text())
    win = float(data.get("win", 6.0))
    audio = load_wav(cv.S / key / "audio.wav")
    place, place_end = [], []
    t0 = time.time()
    for t, row in zip(data["steps"], data["scores"]):
        a = audio[int((t - win) * 16000): int(t * 16000)]
        st, en = S.align(a, data["spans"][int(np.argmax(row))])
        place.append(round(st, 2))
        place_end.append(round(en, 2))
    data["place"], data["place_end"] = place, place_end
    path.write_text(json.dumps(data))
    print(f"{key}: aligned {len(place)} steps in {time.time() - t0:.0f}s", flush=True)
