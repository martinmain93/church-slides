"""`MatchingEngine` protocol + `ParticleFilterEngine`, the engine the app runs.

The Director doesn't care how decisions are produced — it needs
`update(event) -> decision?` and `set_current_item(idx)` — so that is a
Protocol.

`ParticleFilterEngine` wraps the (tracker, scheduler) pair into one object
satisfying it. The tracker holds the belief; the scheduler converts each
belief snapshot into an anticipating `MatcherDecision` (or `None`). The name
is historical: it was built around the Phase 6 particle filter, and the
tracker has been the dense forward filter (`tracker.forward`) since
2026-08-29. The particle filter and the Phase 7 lost-mode add-ons that
hung off this class are in research/dead_ends/particle_filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.decision.scheduler import LeadTimeScheduler
from auto_presenter.index.types import IndexedSetlist
from auto_presenter.matcher.types import MatcherDecision
from auto_presenter.tracker.forward import ForwardTracker
from auto_presenter.tracker.types import BeliefState

log = logging.getLogger(__name__)


class MatchingEngine(Protocol):
    """The Director's view of a matcher: `ParticleFilterEngine`, and the
    `FollowerEngine` built on it, or a test double.

    Keeps `current_item_idx` as a writable focus and produces a
    `MatcherDecision | None` per transcript event.
    """

    current_item_idx: int

    def update(self, event: TranscriptEvent) -> MatcherDecision | None: ...

    def set_current_item(self, item_idx: int) -> None: ...

    def commit_external(self, item_idx: int, slide_idx: int) -> None:
        """Operator hard-committed to a specific slide — re-anchor.

        Distinct from `set_current_item` because operators can override
        within the current item too.
        """
        ...

    def reindex(self, setlist: IndexedSetlist, current_item_idx: int) -> None:
        """Swap in a freshly-built index (operator edited the setlist).

        Re-anchors to `current_item_idx` so tracking resumes where the
        operator is rather than restarting from the first slide.
        """
        ...


@dataclass
class ParticleFilterEngine:
    """Glue (tracker, scheduler) into a `MatchingEngine`.

    Each call to `update` advances the tracker, snapshots the belief, then
    asks the scheduler if a commit should fire. The scheduler's
    `note_external_commit` is used on operator overrides so the scheduler
    doesn't immediately re-fire the same target after the operator has
    manually advanced.
    """

    filter: ForwardTracker
    scheduler: LeadTimeScheduler

    _last_belief: BeliefState | None = field(default=None, init=False, repr=False)
    #: What the audience is currently seeing.
    _displayed: tuple[int, int] | None = field(default=None, init=False, repr=False)

    @property
    def current_item_idx(self) -> int:
        return self.filter.current_item_idx

    @current_item_idx.setter
    def current_item_idx(self, value: int) -> None:
        # Director writes through this; route via `set_current_item`
        # so the filter resets state alongside the scheduler.
        self.set_current_item(value)

    @property
    def belief(self) -> BeliefState:
        """Most recent belief — exposed for the WS belief broadcast."""
        return self._last_belief if self._last_belief is not None else self.filter.belief

    def update(self, event: TranscriptEvent) -> MatcherDecision | None:
        if event.is_final:
            self.scheduler.note_words(len(event.words))
        belief = self.filter.update(event)
        self._last_belief = belief
        decision = self.scheduler.on_belief(belief)
        if decision is None:
            return None
        return self._commit(
            decision.item_idx,
            decision.slide_idx,
            decision.t_seconds,
            decision.confidence,
            already_recorded=True,
        )

    def tick(self, t_seconds: float) -> MatcherDecision | None:
        """Let the freeze watchdog act when no transcript is arriving.

        `update` is the only path that normally reaches the scheduler, so a
        display frozen *because evidence stopped* had nothing to notice it —
        the watchdog ran on the clock that had stalled. This drives it from
        the audio clock instead. See `LeadTimeScheduler.tick`.
        """
        decision = self.scheduler.tick(t_seconds)
        if decision is None:
            return None
        return self._commit(
            decision.item_idx,
            decision.slide_idx,
            decision.t_seconds,
            decision.confidence,
            already_recorded=True,
        )

    def _commit(
        self,
        item_idx: int,
        slide_idx: int,
        t_seconds: float,
        confidence: float,
        *,
        already_recorded: bool = False,
    ) -> MatcherDecision:
        """Publish a slide change and re-anchor everything that tracks it.

        Re-anchoring the tracker on its own commits keeps the scheduler's
        "we've moved to the next slide" view in sync with the tracker's belief
        — without it the tracker would still be drifting through the
        *previous* slide while the UI shows the new one.
        """
        if getattr(self.filter, "reanchor_on_commit", True):
            self.filter.commit_slide(item_idx, slide_idx)
        if not already_recorded:
            # A decision that came from the scheduler is already in its own
            # books; re-recording it notifies `SongProgress` twice, and a
            # slide seen twice reads as a repeat.
            self.scheduler.note_external_commit(item_idx, slide_idx)
        self._displayed = (item_idx, slide_idx)
        return MatcherDecision(
            item_idx=item_idx,
            slide_idx=slide_idx,
            t_seconds=t_seconds,
            confidence=confidence,
        )

    def set_current_item(self, item_idx: int) -> None:
        self.filter.set_current_item(item_idx)
        self.scheduler.reset()

    def note_music_change(self, t_seconds: float) -> None:
        """The music changed key or tempo (`music.change_watch`): a song
        change the words propose may land a little sooner."""
        self.scheduler.note_music_change(t_seconds)

    def note_displayed(self, item_idx: int, slide_idx: int) -> None:
        """Session start: this is what the screen already shows.

        Tells the scheduler only. It otherwise starts believing nothing is
        displayed, and with nothing displayed there is no song to leave, so the
        first commit skipped `_cross_song_ready` entirely. Traced 2026-09-29: a
        stray "!" as the session's first transcript event put Amazing Grace
        up over Reckless Love. The filter is deliberately left alone (no
        `commit_slide` collapse): its standing chance of any song's opening
        is wanted, and this only makes acting on it clear the song-change bar.
        """
        if item_idx != self.current_item_idx:
            self.set_current_item(item_idx)
        self.scheduler.note_external_commit(item_idx, slide_idx)
        self._displayed = (item_idx, slide_idx)

    def note_not_shown(self, item_idx: int, slide_idx: int) -> None:
        """A move this engine decided did not reach the screen, which still
        shows `item_idx`/`slide_idx`. Puts the scheduler's record back so it
        decides the move again (and its gates and watchdog reason from the
        real screen). The tracker is left alone: its belief is still right."""
        self.note_displayed(item_idx, slide_idx)

    def commit_external(self, item_idx: int, slide_idx: int) -> None:
        """Operator-driven slide change → re-anchor both filter and scheduler.

        Called by the server's slide-change fan-out after the Director
        has noted the override. Without this the tracker's belief stays on
        the matcher's previous one, fights the operator on
        the next belief snapshot, and the scheduler tries to re-fire its
        last commit.
        """
        self.filter.commit_slide(item_idx, slide_idx)
        self.scheduler.note_external_commit(item_idx, slide_idx)
        # Re-read the belief. An override moves the tracker without an audio
        # event, and `belief` serves the cached snapshot, so without this a
        # caller reading it straight afterwards — the WS broadcast, the
        # inspector — sees where we were before the operator spoke, until the
        # next transcript event arrives up to a second later.
        self._last_belief = self.filter.belief
        self._displayed = (item_idx, slide_idx)

    def reindex(self, setlist: IndexedSetlist, current_item_idx: int) -> None:
        anchor = max(0, min(current_item_idx, max(0, setlist.item_count - 1)))
        self.filter.reindex(setlist, anchor)
        self.scheduler.setlist = setlist
        self.scheduler.reset()
        self._last_belief = self.filter.belief
