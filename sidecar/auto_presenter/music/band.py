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

`sustained` asks more, for the one use where a false "playing" moves the
screen on talk: the plan gate's band exemption (`decision.plan_gate`). On the
hosted-speech talk bench (2026-11-27) a meeting's room tone read 0.61 and 0.63
over 6 s at two false slide changes, with the talking score at 0.97; over
45 s it read 0.34 and 0.37. A band plays for minutes: at all 198 sung
within-song moves of the approved goldens that the 6 s reading exempts, the
45 s mean was still at least 0.5 (10th percentile 0.93). Song starts keep the
quick reading (`playing`), so a new song's first hearing isn't delayed.
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
#: Trailing seconds the harmony must also hold over for `sustained`.
SUSTAIN_S = 45.0


@dataclass
class BandPresence:
    window_s: float = WINDOW_S
    min_harmony: float = MIN_HARMONY
    sustain_s: float = SUSTAIN_S
    _rows: deque[tuple[float, float]] = field(default_factory=deque, init=False, repr=False)

    def observe(self, t_end: float, chord_probs: np.ndarray | None) -> None:
        """One analyser update. `chord_probs` is BTC's 25-class distribution
        (no-chord last); None (no learned recogniser) is not evidence."""
        if chord_probs is None:
            return
        self._rows.append((float(t_end), 1.0 - float(chord_probs[-1])))
        keep = max(self.window_s, self.sustain_s)
        while self._rows and self._rows[0][0] < t_end - keep:
            self._rows.popleft()

    def _mean(self, seconds: float) -> float | None:
        if not self._rows:
            return None
        newest = self._rows[-1][0]
        recent = [h for t, h in self._rows if t >= newest - seconds]
        return sum(recent) / len(recent)

    @property
    def harmony(self) -> float | None:
        """The window's mean harmonic probability; None before any evidence."""
        return self._mean(self.window_s)

    @property
    def playing(self) -> bool:
        """True only on evidence: no analyser or no audio yet is "not known
        to be playing", which costs a song change one more hearing (2 s)."""
        h = self.harmony
        return h is not None and h >= self.min_harmony

    @property
    def sustained(self) -> bool:
        """Playing, and over the last `sustain_s` too: for overriding a high
        talking score, where room tone mistaken for a band moves the screen."""
        h = self._mean(self.sustain_s)
        return self.playing and h is not None and h >= self.min_harmony

    def reset(self) -> None:
        self._rows.clear()
