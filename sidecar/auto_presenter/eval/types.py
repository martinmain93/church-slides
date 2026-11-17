"""Data types for the evaluation harness.

A `Recording` is a single replayable service. It carries:
  - the ground-truth slide-change timeline (always present)
  - an optional path to a WAV file with the captured audio (lands in Phase 4)
  - the setlist that was being presented
  - optional deviation markers annotating known band reorders / chorus repeats
    (used by `recovery_time_ms`)

A `Decision` is what some pipeline produced when run against the recording —
either a matcher commit or a human override. We compare the decision stream
against the ground-truth stream to compute metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class GroundTruthEvent(BaseModel):
    """An authoritative slide-change at a point in time."""

    t_seconds: float
    item_idx: int
    slide_idx: int
    #: What moved the screen. `RecordingSink.record_slide_change` has always
    #: been handed this and always discarded it, so a display log could say
    #: *that* the screen changed and never *why* — and "was that the matcher,
    #: a spoken request, or the operator's hand?" is the first question of
    #: every live diagnosis. Three separate investigations stalled on it.
    #:
    #: Optional, because every recording written before 2026-09-15 lacks it
    #: and those are the golden set.
    source: Literal["manual", "override", "ai", "voice"] | None = None


class Decision(BaseModel):
    """Pipeline output — what slide it committed to at a point in time."""

    t_seconds: float
    item_idx: int
    slide_idx: int
    source: Literal["ai", "override"] = "ai"


class DeviationMarker(BaseModel):
    """Hand-annotated band deviation for recovery-time analysis."""

    t_seconds: float
    kind: Literal["chorus_repeat", "jump", "reorder", "unplanned"]


class SetlistItemSnapshot(BaseModel):
    """One item's tokenized slides as captured at recording time.

    Mirrors `SetlistItemContent` from the wire protocol but lives here
    to avoid a recording → protocol dependency. The shape is the same
    so a Recording's `setlist_content` can be passed directly into
    `index.build_index(SetlistContent(items=...))` for baseline replay.
    """

    item_idx: int
    song_id: str
    slides: list[list[str]]
    slide_sections: list[str] = []
    title: str = ""
    #: See `protocol.SetlistItemContent.spoken`.
    spoken: bool = False


class SessionConfig(BaseModel):
    """The settings a recording was captured under.

    Without this a live trace cannot be analysed on the variables being tuned.
    A run reported as "lead time 3000, fast-mode on" could not be confirmed
    from its own recording, so a diagnosis had to guess at the configuration
    it was diagnosing — and the guess is exactly where the answer lives when
    a replay behaves differently from the session it replays.

    Deliberately the *effective* values read off the live objects rather than
    what a request asked for: a slider that silently clamped its value is the
    kind of thing this has to be able to catch, and it has happened.
    """

    #: "mock" | "whisper" | "groq" | "mlx", and the model within it.
    asr_backend: str = ""
    asr_model: str = ""
    #: "shadow" | "active" — whether decisions reached the screen at all.
    ai_mode: str = ""
    #: Scheduler/tracker knobs, as a plain dict so adding one does not
    #: invalidate every recording already on disk.
    tunables: dict[str, float] = Field(default_factory=dict)
    #: Sliding-window ASR geometry, which sets the delivery lag.
    window_seconds: float = 0.0
    step_seconds: float = 0.0
    stable_margin_seconds: float = 0.0
    #: Whether the score follower decided slides, and for which setlist items
    #: (songs with a usable lead sheet).
    score_follower: bool = False
    follower_items: list[int] = Field(default_factory=list)
    #: Free-form, for anything added later without a schema change.
    extra: dict[str, str] = Field(default_factory=dict)


class Recording(BaseModel):
    """Single replayable service.

    `audio_path` is optional in Phase 3 (no audio capture yet) — Phase 4 fills
    it in for real recordings, and the replay engine streams audio frames
    when present. Phase-3-only recordings can still drive metric tests by
    feeding a synthetic decision stream against the ground truth.

    `setlist_content` is the per-item tokenized slides as they were at
    recording time. Phase 5+ baseline runs feed this back into the
    matcher so the same words are scored against; without it, the
    recording is only useful for measuring slide-decision metrics
    against synthetic pipelines.
    """

    id: str
    setlist_id: str
    started_at_ms: int
    duration_seconds: float
    ground_truth: list[GroundTruthEvent]
    deviation_markers: list[DeviationMarker] = Field(default_factory=list)
    audio_path: str | None = None
    notes: str = ""
    setlist_content: list[SetlistItemSnapshot] = Field(default_factory=list)
    #: What produced this trace. Absent on everything recorded before
    #: 2026-09-08, hence optional.
    config: SessionConfig | None = None


class GoldenAnnotation(BaseModel):
    """Human-corrected ground truth for a recording.

    Written by the Golden Recording Editor to a sibling `golden.json` so the
    raw operator capture in `recording.json` stays untouched. When present,
    `eval.replay.load_recording` overlays this `ground_truth` onto the base
    Recording, so all eval/baseline replays use the corrected timeline.

    `approved` is the human's signature on the timeline. It is deliberately
    separate from the file merely existing: machine-estimated annotations
    (see `research/eval/bootstrap_golden.py`) also write a `golden.json`, and
    saving mid-review shouldn't claim the recording is finished. Only an
    explicit approval in the editor sets it.
    """

    recording_id: str
    ground_truth: list[GroundTruthEvent]
    notes: str = ""
    edited_at_ms: int
    approved: bool = False


class MetricResult(BaseModel):
    """Single metric value with optional confidence interval."""

    name: str
    value: float
    unit: str = ""
    ci: tuple[float, float] | None = None
    n: int | None = None


@dataclass(frozen=True)
class CueTiming:
    """The sung moments around one golden cue (seconds, the golden's clock).

    Made by `eval.sung_timing.cue_timings`, read by `metrics.perceptual_score`.
    """

    cue: float
    #: The new slide's first word starts.
    first_word: float
    #: The previous slide's last line starts (<= `first_word`).
    last_line: float
    first_word_measured: bool
    last_line_measured: bool
