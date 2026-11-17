"""Key and tempo changes in the live music: a hint that the band changed song.

One-directional on purpose. A *different* key or tempo is evidence the song
changed; the *same* key is no evidence of anything: worship sets are often
kept in one key so songs flow (both multi-song goldens: every song in G,
every song in E), and a band plays the same song in different keys on
different days. So nothing here ever says "same song"; a change only lowers
the bar for a song change the words already propose (`LeadTimeScheduler.
note_music_change`).

Sheet-free, from the analyser's outputs alone:

* **key**: BTC chord probabilities summed over a trailing window, scored
  against diatonic templates for the 12 major keys. A relative minor shares
  its chords, so it is the same class here: a change between relative keys is
  not counted, which is the conservative direction.
* **tempo**: the analyser's beat tracker. A change is a move of more than
  `TEMPO_JUMP` that isn't an octave misreading of the same pulse (3:2 is not
  folded: 120 -> 84 bpm is a real change and sits close to it).

Either counts only once the new value has held for `HOLD_S`, so a passing
chord or a fill doesn't fire it. Measured on the 8 goldens (offline chords,
offline pulse): 0.09 key and 0.11 tempo changes a minute away from song
changes (real in-song key lifts among them), and at the 7 song changes there
none of the sets changed key; tempo changed at 1. The golden case this is
for (a set that changes key between songs) is still to come.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from auto_presenter.music.btc import MAJMIN_LABELS

PITCHES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
#: Seconds of chords a key estimate reads.
WINDOW_S = 30.0
#: A new key or tempo must hold this long to count.
HOLD_S = 15.0
#: How often the key and tempo are re-read.
STEP_S = 1.0
#: Relative tempo move that counts as a change (octaves folded).
TEMPO_JUMP = 0.08


def key_templates() -> np.ndarray:
    """12 x 25: how much each BTC class says each major key (I IV V, ii iii vi)."""
    t = np.zeros((12, len(MAJMIN_LABELS)), np.float32)
    degrees = (
        (0, "", 1.0), (5, "", 1.0), (7, "", 1.0), (2, "m", 0.7), (4, "m", 0.5), (9, "m", 0.8)
    )
    for k in range(12):
        for deg, quality, w in degrees:
            t[k, MAJMIN_LABELS.index(PITCHES[(k + deg) % 12] + quality)] = w
    return t


def same_tempo(a: float, b: float) -> bool:
    r = max(a, b) / max(min(a, b), 1e-6)
    return any(abs(r / f - 1) <= TEMPO_JUMP for f in (1.0, 2.0, 4.0))


@dataclass(frozen=True)
class MusicChange:
    t_seconds: float
    kind: str  # "key" | "tempo"
    before: str
    after: str


@dataclass
class _Stable:
    """A value that changes only when a new one holds for `hold` seconds."""

    hold: float
    current: object = None
    _cand: object = None
    _since: float = 0.0

    def update(self, t: float, value, same) -> tuple[object, object] | None:
        if self.current is None:
            self.current = value
            return None
        if same(value, self.current):
            self._cand = None
            return None
        if self._cand is None or not same(value, self._cand):
            self._cand, self._since = value, t
            return None
        if t - self._since >= self.hold:
            before, self.current, self._cand = self.current, self._cand, None
            return before, self.current
        return None


@dataclass
class KeyTempoWatch:
    window_s: float = WINDOW_S
    hold_s: float = HOLD_S
    step_s: float = STEP_S
    _chords: deque = field(default_factory=deque, init=False)
    _sum: np.ndarray = field(default=None, init=False)  # type: ignore[assignment]
    _next: float = field(default=0.0, init=False)
    _key: _Stable = field(init=False)
    _tempo: _Stable = field(init=False)
    _templates: np.ndarray = field(default_factory=key_templates, init=False)

    def __post_init__(self) -> None:
        self._key = _Stable(self.hold_s)
        self._tempo = _Stable(self.hold_s)
        self._sum = np.zeros(len(MAJMIN_LABELS), np.float64)

    @property
    def key(self) -> str | None:
        return None if self._key.current is None else f"{PITCHES[int(self._key.current)]} major"

    def update(self, t: float, chord_probs: np.ndarray | None, bpm: float) -> list[MusicChange]:
        """Feed one analyser tick (audio time `t`); returns changes that just counted."""
        if chord_probs is not None:
            p = np.asarray(chord_probs, np.float64)
            self._chords.append((t, p))
            self._sum += p
        while self._chords and self._chords[0][0] < t - self.window_s:
            self._sum -= self._chords.popleft()[1]
        if t < self._next:
            return []
        self._next = t + self.step_s
        out: list[MusicChange] = []
        # a key needs most of a window of chords behind it
        if self._chords and t - self._chords[0][0] >= 0.8 * self.window_s:
            key = int(np.argmax(self._templates @ self._sum))
            moved = self._key.update(t, key, lambda a, b: a == b)
            if moved is not None:
                out.append(MusicChange(t, "key", PITCHES[int(moved[0])], PITCHES[int(moved[1])]))
        if bpm > 0:
            moved = self._tempo.update(t, float(bpm), same_tempo)
            if moved is not None:
                out.append(MusicChange(t, "tempo", f"{moved[0]:.0f}", f"{moved[1]:.0f}"))
        return out
