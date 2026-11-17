"""Demucs stems for a recording, written beside it (vocals/drums/bass/other).

    uv run --project sidecar python research/spikes/music_only/stems.py REC_DIR [REC_DIR ...]

Uses the app's own realtime stem session (htdemucs via MLX, in its venv),
pushed a minute at a time, so the stems are the ones the inspector makes.
"""

import sys
from pathlib import Path

import numpy as np

from auto_presenter.music.inspect import load_wav
from auto_presenter.music.realtime_stems import RealtimeStems, _merge, write_stem_wavs

for arg in sys.argv[1:]:
    d = Path(arg)
    out = d / "stems"
    if (out / "vocals.wav").exists():
        print(f"{d.name}: stems exist")
        continue
    audio = load_wav(d / "audio.wav")
    rs = RealtimeStems()
    rs.start()
    got: dict[str, np.ndarray] = {}
    step = 16000 * 60
    for i in range(0, len(audio), step):
        got = _merge(got, rs.push(audio[i : i + step]))
        print(f"{d.name}: {min(len(audio), i + step) / len(audio):.0%}", flush=True)
    got = _merge(got, rs.finish())
    rs.close()
    out.mkdir(exist_ok=True)
    write_stem_wavs(out, {k: v[: len(audio)] for k, v in got.items()})
    print(f"{d.name}: wrote {out}")
