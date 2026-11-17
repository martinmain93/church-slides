"""The optional learned recogniser, and — more importantly — its absence.

The model is an extra install, so the path that must never break is the one
where it is missing. These tests run either way.
"""

from __future__ import annotations

import numpy as np
import pytest

from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame
from auto_presenter.music.analyser import MusicAnalyser
from auto_presenter.music.btc import MAJMIN_LABELS, BtcChordBackend

backend = BtcChordBackend()
needs_model = pytest.mark.skipif(
    not backend.available, reason=f"BTC model not installed ({backend.status})"
)


def _tone(notes: list[float], seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    out = np.zeros_like(t)
    for f in notes:
        for h, amp in ((1, 1.0), (2, 0.5), (3, 0.25)):
            out += amp * np.sin(2 * np.pi * f * h * t)
    return (out / np.abs(out).max() * 0.5).astype(np.float32)


def _drive(a: MusicAnalyser, audio: np.ndarray):
    out, t = [], 0.0
    for i in range(0, len(audio) - 1600 + 1, 1600):
        nxt = t + 1600 / SAMPLE_RATE
        out.append(a.feed(AudioFrame(samples=audio[i:i + 1600], t_start=t, t_end=nxt)))
        t = nxt
    return out


def test_label_set_is_the_maj_min_vocabulary():
    assert len(MAJMIN_LABELS) == 25
    assert MAJMIN_LABELS[0] == "C" and MAJMIN_LABELS[1] == "Cm"
    assert MAJMIN_LABELS[-1] == "N"


def test_a_missing_model_reports_why_and_stays_unavailable(tmp_path):
    b = BtcChordBackend(model_dir=tmp_path)
    assert not b.available
    assert b.status == "model_missing"
    assert b.predict(np.zeros(16000 * 12, dtype=np.float32)) is None


def test_analyser_falls_back_to_templates_without_a_model(tmp_path):
    a = MusicAnalyser(beats_enabled=False, chord_backend=BtcChordBackend(tmp_path))
    last = _drive(a, _tone([261.6, 329.6, 392.0], 3.0))[-1]
    assert last.backend == "template"
    assert last.chord == "C"


def test_a_backend_that_raises_is_dropped_not_propagated():
    class Boom:
        available = True
        labels = MAJMIN_LABELS

        def push(self, _samples):
            raise RuntimeError("boom")

        def predict_stream(self):
            return None

    a = MusicAnalyser(beats_enabled=False, chord_backend=Boom())
    last = _drive(a, _tone([261.6, 329.6, 392.0], 2.0))[-1]
    assert last.backend == "template"
    assert last.chord == "C"


@needs_model
def test_model_reports_ready_and_a_sane_window():
    assert backend.status == "ready"
    # One block plus the CQT's own context: about eleven and a half seconds.
    assert 10.0 < backend.window_seconds < 14.0


@needs_model
def test_every_backend_shares_one_loaded_model():
    # One per AI session: a model loaded per service start leaked ~50 MB of
    # native memory a start in the frozen engine (the Windows soak).
    other = BtcChordBackend()
    assert other._sess is backend._sess
    # ...but the streaming state is each backend's own.
    other.push(np.zeros(16000, dtype=np.float32))
    assert not hasattr(backend, "_carry") or backend._carry is not other._carry


@needs_model
def test_predict_returns_none_until_a_full_block_exists():
    assert backend.predict(np.zeros(16000 * 2, dtype=np.float32)) is None


@needs_model
def test_probabilities_are_a_distribution_over_the_vocabulary():
    audio = _tone([261.6, 329.6, 392.0], backend.window_seconds + 2)
    probs = backend.predict(audio)
    assert probs is not None
    assert len(probs) == len(MAJMIN_LABELS)
    assert probs.min() >= 0.0
    assert abs(float(probs.sum()) - 1.0) < 1e-4


@needs_model
def test_streaming_and_batch_agree():
    """`push`/`predict_stream` is an optimisation of `predict`, not a variant."""
    audio = _tone([261.6, 329.6, 392.0], backend.window_seconds + 3)
    batch = backend.predict(audio)
    backend.reset_stream()
    for i in range(0, len(audio) - 1600 + 1, 1600):
        backend.push(audio[i:i + 1600])
    stream = backend.predict_stream()
    assert batch is not None and stream is not None
    assert int(np.argmax(batch)) == int(np.argmax(stream))
