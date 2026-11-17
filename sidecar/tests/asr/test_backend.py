"""MockASRBackend behavior."""

from __future__ import annotations

import numpy as np

from auto_presenter.asr.backend import MockASRBackend, schedule_from_words
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import AudioFrame


def make_frame(t_start: float, t_end: float) -> AudioFrame:
    n = int((t_end - t_start) * 16_000)
    return AudioFrame(
        samples=np.zeros(max(n, 1), dtype=np.float32),
        t_start=t_start,
        t_end=t_end,
    )


def test_emits_scheduled_event_after_trigger_time():
    schedule = [
        (
            0.5,
            TranscriptEvent(
                words=[Word(text="hello", t_start=0.0, t_end=0.5)],
                is_final=True,
            ),
        )
    ]
    backend = MockASRBackend(schedule=schedule)
    frames = [make_frame(0.0, 0.4), make_frame(0.4, 0.6)]
    events = list(backend.transcribe(iter(frames)))
    assert len(events) == 1
    assert events[0].words[0].text == "hello"


def test_does_not_emit_before_trigger_time():
    schedule = [
        (
            10.0,
            TranscriptEvent(
                words=[Word(text="future", t_start=10.0, t_end=10.2)],
                is_final=True,
            ),
        )
    ]
    backend = MockASRBackend(schedule=schedule)
    frames = [make_frame(0.0, 0.5)]
    events = list(backend.transcribe(iter(frames)))
    # Frame stream ended before t=10; the event flushes at the end.
    assert len(events) == 1
    assert events[0].words[0].text == "future"


def test_multiple_events_emit_in_order():
    sched = schedule_from_words(
        [
            ("amazing", 0.0, 0.3),
            ("grace", 0.4, 0.7),
            ("how", 0.8, 1.0),
        ]
    )
    backend = MockASRBackend(schedule=sched)
    frames = [make_frame(i * 0.1, (i + 1) * 0.1) for i in range(15)]
    events = list(backend.transcribe(iter(frames)))
    texts = [e.words[0].text for e in events]
    assert texts == ["amazing", "grace", "how"]


def test_text_property_joins_words():
    ev = TranscriptEvent(
        words=[
            Word(text="amazing", t_start=0.0, t_end=0.3),
            Word(text="grace", t_start=0.4, t_end=0.7),
        ],
        is_final=True,
    )
    assert ev.text == "amazing grace"
    assert ev.t_start == 0.0
    assert ev.t_end == 0.7


def test_empty_event_text():
    ev = TranscriptEvent(words=[], is_final=False)
    assert ev.text == ""
    assert ev.t_start is None
    assert ev.t_end is None


def test_schedule_from_words_helper():
    sched = schedule_from_words([("foo", 0.0, 0.1), ("bar", 0.2, 0.3)])
    assert len(sched) == 2
    assert sched[0][0] == 0.1  # trigger at t_end
    assert sched[0][1].words[0].text == "foo"
    assert sched[1][1].is_final


def test_backend_name_propagates():
    backend = MockASRBackend(backend_name="test-mock")
    assert backend.name() == "test-mock"
