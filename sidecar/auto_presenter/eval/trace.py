"""Replay a recording through the matcher, recording everything it thought.

The eval harness reduces a run to a handful of numbers. Those numbers are how
you know something is wrong; they are useless for knowing *why*. Several
findings in this project — the scheduler never anticipating, the Phase 7
confidence monitor never firing — were invisible in the aggregates and obvious the moment the
per-update state was laid against the audio.

This produces that per-update state as a trace: one sample per belief update,
carrying the full internal state at that moment, plus the decisions the
scheduler emitted. Aligned to the recording's own timeline, so it can be
scrubbed against the waveform next to the human-approved ground truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from auto_presenter.asr.backend import MockASRBackend, schedule_from_words
from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.decision.production import build_engine
from auto_presenter.eval.replay import Replayer
from auto_presenter.eval.types import Decision, Recording
from auto_presenter.index.library import build_index
from auto_presenter.protocol import (
    ParticleFilterTunables,
    SetlistContent,
    SetlistItemContent,
)


@dataclass
class TraceSample:
    """Everything the matcher believed at one instant."""

    t_seconds: float
    #: Top hypothesis and how much of the cloud backs it.
    item_idx: int
    slide_idx: int
    marginal: float
    #: Runner-up hypotheses, so a near-miss is visible rather than implied.
    top_3: list[tuple[int, int, float]]
    ess: float
    #: Per-word match quality of this update under the belief — the signal
    #: that distinguishes "unsure" from "confidently wrong".
    evidence: float
    mode_position: float
    mode_rate: float
    is_lost: bool
    #: What the audience would be seeing at this moment.
    displayed_slide: int | None
    #: Words that arrived in this update.
    words: list[str] = field(default_factory=list)


@dataclass
class ModelTrace:
    recording_id: str
    samples: list[TraceSample]
    decisions: list[Decision]
    notes: str = ""


def synthesize_schedule(recording: Recording) -> list:
    """A transcript proxy derived from the recording's own ground truth.

    Each slide's words are spread evenly across the interval that slide was
    displayed for. This is a *perfect* transcript by construction, which is
    the point: it isolates the matcher from ASR error, so a trace shows what
    the tracker does when the words are not in question.

    Ported from `research/dead_ends/particle_filter/sweep_pf.py` (which kept its own copy in sync with
    `run_baseline.py` by hand). It belongs in the package: the sidecar serves
    traces to the inspector and cannot import from `scripts/`.
    """
    out: list = []
    snapshot_by_item: dict[int, list[list[str]]] = {
        item.item_idx: list(item.slides) for item in recording.setlist_content
    }
    sorted_gt = sorted(recording.ground_truth, key=lambda g: g.t_seconds)
    for i, ev in enumerate(sorted_gt):
        slides = snapshot_by_item.get(ev.item_idx)
        if slides is None or ev.slide_idx >= len(slides):
            continue
        words = slides[ev.slide_idx]
        if not words:
            continue
        next_t = (
            sorted_gt[i + 1].t_seconds
            if i + 1 < len(sorted_gt)
            else ev.t_seconds + max(2.0, len(words) * 0.4)
        )
        span = max(0.1, next_t - ev.t_seconds)
        step = span / max(1, len(words))
        for w_idx, word in enumerate(words):
            t_start = ev.t_seconds + w_idx * step
            out.extend(
                schedule_from_words(
                    [(word, t_start, t_start + step * 0.8)],
                    segment_id=i * 1000 + w_idx,
                )
            )
    return out



def real_asr_schedule(recording_dir: Path) -> list | None:
    """A transcript proxy built from a **real** ASR pass, if one is cached.

    `synthesize_schedule` derives words from the ground truth, which makes it
    a perfect transcript by construction. That is the right tool for asking
    "what does the tracker do when the words are not in question", and the
    wrong one for anything whose whole purpose is surviving words that *are*
    in question — mishearings, dropped words, a filter that is genuinely
    behind.

    `research/eval/bootstrap_golden.py` already caches a faster-whisper pass beside
    each recording as `transcript.json`, so the expensive part is done. This
    reads it back. Returns `None` when no cache exists; callers should fall
    back rather than silently measuring nothing.
    """
    cache = recording_dir / "transcript.json"
    if not cache.exists():
        return None
    try:
        data = json.loads(cache.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    words = data.get("words") or []
    out: list = []
    for i, w in enumerate(words):
        text = w.get("word") or w.get("text") or ""
        if not text:
            continue
        out.extend(
            schedule_from_words(
                [(text, float(w["start"]), float(w["end"]))], segment_id=i
            )
        )
    return out or None


def _content(recording: Recording) -> SetlistContent:
    return SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=s.item_idx,
                song_id=s.song_id,
                slides=list(s.slides),
                slide_sections=list(s.slide_sections),
                title=s.title,
            )
            for s in recording.setlist_content
        ]
    )


def trace_recording(
    recording: Recording,
    *,
    schedule=None,
    #: Production's value (`ParticleFilterTunables`), not the old 300.
    target_margin_ms: int = 0,
    #: ASR windowing. Defaults mirror `SlidingWindowWrapper`'s own, which is
    #: what production runs — nothing passes `wrapper_kwargs` in the live
    #: path. They were previously hardcoded here at 4.0/0.5/0.5, so every
    #: accuracy number measured through this tracer described a lower-latency
    #: configuration than the one shipping.
    window_seconds: float = 6.0,
    step_seconds: float = 1.0,
    stable_margin_seconds: float = 1.5,
) -> ModelTrace:
    """Replay `recording` through the engine the app runs and capture
    per-update internal state.

    `schedule` is the transcript source — a `MockASRBackend` schedule. Defaults
    to the ground-truth-derived proxy (fast, deterministic, isolates the
    matcher from ASR error); pass a real ASR transcript to trace the whole
    pipeline instead.

    The engine comes from `decision.production.build_engine`. Harnesses that
    built their own drifted off production five times (one drew a matcher
    the app does not run: 0.411 wrong-slide against 0.072).
    """
    if schedule is None:
        schedule = synthesize_schedule(recording)
    indexed = build_index(_content(recording))
    engine = build_engine(
        indexed, ParticleFilterTunables(target_margin_ms=target_margin_ms)
    )
    tracker = engine.filter

    backend = SlidingWindowWrapper(
        MockASRBackend(schedule=schedule),
        window_seconds=window_seconds,
        step_seconds=step_seconds,
        stable_margin_seconds=stable_margin_seconds,
    )

    samples: list[TraceSample] = []
    decisions: list[Decision] = []
    displayed: int | None = None

    for event in backend.transcribe(Replayer(recording, realtime=False).audio_frames()):
        if not event.is_final:
            continue
        decision = engine.update(event)
        if decision is not None:
            displayed = decision.slide_idx
            decisions.append(
                Decision(
                    t_seconds=decision.t_seconds,
                    item_idx=decision.item_idx,
                    slide_idx=decision.slide_idx,
                    source="ai",
                )
            )
        belief = engine.belief
        item_idx, slide_idx, marginal = belief.top_hypothesis
        samples.append(
            TraceSample(
                t_seconds=belief.t_seconds,
                item_idx=item_idx,
                slide_idx=slide_idx,
                marginal=marginal,
                top_3=list(belief.top_3),
                ess=belief.ess,
                evidence=tracker.last_evidence,
                mode_position=belief.mode_position,
                mode_rate=belief.mode_rate,
                is_lost=tracker.is_lost,
                displayed_slide=displayed,
                words=[w.text for w in event.words],
            )
        )

    return ModelTrace(
        recording_id=recording.id,
        samples=samples,
        decisions=decisions,
        notes=f"target_margin_ms={target_margin_ms}",
    )
