"""One performance of one song, as it is stored: small, versioned, anchored
to lyric text rather than slide numbers (slides get edited, split and
reordered; a row whose text no longer binds is dropped on load, not
misapplied)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from auto_presenter.follower import score as sc

SUMMARY_VERSION = 1


def slide_key(words: list[str]) -> str:
    """A slide's text identity: its normalised words, hashed."""
    text = " ".join(w for w in (sc.norm(x) for x in words) if w)
    return hashlib.sha1(text.encode()).hexdigest()[:12] if text else ""


@dataclass
class PassRecord:
    """One pass through one slide's line (`lookback.Pass`), times relative to
    the performance's first heard word."""

    slide: int
    key: str
    t_start: float
    t_end: float
    #: (word index in the slide, seconds after `t_start`).
    onsets: list[tuple[int, float]]
    confidence: float


@dataclass
class PerformanceSummary:
    id: str
    song_id: str
    #: Wall clock (ms since the epoch) the song was first shown.
    started_at_ms: int
    #: "live" (a real room), "injected" (dev audio injection), ...: only
    #: "live" rows are learned from.
    provenance: str
    #: Who moved the slides: "operator", "ai", "mixed" or "none".
    led_by: str
    #: How much this performance counts (`labels.performance_weight`).
    weight: float
    passes: list[PassRecord]
    #: The operator's cleaned slide changes: (seconds after the first heard
    #: word, slide, source).
    operator: list[tuple[float, int, str]]
    #: Share of the look-back's confident line changes the operator made too;
    #: None when the operator made none.
    agreement: float | None
    #: Text identity of every slide at the time, by slide index.
    slide_keys: list[str]
    heard_words: int
    #: False while the song is still being sung (a draft).
    final: bool = False
    version: int = SUMMARY_VERSION
    notes: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, data: str) -> PerformanceSummary | None:
        """None for a row this version cannot read."""
        try:
            raw = json.loads(data)
            if not isinstance(raw, dict) or raw.get("version") != SUMMARY_VERSION:
                return None
            raw["passes"] = [
                PassRecord(**{**p, "onsets": [tuple(o) for o in p["onsets"]]})
                for p in raw["passes"]
            ]
            raw["operator"] = [tuple(o) for o in raw["operator"]]
            return cls(**raw)
        except (ValueError, TypeError, KeyError, AttributeError):
            # a row this version can't read (malformed, written by another
            # build) hides only itself, not the song's whole history
            return None
