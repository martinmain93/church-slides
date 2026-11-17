"""Band onsets and a tempo that is allowed to change inside one clip."""

from __future__ import annotations

import itertools
from typing import ClassVar

import numpy as np
import pytest

from auto_presenter.music.basic_pitch import FPS, HOP, frame_times
from auto_presenter.music.basic_pitch import SR as PITCH_SR
from auto_presenter.music.layers import BANDS, band_onsets, pulse_from_beats, pulse_track

SR = 16000


def _burst(lo: float, hi: float, dur_s: float = 0.03) -> np.ndarray:
    n = int(SR * dur_s)
    rng = np.random.default_rng(1)
    noise = rng.standard_normal(n).astype(np.float32)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    spec[(freqs < lo) | (freqs >= hi)] = 0
    y = np.fft.irfft(spec, n=n).astype(np.float32)
    y *= np.linspace(1.0, 0.0, n, dtype=np.float32) ** 2
    peak = float(np.max(np.abs(y))) or 1.0
    return y / peak


def _clicks(bpm: float, seconds: float, band: tuple[float, float]) -> np.ndarray:
    audio = np.zeros(int(SR * seconds), dtype=np.float32)
    burst = _burst(*band, 0.04)
    step = 60.0 / bpm
    t = 0.0
    while t < seconds - 0.05:
        i = int(t * SR)
        j = min(len(audio), i + len(burst))
        audio[i:j] += burst[: j - i]
        t += step
    return audio


def test_air_onsets_land_on_the_clicks():
    audio = _clicks(120.0, 4.0, (2500.0, 7500.0))
    times = band_onsets(audio)["air"]
    clicks = [i * 0.5 for i in range(8)]
    assert len(times) >= 6
    for t in times:
        assert any(abs(t - c) < 0.08 for c in clicks), (t, times)
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert all(0.42 <= g <= 0.58 for g in gaps), gaps


def test_pulse_follows_a_tempo_change():
    slow = _clicks(100.0, 10.0, (30.0, 90.0))
    fast = _clicks(140.0, 10.0, (30.0, 90.0))
    audio = np.concatenate([slow, fast])
    points = pulse_track(audio)
    early = [p.bpm for p in points if 2.0 <= p.t_seconds <= 6.0]
    late = [p.bpm for p in points if 14.0 <= p.t_seconds <= 18.0]
    assert early, points
    assert late, points
    assert 90 <= float(np.median(early)) <= 110
    assert 125 <= float(np.median(late)) <= 155


def test_pulse_from_beats_matches_the_grid_and_can_slow_down():
    steady = [i * 0.5 for i in range(12)]
    held = pulse_from_beats(steady)
    assert held
    assert all(118 <= p.bpm <= 122 for p in held)

    beats = list(steady)
    t = beats[-1]
    for _ in range(12):
        t += 60.0 / 90.0
        beats.append(t)
    moving = pulse_from_beats(beats)
    early = [p.bpm for p in moving if p.t_seconds < 4]
    late = [p.bpm for p in moving if p.t_seconds > beats[-4]]
    assert 115 <= float(np.median(early)) <= 125
    assert 85 <= float(np.median(late)) <= 95


def test_analysis_publishes_thirty_seconds_then_the_rest(tmp_path, monkeypatch):
    import wave

    from auto_presenter.music.basic_pitch import PitchPoint
    from auto_presenter.music.inspect import iter_analyse
    from auto_presenter.music.layers import ChordSpan

    with wave.open(str(tmp_path / "audio.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000 * 31)

    class FakePitch:
        def __init__(self, _audio):
            self.backend = "basic_pitch"
            self.points = [PitchPoint(1.0, 60.0, 0.5)]
            self.notes = []
            self.committed = 0.0

        def advance_to(self, t):
            self.committed = t

    class FakeChord:
        def __init__(self, _audio):
            self.backend = "btc"
            self.spans = [ChordSpan("C", 0.0, 31.0)]
            self.committed = 0.0

        def advance_to(self, t):
            self.committed = t

    class FakeBeats:
        def __init__(self, _wav):
            pass

        def start(self):
            pass

        def close(self):
            pass

        def join(self, timeout=None):
            pass

        def snapshot(self):
            return {
                "beats": [0.5, 1.0, 1.5],
                "downbeats": [0.5],
                "through": 100.0,
                "backend": "beat_this",
                "done": True,
            }

    monkeypatch.setattr("auto_presenter.music.inspect.PitchRun", FakePitch)
    monkeypatch.setattr("auto_presenter.music.inspect.ChordRun", FakeChord)
    monkeypatch.setattr("auto_presenter.music.inspect._BeatPump", FakeBeats)
    monkeypatch.setattr(
        "auto_presenter.music.inspect.band_onsets",
        lambda _audio: {name: [0.4, 40.0] if name == "kick" else [] for name, _lo, _hi in BANDS},
    )
    parts = list(iter_analyse(tmp_path))
    assert [p["partial"] for p in parts] == [True, False]
    assert parts[0]["through_seconds"] == 30
    kick = next(b for b in parts[0]["onsets"] if b["band"] == "kick")
    assert kick["times"] == [0.4]
    assert parts[1]["through_seconds"] == 31
    kick_all = next(b for b in parts[1]["onsets"] if b["band"] == "kick")
    assert 40.0 in kick_all["times"]


def test_beats_stay_partial_until_the_tracker_finishes(tmp_path, monkeypatch):
    import wave

    from auto_presenter.music.basic_pitch import PitchPoint
    from auto_presenter.music.inspect import iter_analyse
    from auto_presenter.music.layers import ChordSpan

    with wave.open(str(tmp_path / "audio.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000 * 31)

    class FakePitch:
        def __init__(self, _audio):
            self.backend = "basic_pitch"
            self.points = [PitchPoint(1.0, 60.0, 0.5)]
            self.notes = []
            self.committed = 0.0

        def advance_to(self, t):
            self.committed = t

    class FakeChord:
        def __init__(self, _audio):
            self.backend = "btc"
            self.spans = [ChordSpan("C", 0.0, 31.0)]
            self.committed = 0.0

        def advance_to(self, t):
            self.committed = t

    class SlowBeats:
        def __init__(self, _wav):
            self.joins = 0

        def start(self):
            return None

        def close(self):
            return None

        def join(self, timeout=None):
            self.joins += 1

        def snapshot(self):
            done = self.joins >= 2
            return {
                "beats": [0.5, 9.0] if done else [0.5],
                "downbeats": [0.5] if done else [],
                "through": 31.0 if done else 10.0,
                "backend": "beat_this",
                "done": done,
            }

    monkeypatch.setattr("auto_presenter.music.inspect.PitchRun", FakePitch)
    monkeypatch.setattr("auto_presenter.music.inspect.ChordRun", FakeChord)
    monkeypatch.setattr("auto_presenter.music.inspect._BeatPump", SlowBeats)
    monkeypatch.setattr(
        "auto_presenter.music.inspect.band_onsets",
        lambda _audio: {},
    )
    parts = list(iter_analyse(tmp_path))
    assert parts[-1]["partial"] is False
    assert 9.0 in parts[-1]["beats"]
    assert any(p["partial"] and 9.0 not in p["beats"] for p in parts[:-1])
    assert all(p["partial"] for p in parts[:-1])


def test_long_recordings_are_refused(tmp_path, monkeypatch):
    import wave

    from auto_presenter.music.inspect import analyse_recording

    monkeypatch.setattr("auto_presenter.music.inspect.MAX_INSPECT_SECONDS", 1.0)
    with wave.open(str(tmp_path / "audio.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 32000)
    with pytest.raises(ValueError, match="stops at"):
        analyse_recording(tmp_path)


def test_chord_frame_count_stays_an_int_when_the_hop_is_a_float(monkeypatch):
    """The last chunk asks for every frame. A float count crashes that chunk."""
    from auto_presenter.music.layers import ChordRun

    class FakeCqt:
        n_fft = 32768

    class FakeBackend:
        available = True
        _sess = object()
        _cqt = FakeCqt()
        _meta: ClassVar[dict] = {"sample_rate": 22050.0, "hop_length": 2048.0}
        timestep = 108

    monkeypatch.setattr(
        "auto_presenter.music.btc.BtcChordBackend",
        lambda: FakeBackend(),
    )
    run = ChordRun(np.zeros(16000 * 20, np.float32))
    run._ensure()
    assert run.backend == "btc"
    assert type(run._hop) is int
    assert type(run._n_frames) is int
    # What the finished pass hands to the transform.
    np.empty((run._n_frames, 2))
    list(range(0, run._n_frames, 108))


def test_pitch_frames_stay_on_the_audio_clock():
    times = frame_times(3)
    # First kept frame sits on the start of the unpadded audio.
    assert abs(float(times[0])) < 0.001
    # The next window begins a hop later, not 142/86 seconds later.
    assert abs(float(times[142]) - HOP / PITCH_SR) < 0.001
    assert float(times[142]) < 142 / FPS - 0.005
