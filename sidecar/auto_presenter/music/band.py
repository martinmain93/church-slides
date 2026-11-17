"""Is a band playing? From the live chord analyser, for decisions that must
not fire on talk.

The singing detector's talking score asks "do the words match the song on
screen?", so it rises at exactly the moment a band starts another song. A
sermon or a meeting has no instruments; the chord recogniser (BTC) says
"no chord" through speech. This keeps a trailing window of BTC's harmonic
probability (1 - P(no chord)) and reports the band as playing while its mean
clears a bar.

Measured 2026-11-05 through this analyser (6 s window, bar 0.5): 3 read
sermons (75 min) never read as a band; 5 meetings (190 min) did 0.4-30% of the
time (laptop hum, room tone), but at every one of the 21 false slide changes
the talk benchmark had made, the reading was 0.00-0.36. Every real song change
on the goldens read 0.70-1.00; overall the goldens read "no band" 6% of the
time (talk between songs, quiet intros).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

#: Trailing seconds of analyser updates averaged.
WINDOW_S = 6.0
#: Mean harmonic probability (1 - P(no chord)) over the window at or above
#: which the band counts as playing.
MIN_HARMONY = 0.5


@dataclass
class BandPresence:
    window_s: float = WINDOW_S
    min_harmony: float = MIN_HARMONY
    _rows: deque[tuple[float, float]] = field(default_factory=deque, init=False, repr=False)

    def observe(self, t_end: float, chord_probs: np.ndarray | None) -> None:
        """One analyser update. `chord_probs` is BTC's 25-class distribution
        (no-chord last); None (no learned recogniser) is not evidence."""
        if chord_probs is None:
            return
        self._rows.append((float(t_end), 1.0 - float(chord_probs[-1])))
        while self._rows and self._rows[0][0] < t_end - self.window_s:
            self._rows.popleft()

    @property
    def harmony(self) -> float | None:
        """The window's mean harmonic probability; None before any evidence."""
        if not self._rows:
            return None
        return sum(h for _, h in self._rows) / len(self._rows)

    @property
    def playing(self) -> bool:
        """True only on evidence: no analyser or no audio yet is "not known
        to be playing", which costs a song change one more hearing (2 s)."""
        h = self.harmony
        return h is not None and h >= self.min_harmony

    def reset(self) -> None:
        self._rows.clear()
