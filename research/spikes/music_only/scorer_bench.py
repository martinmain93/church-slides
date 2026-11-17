"""Scorer compute per pass at each window (production MLXSpanScorer, small.en).

    python research/spikes/music_only/wt_run.py scorer_bench.py [N] [windows...]

N passes per window on What A Beautiful Name (160 spans) and Awesome God
(the set's item 1, 21 spans), median / p90 seconds, interleaved so GPU
contention from other processes hits every window alike.
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr  # noqa: E402
import scorer_cache as scc  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.follower.scorer import MLXSpanScorer, spans_for  # noqa: E402
from auto_presenter.music.inspect import load_wav  # noqa: E402

n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
wins = [float(x) for x in sys.argv[2:]] or [6.0, 4.0, 3.0]
cases = []
for name, item in (("wabn", 0), ("set", 1)):
    rdir = mr.CASES[name][0]
    rec = load_recording(rdir)
    snap = [x for x in rec.setlist_content if x.item_idx == item][0]
    cases.append((name, load_wav(Path(rdir) / "audio.wav"), sorted(set(spans_for(snap.slides).values()))))
repo = scc.scorer()
scorers = {w: MLXSpanScorer(repo, audio_ctx=int(round(w * 50))) for w in wins}
times = {(c[0], w): [] for c in cases for w in wins}
for name, audio, spans in cases:
    for w in wins:
        scorers[w].score(audio[: int(w * 16000)], spans)  # warm up
for k in range(n):
    for name, audio, spans in cases:
        end = 60.0 + 7.3 * k
        for w in wins:
            a = audio[int((end - w) * 16000): int(end * 16000)]
            t0 = time.perf_counter()
            scorers[w].score(a, spans)
            times[(name, w)].append(time.perf_counter() - t0)
for (name, w), v in times.items():
    print(f"{name:5s} ({len([c for c in cases if c[0] == name][0][2])} spans) window {w:g}: "
          f"median {np.median(v):.3f}s p90 {np.percentile(v, 90):.3f}s", flush=True)
