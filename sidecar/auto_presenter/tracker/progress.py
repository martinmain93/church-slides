"""How predictable is the *next* slide right now?

Localization ("where are we") and prediction ("where next") are different
questions with different evidence, and conflating them is what made the
tracker either reckless or frozen depending on how it was tuned.

Prediction is close to deterministic, and which regime applies is knowable:

  - **First pass through the song, nothing repeated yet.** The next slide is
    almost certainly the next one in order. Commit to it as soon as the
    current slide is ending — do not wait for evidence that has not arrived,
    because waiting means being late on the ~90% case to hedge against the
    ~10% one.

  - **Past the end, or standing where the band has jumped from before.**
    Order no longer predicts anything *here*: the end of a bridge that was
    exited backwards once will likely be exited backwards again. The honest
    answer is that we do not know, and the right move is to wait for the first
    words of whatever comes next.

Ambiguity is tracked per *position*, not per song. A song that repeats its
bridge four times deviates only at the bridge's end — the slides within each
pass still run in order, and treating the whole remainder of the song as
unpredictable would make every one of those transitions needlessly late. What
is learned is narrower and more useful: *this* slide is one the band leaves
unpredictably.

The consequence is a deliberate, correct-by-design error: on a deviation the
system advances to the next ordered slide, is briefly wrong, and corrects.
That is what a good human operator does too — they cue the expected slide and
fix it when the band surprises them. It should be judged on how fast it
corrects (`recovery_time_ms`), not on the existence of the correction, which
is why `wrong_slide_rate` takes a post-deviation grace window.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SongProgress:
    """Tracks whether slide order is still a reliable predictor.

    Fed the committed slide sequence — what has actually been displayed —
    rather than the belief, because the question is about the *song's*
    structure so far, not the tracker's confidence.
    """

    #: Slides displayed so far for the current item, in order.
    _seen: list[int] = field(default_factory=list, init=False)
    _item_idx: int | None = field(default=None, init=False)
    _deviated: bool = field(default=False, init=False)
    #: Slides the band has previously left by something other than +1. These
    #: are the positions where order stops being a safe assumption.
    _jump_origins: set[int] = field(default_factory=set, init=False)

    @property
    def deviated(self) -> bool:
        """Whether this song has already departed from straight order."""
        return self._deviated

    @property
    def slides_seen(self) -> int:
        return len(self._seen)

    def reset(self) -> None:
        self._seen.clear()
        self._item_idx = None
        self._deviated = False
        self._jump_origins.clear()

    def note_commit(self, item_idx: int, slide_idx: int) -> None:
        """Record a slide actually being displayed."""
        if item_idx != self._item_idx:
            # New song: order is predictive again from the top.
            self._item_idx = item_idx
            self._seen = [slide_idx]
            self._deviated = False
            self._jump_origins.clear()
            return
        if self._seen:
            previous = self._seen[-1]
            # A repeat or a jump backwards is a deviation. A forward skip is
            # not: slides get skipped when a verse is cut, and order still
            # predicts what follows.
            if slide_idx <= previous:
                self._deviated = True
                self._jump_origins.add(previous)
        self._seen.append(slide_idx)

    def order_is_predictive(
        self, current_slide_idx: int, *, at_last_slide: bool
    ) -> bool:
        """Whether to commit the next ordered slide without waiting.

        False at the final slide (nothing to assume), and false when standing
        on a slide the band has previously left unpredictably.
        """
        if at_last_slide:
            return False
        return current_slide_idx not in self._jump_origins
