"""What each stage costs on this machine, streamed the way it would run live.

    uv run --project sidecar python research/spikes/music_only/bench_latency.py

60 s of a golden through each stage in its live chunking. Reports compute
per call, calls per second of audio (so realtime factor), and the stage's
structural delay (how much audio it must wait for before it can answer).
"""
import time

import numpy as np

import cv
from auto_presenter.music import basic_pitch as bp
from auto_presenter.music.btc import BtcChordBackend
from auto_presenter.music.inspect import load_wav
from auto_presenter.music.realtime_stems import RealtimeStems

audio = load_wav(cv.S / "wabn" / "audio.wav")[: 16000 * 60]
rows = []

# Demucs (htdemucs, MLX), the app's realtime session
rs = RealtimeStems()
rs.start()
t0 = time.time()
got = 0
first_out = None
for i in range(0, len(audio), 1600):
    out = rs.push(audio[i : i + 1600])
    n = len(out.get("vocals", []))
    if n and first_out is None:
        first_out = (i + 1600) / 16000
    got += n
rs.finish()
rs.close()
dt = time.time() - t0
rows.append(("demucs htdemucs (MLX)", dt / 60, f"window 7.8 s, first output after {first_out:.1f} s of audio"))

# Basic Pitch, 2 s windows
run = bp.PitchRun(audio)
t0 = time.time()
run.advance_to(1e9)
dt = time.time() - t0
rows.append(("basic pitch", dt / 60, "window 2.0 s (needs ~1 s past a frame)"))

# BTC, trailing blocks at 4 Hz (as the analyser runs it)
btc = BtcChordBackend()
btc.reset_stream()
t0 = time.time()
n = 0
for i in range(0, len(audio), 4000):
    btc.push(audio[i : i + 4000])
    if btc.predict_stream() is not None:
        n += 1
dt = time.time() - t0
rows.append(("BTC chords (stream, 4 Hz)", dt / 60, "trailing 10 s block; label describes ~0.74 s ago"))

# Whisper small.en MLX: one 6 s pass
from auto_presenter.asr.mlx_backend import MLXWhisperBackend
from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.eval.replay import Replayer, load_recording
be = MLXWhisperBackend("small.en")
w = SlidingWindowWrapper(be)
t0 = time.time()
frames = list(Replayer(load_recording(cv.S / "wabn"), realtime=False).audio_frames())
k = 0
for e in w.transcribe(iter(frames[:600])):
    k += 1
dt = time.time() - t0
rows.append(("whisper small.en (MLX, 6 s window)", dt / 60, "~2.7 s measured delivery lag (margin 1.5 s + step + pass)"))

# fast recogniser
from auto_presenter.asr.fast_channel import FastChannel, default_model_dir
fc = FastChannel(default_model_dir())
fc.start()
t0 = time.time()
for i in range(0, len(audio), 1600):
    fc.feed(audio[i : i + 1600])
dt = time.time() - t0
rows.append(("zipformer 20M streaming", dt / 60, "~0.4 s word latency"))

print(f"{'stage':38s} {'realtime factor':>16s}   structural delay")
for name, rtf, note in rows:
    print(f"{name:38s} {rtf:16.3f}   {note}")
