"""ASR backend protocol + a deterministic mock.

Every backend converts a stream of `AudioFrame`s into a stream of
`TranscriptEvent`s. The protocol intentionally hides whether the
implementation is local (faster-whisper, MLX) or hosted (Groq), batch or
streaming. The mock keeps every downstream pipeline test deterministic and
fast.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import AudioFrame


class ASRBackend(Protocol):
    """Convert audio frames into a stream of transcript events."""

    def transcribe(self, frames: Iterable[AudioFrame]) -> Iterator[TranscriptEvent]: ...

    def name(self) -> str: ...


@dataclass
class MockASRBackend:
    """Backend that replays a canned schedule of transcript events.

    Used by:
        - matcher tests that don't need a real ASR model
        - integration tests of the eval harness that want determinism
        - the debug panel transcript tab during early development

    The schedule is a list of `(after_t, event)` pairs. As frames flow in,
    the backend yields any event whose `after_t` has been reached.
    Frames pace naturally because the eval harness's `FileCapture` enforces
    real-time playback — so a "schedule" of (1.0s, "amazing"), (2.0s, "grace")
    plays out roughly at those wall times during a real-time replay.
    """

    schedule: list[tuple[float, TranscriptEvent]] = field(default_factory=list)
    backend_name: str = "mock"

    def name(self) -> str:
        return self.backend_name

    def transcribe(
        self, frames: Iterable[AudioFrame]
    ) -> Iterator[TranscriptEvent]:
        # Keep the schedule sorted by trigger time so we can pop items in order.
        remaining = sorted(self.schedule, key=lambda x: x[0])
        for frame in frames:
            while remaining and remaining[0][0] <= frame.t_end:
                _, ev = remaining.pop(0)
                yield ev
        # Flush anything left over once the frame stream ends — useful for
        # synthetic schedules whose final timestamps slightly exceed the
        # audio duration.
        for _, ev in remaining:
            yield ev


def schedule_from_words(
    words: list[tuple[str, float, float]],
    *,
    segment_id: int = 0,
) -> list[tuple[float, TranscriptEvent]]:
    """Convenience: turn `[(text, t_start, t_end), ...]` into a per-word
    schedule that emits each word as its own final event."""
    out: list[tuple[float, TranscriptEvent]] = []
    for text, t_start, t_end in words:
        out.append(
            (
                t_end,
                TranscriptEvent(
                    words=[Word(text=text, t_start=t_start, t_end=t_end)],
                    is_final=True,
                    segment_id=segment_id,
                ),
            )
        )
    return out
