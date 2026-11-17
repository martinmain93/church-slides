"""Live latency of MLX scoring next to the app's live Whisper, on one GPU.

    uv run --project sidecar python research/spikes/music_only/bench_concurrent.py [--seconds 90] [--candidates 16]

Plays a golden's audio at real-time pace. One thread runs the app's own
live path — `SlidingWindowWrapper` over `MLXWhisperBackend` small.en — and
records each pass's duration and each event's delivery lag. Another thread,
once a second, scores N candidate spans against the trailing 6 s with the
MLX scorer (truncated encoder) and records how long each step takes from the
moment its window's audio exists.

Run alone first (each side by itself), then together, so contention shows.
"""
from __future__ import annotations

import argparse
import queue
import threading
import time

import numpy as np

import cv
import wscore_cache as wc
import wscore_mlx as wm
from auto_presenter.asr.mlx_backend import MLXWhisperBackend
from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.audio.types import AudioFrame
from auto_presenter.music.inspect import load_wav


def run(audio, seconds, n_cand, do_whisper, do_score):
    start = time.perf_counter()
    stop = threading.Event()
    frames_q: queue.Queue = queue.Queue()
    lags, pass_times, step_times = [], [], []

    def feeder():
        # 100 ms frames at real-time pace
        for i in range(int(seconds * 10)):
            target = start + (i + 1) * 0.1
            time.sleep(max(0.0, target - time.perf_counter()))
            frames_q.put(i)
        frames_q.put(None)

    def whisper():
        be = MLXWhisperBackend("small.en")
        w = SlidingWindowWrapper(be, emission_observer=lambda ev, avail: None)

        def frames():
            while True:
                i = frames_q.get()
                if i is None:
                    return
                seg = audio[i * 1600:(i + 1) * 1600]
                yield AudioFrame(samples=seg.astype(np.float32), t_start=i * 0.1, t_end=(i + 1) * 0.1)

        for ev in w.transcribe(frames()):
            if ev.is_final and ev.words:
                now = time.perf_counter() - start
                lags.append(now - ev.t_end)
            pt = getattr(w, "_last_pass_seconds", None)
            if pt:
                pass_times.append(pt)

    def scorer():
        S = wm.MLXScorer(audio_ctx=300)
        song = cv.Song("wabn")
        spans = sorted(set(wc.spans_for(song.slides).values()))[:n_cand]
        S.score(audio[:96000], spans)  # warm-up
        t = 6.0
        while t <= seconds:
            # wait until this window's audio has been "heard"
            ready = start + t
            time.sleep(max(0.0, ready - time.perf_counter()))
            a = audio[int((t - 6) * 16000): int(t * 16000)]
            t0 = time.perf_counter()
            S.score(a, spans)
            step_times.append(time.perf_counter() - t0)
            # how late, relative to when the window's audio existed
            t += 1.0

    threads = [threading.Thread(target=feeder)]
    if do_whisper:
        threads.append(threading.Thread(target=whisper))
    if do_score:
        threads.append(threading.Thread(target=scorer))
    if not do_whisper:
        threads[0] = threading.Thread(target=lambda: [time.sleep(seconds)])
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return np.array(lags), np.array(pass_times), np.array(step_times)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=90)
    ap.add_argument("--candidates", type=int, default=16)
    args = ap.parse_args()
    audio = load_wav(cv.S / "wabn" / "audio.wav")
    q = lambda x: f"median {np.median(x):.2f} s, p90 {np.percentile(x, 90):.2f} s" if len(x) else "n/a"
    for label, w, s in (("scorer alone", False, True), ("whisper alone", True, False), ("together", True, True)):
        lags, passes, steps = run(audio, args.seconds, args.candidates, w, s)
        print(f"{label:14s} | whisper pass {q(passes)}; word delivery lag {q(lags)} | scoring step ({args.candidates} candidates) {q(steps)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
