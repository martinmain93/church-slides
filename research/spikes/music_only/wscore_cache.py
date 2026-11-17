"""Score every distinct 6-word span of a song's slides against the trailing
6 s of audio, every STEP seconds, and cache it (offline stand-in for the
live version, which would score only the follower's candidates).

    uv run --project sidecar python research/spikes/music_only/wscore_cache.py KEY [KEY ...]
"""
import json
import sys
import time

import numpy as np

import cv
import score as sc
import wscore
from auto_presenter.music.inspect import load_wav

STEP, WIN, SPAN = 2.0, 6.0, 6


def spans_for(slides, span: int | None = None):
    """(slide, end word index) -> the span of up to `span` words ending there."""
    SPAN_ = span or SPAN
    out = {}
    for si, sw in enumerate(slides):
        w = [x for x in (sc.norm(t) for t in sw) if x]
        for j in range(len(w)):
            out[(si, j)] = " ".join(w[max(0, j - SPAN_ + 1) : j + 1])
    return out


def main():
    for key in sys.argv[1:]:
        path = cv.S / f"wscore2_{key}.json"
        if path.exists():
            continue
        song = cv.Song(key)
        audio = load_wav(cv.S / key / "audio.wav")
        spans = spans_for(song.slides)
        uniq = sorted(set(spans.values()))
        S = wscore.Scorer("small.en", keep_frames=300, threads=4)
        steps, mat, ends = [], [], []
        t0 = time.time()
        t = WIN
        while t <= len(audio) / 16000:
            a = audio[int((t - WIN) * 16000) : int(t * 16000)]
            mat.append(S.score(a, uniq).round(3).tolist())
            ends.append(np.nan_to_num(S.last_end, nan=-1.0).round(2).tolist())
            steps.append(t)
            t += STEP
        path.write_text(json.dumps({"steps": steps, "spans": uniq, "scores": mat, "ends": ends, "win": WIN,
                                    "owner": {f"{si}:{j}": s for (si, j), s in spans.items()}}))
        print(f"{key}: {len(steps)} steps x {len(uniq)} spans in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
