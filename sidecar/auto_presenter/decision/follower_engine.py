"""The production engine with the score follower deciding slides inside songs
that have a lead sheet.

`FollowerEngine` *is* a `ParticleFilterEngine` — built from the production
engine's own filter and scheduler (`wrap`) — so every server path that reads
the matcher (lag, telemetry, belief broadcasts, lost mode, singing,
spoken instructions, off-setlist recovery) keeps working unchanged. Only how
decisions are made differs:

* **which song** — still the production engine's call. Its decision passes
  through when it changes song, or when the song on screen has no follower;
* **which slide**, inside a song with a lead sheet — the follower's
  (`follower.live.LiveFollowers`). The production engine's within-song
  decisions are dropped, and it is told what the screen really shows
  (`note_displayed`), so its cross-song gates and watchdog reason from the
  truth rather than from a slide it only thinks it put up.

Follower decisions arrive from the capture thread; the server posts them to
the event loop and hands them to `accept_follower_decision` before the
Director, so everything here runs on the loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.decision.engine import ParticleFilterEngine
from auto_presenter.follower.live import LiveFollowers
from auto_presenter.matcher.types import MatcherDecision

log = logging.getLogger(__name__)


@dataclass
class FollowerEngine(ParticleFilterEngine):
    followers: LiveFollowers | None = None
    #: What is on screen, as far as this engine knows (item, slide).
    _screen: tuple[int, int] | None = field(default=None, init=False)

    @classmethod
    def wrap(cls, base: ParticleFilterEngine, followers: LiveFollowers) -> FollowerEngine:
        engine = cls(
            filter=base.filter,
            scheduler=base.scheduler,
            followers=followers,
        )
        engine._displayed = base._displayed
        engine._screen = base._displayed
        return engine

    def following(self, item_idx: int | None) -> bool:
        return item_idx is not None and self.followers is not None and self.followers.has(item_idx)

    # ------------------------------------------------------------ decisions

    def update(self, event: TranscriptEvent) -> MatcherDecision | None:
        decision = super().update(event)
        if self.followers is not None and event.is_final and event.words:
            self.followers.on_words(event.words)
        return self._route(decision)

    def tick(self, t_seconds: float) -> MatcherDecision | None:
        return self._route(super().tick(t_seconds))

    def _route(self, decision: MatcherDecision | None) -> MatcherDecision | None:
        if decision is None:
            return None
        current = self._screen[0] if self._screen is not None else None
        if decision.item_idx == current and self.following(current):
            # inside a followed song the follower decides; put the production
            # engine's idea of the screen back to what is actually shown
            assert self._screen is not None
            super().note_displayed(*self._screen)
            return None
        self._screen = (decision.item_idx, decision.slide_idx)
        if self.followers is not None:
            # a song change the production engine heard in the words: the band
            # is already inside that slide
            self.followers.enter(
                decision.item_idx, decision.slide_idx, decision.t_seconds, mid_slide=True
            )
        return decision

    def accept_follower_decision(self, decision: MatcherDecision) -> bool:
        """A slide change from the follower, on the event loop. False if it is
        stale — the screen has since moved to another song."""
        if self._screen is None or decision.item_idx != self._screen[0]:
            return False
        self._screen = (decision.item_idx, decision.slide_idx)
        super().note_displayed(decision.item_idx, decision.slide_idx)
        return True

    # ------------------------------------------------------------ the screen moved

    def note_displayed(self, item_idx: int, slide_idx: int) -> None:
        super().note_displayed(item_idx, slide_idx)
        self._screen = (item_idx, slide_idx)
        if self.followers is not None:
            self.followers.enter(item_idx, slide_idx)

    def note_not_shown(self, item_idx: int, slide_idx: int) -> None:
        """A move did not reach the screen: the scheduler, this engine and the
        follower all go back to what is shown. The follower keeps its place in
        the song (`note_shown`) unless the move left the song."""
        changed_song = self._screen is None or self._screen[0] != item_idx
        ParticleFilterEngine.note_displayed(self, item_idx, slide_idx)
        self._screen = (item_idx, slide_idx)
        if self.followers is not None:
            if changed_song:
                self.followers.enter(item_idx, slide_idx)
            else:
                self.followers.note_shown(item_idx, slide_idx)

    def commit_external(self, item_idx: int, slide_idx: int) -> None:
        super().commit_external(item_idx, slide_idx)
        self._screen = (item_idx, slide_idx)
        if self.followers is not None:
            self.followers.enter(item_idx, slide_idx)

    def set_current_item(self, item_idx: int) -> None:
        super().set_current_item(item_idx)
        if self._screen is None or self._screen[0] != item_idx:
            self._screen = (item_idx, 0)
            if self.followers is not None:
                self.followers.enter(item_idx, None)

    def replace_followers(self, followers: LiveFollowers | None) -> None:
        """New references (the setlist was edited): follow from the current screen."""
        old = self.followers
        self.followers = followers
        if old is not None and followers is None:
            old.detach()
        if followers is not None and old is not None:
            # the new followers join a session already under way: from its
            # clock, not from 0 (the first read-out stepped through every
            # frame since the session began, seconds under the lock an hour in)
            followers.take_over(old)
        if followers is not None and self._screen is not None:
            followers.enter(*self._screen)
