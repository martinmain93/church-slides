"""The realtime stem session finalises audio a stride behind the input."""

from __future__ import annotations

import numpy as np

from auto_presenter.music.realtime_stems import STEMS, OverlapAdd, RealtimeStems

SEGMENT = 8
# overlap 0.25 -> stride 6. The freshest finished sample is 2 samples behind.


class _Echo:
    samplerate = 16000
    segment = SEGMENT

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None

    def infer(self, mono: np.ndarray, valid: int) -> np.ndarray:
        # The real model centre-trims. A short window arrives centre-padded.
        left = (len(mono) - valid) // 2
        piece = np.asarray(mono[left : left + valid], dtype=np.float32)
        return np.stack([piece for _ in STEMS])


def test_a_full_window_finishes_one_stride_and_keeps_the_level():
    ola = OverlapAdd(SEGMENT, 0.25, len(STEMS))
    first = ola.add(np.ones((len(STEMS), SEGMENT), dtype=np.float32))
    assert first.shape == (len(STEMS), 6)
    assert np.allclose(first, 1.0)
    second = ola.add(np.ones((len(STEMS), SEGMENT), dtype=np.float32))
    assert second.shape[1] == 6
    assert np.allclose(second, 1.0)
    tail = ola.finish()
    assert tail.shape[1] == 2
    assert np.allclose(tail, 1.0)


def test_push_releases_audio_only_after_a_window_is_full():
    session = RealtimeStems(_Echo())  # type: ignore[arg-type]
    session.start()
    early = session.push(np.ones(5, dtype=np.float32))
    assert all(len(samples) == 0 for samples in early.values())
    ready = session.push(np.ones(3, dtype=np.float32))
    assert len(ready["drums"]) == 6
    assert np.allclose(ready["vocals"], 1.0, atol=1e-5)
    tail = session.finish()
    heard = np.concatenate([ready["drums"], tail["drums"]])
    assert len(heard) == 8
    assert np.allclose(heard, 1.0, atol=1e-5)
    session.close()


def test_the_tail_stays_aligned_with_the_input():
    session = RealtimeStems(_Echo())  # type: ignore[arg-type]
    session.start()
    audio = np.arange(10, dtype=np.float32)
    first = session.push(audio)
    tail = session.finish()
    heard = np.concatenate([first["drums"], tail["drums"]])
    assert len(heard) == 10
    assert np.allclose(heard, audio, atol=1e-4)
    session.close()
