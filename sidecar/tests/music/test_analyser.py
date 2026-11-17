"""The analyser is verified against signals whose answer is known.

A chord recogniser that has quietly degenerated still emits plausible-looking
chord names, and a beat tracker that has stopped locking still emits beats at
roughly the right rate — so "it produced output" is not evidence of anything.
"""

from __future__ import annotations

import numpy as np
import pytest

from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame
from auto_presenter.music.analyser import MusicAnalyser
from auto_presenter.music.features import PITCH_NAMES, chroma

FRAME = 1600  # 100 ms, the capture default


def _midi(name: str, octave: int = 3) -> float:
    return 440.0 * 2 ** ((PITCH_NAMES.index(name) + 12 * (octave + 1) - 69) / 12.0)


def _tone(notes: list[str], seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    out = np.zeros_like(t)
    for n in notes:
        f = _midi(n)
        for h, amp in ((1, 1.0), (2, 0.5), (3, 0.3)):
            out += amp * np.sin(2 * np.pi * f * h * t)
    return (out / np.abs(out).max() * 0.5).astype(np.float32)


def _clicks(bpm: float, seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 0.002, int(seconds * SAMPLE_RATE)).astype(np.float32)
    period = 60.0 / bpm
    t = period
    while t < seconds - 0.2:
        i = int(t * SAMPLE_RATE)
        env = np.exp(-np.arange(1600) / 120.0)
        a[i:i + 1600] += (rng.normal(0, 1, 1600) * env).astype(np.float32) * 0.6
        t += period
    return a


def _drive(analyser: MusicAnalyser, audio: np.ndarray):
    updates = []
    t = 0.0
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        chunk = audio[i:i + FRAME]
        nxt = t + FRAME / SAMPLE_RATE
        updates.append(analyser.feed(
            AudioFrame(samples=chunk, t_start=t, t_end=nxt)))
        t = nxt
    return updates


def test_chroma_reads_a_known_triad():
    ch = chroma(_tone(["C", "E", "G"], 2.0))
    top = np.argsort(ch.mean(axis=0))[::-1][:3]
    assert {PITCH_NAMES[i] for i in top} == {"C", "E", "G"}


@pytest.mark.parametrize(
    "notes,expected",
    [(["C", "E", "G"], "C"), (["A", "C", "E"], "Am"), (["G", "B", "D"], "G")],
)
def test_recognises_a_sustained_triad(notes, expected):
    a = MusicAnalyser(beats_enabled=False)
    updates = _drive(a, _tone(notes, 3.0))
    assert updates[-1].chord == expected


def test_top_three_are_ordered_and_named():
    a = MusicAnalyser(beats_enabled=False)
    last = _drive(a, _tone(["C", "E", "G"], 3.0))[-1]
    assert len(last.candidates) == 3
    confs = [c.confidence for c in last.candidates]
    assert confs == sorted(confs, reverse=True)
    assert last.chord == last.candidates[0].name


def test_tempo_is_recovered_from_a_click_track():
    a = MusicAnalyser(chords_enabled=False)
    updates = _drive(a, _clicks(120.0, 20.0))
    bpm = updates[-1].bpm
    assert 110.0 < bpm < 130.0, f"expected ~120 BPM, got {bpm:.1f}"


def test_beats_land_on_the_clicks():
    bpm = 100.0
    a = MusicAnalyser(chords_enabled=False)
    updates = _drive(a, _clicks(bpm, 20.0))
    beats = [b for u in updates for b in u.beats]
    assert len(beats) > 10
    period = 60.0 / bpm
    truth = np.arange(period, 20.0 - 0.2, period)
    # Beats are placed causally; only judge those after the grid is established.
    err = [min(abs(truth - b)) for b in beats[4:]]
    assert float(np.median(err)) < 0.12, f"median |err| {np.median(err):.3f}s"


def test_toggles_suppress_the_work_they_name():
    audio = _tone(["C", "E", "G"], 2.0)
    off = MusicAnalyser(chords_enabled=False, beats_enabled=False)
    last = _drive(off, audio)[-1]
    assert last.chord is None and last.candidates == [] and last.beats == []
    # Levels are always sent: the waveform is the point of the panel.
    assert last.levels


def test_cost_stays_far_below_the_asr_budget():
    a = MusicAnalyser()
    last = _drive(a, _clicks(110.0, 20.0))[-1]
    # Whisper runs at roughly 0.31 realtime; this must not compete with it.
    assert last.realtime_factor < 0.05, f"rtf {last.realtime_factor:.4f}"


def test_reset_clears_state():
    a = MusicAnalyser()
    _drive(a, _clicks(120.0, 12.0))
    a.reset()
    assert a._env.size == 0 and a._scores is None
    assert not a._beat.established
