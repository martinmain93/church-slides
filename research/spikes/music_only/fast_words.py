"""Words from the fast streaming recogniser (sherpa-onnx Zipformer, 20M), on
the mix and on the Demucs vocal stem, with their audio and arrival times.

    uv run --project sidecar python research/spikes/music_only/fast_words.py

Fed 100 ms at a time as capture would. Arrival = the moment the recogniser
emitted the word, plus the stem's own delay when it listens to the stem.
Cached to fast_words.json: {rec_key: {"mix": [...], "stem": [...]}}.
"""
import json
import time

import cv
from auto_presenter.asr.fast_channel import FastChannel, default_model_dir
from auto_presenter.music.inspect import load_wav

STEM_DELAY = 3.0
out_path = cv.S / "fast_words.json"
cache = json.loads(out_path.read_text()) if out_path.exists() else {}
for key in list(cv.SONGS) + list(cv.SETS):
    if key in cache:
        continue
    cache[key] = {}
    for src, path, extra in (("mix", cv.S / key / "audio.wav", 0.0), ("stem", cv.S / key / "stems" / "vocals.wav", STEM_DELAY)):
        audio = load_wav(path)
        fc = FastChannel(default_model_dir())
        assert fc.start(), fc.status
        words = []
        t0 = time.time()
        for i in range(0, len(audio), 1600):
            for w in fc.feed(audio[i : i + 1600]):
                words.append((w.t_start, w.t_emitted + extra, w.text))
        cache[key][src] = words
        print(f"{key} {src}: {len(words)} words, RTF {(time.time() - t0) / (len(audio) / 16000):.3f}", flush=True)
    out_path.write_text(json.dumps(cache))
