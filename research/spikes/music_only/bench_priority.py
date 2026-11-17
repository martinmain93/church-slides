"""Scorer first, transcription in the gaps: does priority protect the scorer?

    uv run --project sidecar python research/spikes/music_only/bench_priority.py [--seconds 60]

One worker owns the GPU and plays a golden in real time. Every second it
first scores 16 candidate spans against the window that just ended (the
critical path); then, if a free-transcription pass is due under the chosen
cadence, it runs one (the low-priority cross-song path). A GPU pass cannot be
interrupted, so priority is purely the order of work.

Reported per cadence:
* scorer latency — window end -> scores ready;
* transcription completion — window end -> transcript ready;
* implied free-word lag — the live wrapper holds the last 1.5 s back until
  stable, so a word waits ~1.5 s + half the cadence + the completion time.
"""
from __future__ import annotations

import argparse
import time

import numpy as np

import cv
import wscore_cache as wc
import wscore_mlx as wm
from auto_presenter.asr.mlx_backend import MLXWhisperBackend
from auto_presenter.audio.types import AudioFrame
from auto_presenter.music.inspect import load_wav


def frames_for(audio, t0, t1):
    out = []
    for i in range(int(t0 * 10), int(t1 * 10)):
        seg = audio[i * 1600:(i + 1) * 1600].astype(np.float32)
        out.append(AudioFrame(samples=seg, t_start=i * 0.1, t_end=(i + 1) * 0.1))
    return out


def run(audio, seconds, cadence, scorer, spans, backend):
    start = time.perf_counter()
    score_lat, tx_lat = [], []
    t = 6.0
    while t <= seconds:
        time.sleep(max(0.0, start + t - time.perf_counter()))
        a = audio[int((t - 6) * 16000): int(t * 16000)]
        if scorer is not None:
            scorer.score(a, spans)
            score_lat.append(time.perf_counter() - (start + t))
        if cadence and (round(t) % cadence == 0):
            list(backend.transcribe(frames_for(audio, t - 6, t)))
            tx_lat.append(time.perf_counter() - (start + t))
        t += 1.0
    return np.array(score_lat), np.array(tx_lat)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    args = ap.parse_args()
    audio = load_wav(cv.S / "wabn" / "audio.wav")
    S = wm.MLXScorer(audio_ctx=300)
    spans = sorted(set(wc.spans_for(cv.Song("wabn").slides).values()))[:16]
    S.score(audio[:96000], spans)
    be = MLXWhisperBackend("small.en")
    list(be.transcribe(frames_for(audio, 0, 6)))
    q = lambda x: f"median {np.median(x):.2f} s, p90 {np.percentile(x, 90):.2f} s" if len(x) else "—"
    for cad in (0, 1, 2, 3):
        sl, tl = run(audio, args.seconds, cad, S, spans, be)
        implied = (1.5 + cad / 2 + np.median(tl)) if len(tl) else None
        print(f"transcription every {cad or 'never':>5} s | scorer latency {q(sl)} | transcription completion {q(tl)}"
              + (f" | implied free-word lag ~{implied:.1f} s" if implied else ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
