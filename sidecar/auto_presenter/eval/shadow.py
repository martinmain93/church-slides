"""Shadow-mode wrapper for an AI matcher.

The contract is the one the plan calls out in PHASE_3.md and PHASE_5.md:

    "During a real service, operator runs as usual (manual control);
     AI's decisions logged side-by-side for offline comparison."

`ShadowLogger` wraps a matcher's decision callback. While shadow-mode is
on, every decision the matcher would have emitted is written to a JSONL
file alongside the human-operator's ground-truth events. The
presentation state machine never sees the decision — the UI stays in
manual mode. After the service ends, the log can be loaded by the eval
harness and replayed against the ground truth using the standard
metrics.

The wrapper is deliberately tiny: it owns no matcher state and makes no
threading assumptions. Pass it a sink to record into (file or in-memory
list) and call `record_decision()` whenever the matcher commits.
`record_override()` is for the parallel signal from the UI when the
operator clicks/keys a slide change — together they reconstruct the
side-by-side log the eval harness expects.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from auto_presenter.eval.types import Decision, GroundTruthEvent, Recording

log = logging.getLogger(__name__)


DecisionSource = Literal["ai", "override"]


@dataclass(frozen=True)
class ShadowEntry:
    """One decision or override event captured during a shadow-mode session."""

    t_seconds: float
    item_idx: int
    slide_idx: int
    source: DecisionSource
    confidence: float = 1.0
    note: str = ""

    def to_decision(self) -> Decision:
        return Decision(
            t_seconds=self.t_seconds,
            item_idx=self.item_idx,
            slide_idx=self.slide_idx,
            source=self.source,
        )


class ShadowSink(Protocol):
    """Anything that can persist `ShadowEntry`s."""

    def write(self, entry: ShadowEntry) -> None: ...


class InMemorySink:
    """Collect entries in a list. Used by tests + interactive replay."""

    def __init__(self) -> None:
        self.entries: list[ShadowEntry] = []
        self._lock = threading.Lock()

    def write(self, entry: ShadowEntry) -> None:
        with self._lock:
            self.entries.append(entry)


class JsonlSink:
    """Append each entry as a JSON line to a file.

    Writes are line-buffered so a crash mid-service still leaves a
    well-formed prefix on disk. The file is opened lazily on the first
    write so creating a sink doesn't touch the filesystem.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._handle = None

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def write(self, entry: ShadowEntry) -> None:
        with self._lock:
            if self._handle is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Line buffering (`buffering=1`) is only well-defined for
                # text-mode files, which is what we want here.
                self._handle = self.path.open("a", buffering=1, encoding="utf-8")
            line = json.dumps(
                {
                    "t_seconds": entry.t_seconds,
                    "item_idx": entry.item_idx,
                    "slide_idx": entry.slide_idx,
                    "source": entry.source,
                    "confidence": entry.confidence,
                    "note": entry.note,
                }
            )
            self._handle.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None


class ShadowLogger:
    """Side-by-side logger for AI decisions vs operator overrides.

    Time origin: the logger captures a monotonic `t0` at construction.
    All entries are stamped relative to that origin, so the resulting
    log lines up frame-for-frame with a `Recording` started at the same
    moment (which is the common case during a real shadow-mode service).
    """

    def __init__(
        self,
        sink: ShadowSink,
        *,
        t0: float | None = None,
    ) -> None:
        self._sink = sink
        self._t0 = t0 if t0 is not None else time.monotonic()

    @property
    def t0(self) -> float:
        return self._t0

    def record_decision(
        self,
        item_idx: int,
        slide_idx: int,
        *,
        confidence: float = 1.0,
        note: str = "",
    ) -> ShadowEntry:
        """Log a matcher decision. Never advances the UI — that's the point."""
        entry = ShadowEntry(
            t_seconds=time.monotonic() - self._t0,
            item_idx=item_idx,
            slide_idx=slide_idx,
            source="ai",
            confidence=confidence,
            note=note,
        )
        self._sink.write(entry)
        return entry

    def record_override(
        self,
        item_idx: int,
        slide_idx: int,
        *,
        note: str = "",
    ) -> ShadowEntry:
        """Log an operator-driven slide change (arrow key, click, etc.)."""
        entry = ShadowEntry(
            t_seconds=time.monotonic() - self._t0,
            item_idx=item_idx,
            slide_idx=slide_idx,
            source="override",
            confidence=1.0,
            note=note,
        )
        self._sink.write(entry)
        return entry


# ----------------------------------------------------------------- loading


def load_shadow_log(path: Path) -> list[ShadowEntry]:
    """Read a JSONL shadow log back into `ShadowEntry`s.

    Tolerant of trailing newlines and partial final lines (the latter
    can happen if the process crashed mid-write).
    """
    p = Path(path)
    out: list[ShadowEntry] = []
    if not p.exists():
        return out
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            log.warning("skipping malformed shadow log line: %r", line[:120])
            continue
        try:
            out.append(
                ShadowEntry(
                    t_seconds=float(data["t_seconds"]),
                    item_idx=int(data["item_idx"]),
                    slide_idx=int(data["slide_idx"]),
                    source=data.get("source", "ai"),
                    confidence=float(data.get("confidence", 1.0)),
                    note=str(data.get("note", "")),
                )
            )
        except (KeyError, ValueError, TypeError) as err:
            log.warning("skipping invalid shadow entry: %s (%r)", err, data)
    return out


# ----------------------------------------------------------------- adapters


def decisions_from_shadow(
    entries: Iterable[ShadowEntry],
    *,
    include_overrides: bool = False,
) -> list[Decision]:
    """Project a shadow log onto the `Decision` shape the eval harness uses.

    By default this only emits the AI's decisions — that's the stream
    you want to score against ground truth. Set `include_overrides=True`
    to interleave human events too (rare; mostly useful for sanity
    plots).
    """
    out: list[Decision] = []
    for e in entries:
        if e.source == "override" and not include_overrides:
            continue
        out.append(e.to_decision())
    return out


def ground_truth_from_shadow(entries: Iterable[ShadowEntry]) -> list[GroundTruthEvent]:
    """Project the override stream of a shadow log into ground-truth events.

    Useful when the recording has no annotated `recording.json` yet — the
    operator's clicks during the shadow run *are* ground truth, modulo
    minor offsets from anticipation. Phase 5 baseline runs use this to
    avoid waiting for hand-annotation before the first scoring pass.
    """
    return [
        GroundTruthEvent(
            t_seconds=e.t_seconds, item_idx=e.item_idx, slide_idx=e.slide_idx
        )
        for e in entries
        if e.source == "override"
    ]


def attach_shadow_to_recording(
    recording: Recording, shadow_path: Path
) -> tuple[Recording, list[Decision]]:
    """Convenience: load a shadow log alongside a recording.

    Returns the recording (unchanged) and the list of AI decisions
    parsed from `shadow_path`. The eval harness's metric functions take
    `(decisions, recording.ground_truth)` so the caller can pass these
    straight in.
    """
    entries = load_shadow_log(shadow_path)
    return recording, decisions_from_shadow(entries)
