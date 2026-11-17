"""Cross-component: Replayer → ASR streaming wrapper → committed events.

Phase 4's success-criteria integration check is "replay every recording
through the ASR pipeline." This test runs that chain end-to-end using
the `MockASRBackend` (so we don't need a Whisper model to validate the
wiring), with the same `SlidingWindowWrapper` and `Replayer` types the
live audio session uses.
"""

from __future__ import annotations

from auto_presenter.asr.backend import MockASRBackend, schedule_from_words
from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.eval.replay import Replayer


def test_replay_through_streaming_mock_yields_committed_words(make_recording_session):
    recording = make_recording_session(
        audio_seconds=3.0,
        slide_changes=[(0, 0), (0, 1)],
    )

    schedule = schedule_from_words(
        [
            ("amazing", 0.5, 1.0),
            ("grace", 1.0, 1.4),
            ("how", 1.4, 1.7),
            ("sweet", 1.7, 2.1),
        ]
    )
    backend = SlidingWindowWrapper(
        backend=MockASRBackend(schedule=schedule),
        window_seconds=2.0,
        step_seconds=0.5,
        stable_margin_seconds=0.5,
    )

    replayer = Replayer(recording, realtime=False)
    events = list(backend.transcribe(replayer.audio_frames()))

    # The wrapper guarantees at least one is_final event with the trailing
    # words by end-of-stream flush.
    finals = [e for e in events if e.is_final]
    assert finals, f"expected committed events, got {events!r}"

    seen_words = {w.text for e in finals for w in e.words}
    assert {"amazing", "grace"} <= seen_words

    # Timestamps must stay monotonic across finals so the matcher can rely
    # on event ordering.
    starts = [e.t_start for e in finals if e.t_start is not None]
    assert starts == sorted(starts)


def test_streaming_wrapper_is_idempotent_across_recordings(make_recording_session):
    """Wrapper resets between calls — replaying twice yields the same events."""
    recording = make_recording_session(audio_seconds=2.0)
    schedule = schedule_from_words([("praise", 0.5, 0.9), ("him", 0.9, 1.2)])
    wrapper = SlidingWindowWrapper(
        backend=MockASRBackend(schedule=schedule),
        window_seconds=1.5,
        step_seconds=0.5,
        stable_margin_seconds=0.25,
    )

    first = list(wrapper.transcribe(Replayer(recording, realtime=False).audio_frames()))
    second = list(wrapper.transcribe(Replayer(recording, realtime=False).audio_frames()))

    def _normalize(events):
        return [
            (e.is_final, tuple((w.text, round(w.t_start, 3)) for w in e.words))
            for e in events
        ]

    assert _normalize(first) == _normalize(second)
