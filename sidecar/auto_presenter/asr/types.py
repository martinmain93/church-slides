"""Streaming ASR event types.

Two-tier model:
- `Word` — a single token with start/end timestamps and a confidence.
- `TranscriptEvent` — a contiguous run of words from the same segment.
  `is_final=False` events may be revised by future events with the same
  `segment_id`; `is_final=True` events are committed.

The matcher in Phase 5+ consumes the final stream and (optionally) treats
partials as soft evidence. Phase 6's particle filter relies on
word-level timestamps, which is why every word carries them — even
when an ASR backend doesn't natively produce them, the wrapper is
responsible for filling them in (e.g. via stable-ts).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Word(BaseModel):
    """One transcribed word with timing + confidence."""

    text: str
    t_start: float
    t_end: float
    confidence: float = 1.0
    #: Whisper's probability that the segment this word came from holds no
    #: speech at all, copied onto each of its words. `None` when the backend
    #: does not report one. See `asr.junk`.
    no_speech: float | None = None


class TranscriptEvent(BaseModel):
    """A contiguous run of words from a single ASR segment.

    Multiple events with the same `segment_id` are emitted as the
    sliding-window backend revises its hypothesis; only the final one
    (`is_final=True`) is authoritative.
    """

    words: list[Word] = Field(default_factory=list)
    is_final: bool = False
    segment_id: int = 0
    #: True when this event's last word is where a *backend* segment ended —
    #: a sentence boundary, as Whisper heard it.
    #:
    #: `segment_id` above counts emissions, not sentences, and
    #: `SlidingWindowWrapper` re-chunks words by its own stability clock, so a
    #: sentence longer than one step arrives in pieces with nothing marking
    #: where it began or ended. Measured on a live recording: Whisper returned
    #: "Can you go to the bridge of what a beautiful name?" as one clean
    #: 11-word segment, and the wrapper delivered it as four events of 4, 1, 3
    #: and 2 words. Anything downstream trying to act on a *sentence* was left
    #: inferring the boundary from time gaps.
    #:
    #: Defaults False, so a backend that reports no segmentation — and every
    #: hand-built event in the tests — behaves exactly as before.
    ends_segment: bool = False

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def t_start(self) -> float | None:
        return self.words[0].t_start if self.words else None

    @property
    def t_end(self) -> float | None:
        return self.words[-1].t_end if self.words else None
