"""AI-assisted mode: when to tell the operator the screen is in the wrong place.

The operator drives the slides; the engine runs free alongside, exactly as it
would in Auto mode with nobody at the controls. This module compares where the
engine would have put the screen with where the operator has it, and decides
when that disagreement is worth a suggestion. See `Plan/AI_ASSISTED_MODE.md`.

It is deliberately a thin layer. Its only required input is the engine-move
stream (`Director.on_decision`), which every engine produces; ranked
candidates and the lost flag are optional extras. Nothing here knows how the
engine scores anything, so an engine upgrade doesn't touch it.

Pure and deterministic: events in, `AssistEvent`s out, no I/O and no clock of
its own. The server feeds it live; `research/eval/eval_assist.py` feeds it tapes.

Two tiers:

  - **next** — the engine moved to the slide after the displayed one and the
    operator still isn't there `next_late_s` later. Highlight only.
  - **place** — the engine moved anywhere else and stayed `place_dwell_s`.
    Highlight, and a notification if the tile isn't in view (the UI decides).

Comparisons are by *place*: identical-lyric slides of a song are one place,
because the room can't tell chorus 1 from chorus 3. Only engine moves create
suggestions; an operator move never does (the engine hears ~3 s late, so an
operator who moves first would otherwise be told to go back).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from auto_presenter.index.types import IndexedSetlist

Tier = Literal["next", "place"]
#: (item_idx, slide_idx) of the lowest-numbered slide with the same words.
Place = tuple[int, int]


@dataclass
class AssistConfig:
    """Starting points for the yardstick, not decisions."""

    #: Next tier: how long after the engine's move to the next slide the
    #: operator may still be on the old one before it pulses.
    next_late_s: float = 1.0
    #: Place tier: how long the engine must stay somewhere else first.
    place_dwell_s: float = 3.0
    #: Place tier: at least this long between two different suggestions.
    place_gap_s: float = 10.0
    #: How long a dismissed place stays quiet.
    mute_s: float = 30.0
    #: Engine moves below this confidence are not evidence enough.
    min_confidence: float = 0.0
    #: Alternatives offered under "Something else".
    max_alternatives: int = 3
    #: Place tier, when the engine *leapt* there: it had agreed with the
    #: screen, then jumped within the song to somewhere that isn't next. That
    #: is mostly the engine confused (measured on the tapes), so it must hold
    #: the claim this long — or confirm it by stepping on from there.
    leap_dwell_s: float = 3.0
    #: Next tier: engine moves below this confidence don't pulse. An early,
    #: unsure "next" is the engine guessing ahead, not the operator late.
    next_min_confidence: float = 0.0
    #: Place tier holds back while what's on screen is among the engine's own
    #: ranked guesses: it isn't sure the screen is wrong. Measured on the
    #: tapes, most false alarms were the band repeating a section, the
    #: operator rightly going back, and the engine (which expects songs to
    #: move forward) not following — with the repeat in its top guesses.
    screen_in_candidates_holds: bool = False
    #: Any operator move (not just one ahead of the engine) waits for the
    #: engine's next move before a suggestion can be raised.
    any_operator_move_waits: bool = False
    #: The next tier at all. Off in the Assist-only tier for slower computers
    #: (`server_follower.assist_tier`): "next is due" is a timing signal, and
    #: there it would always come late.
    next_tier: bool = True


@dataclass(frozen=True)
class Suggestion:
    id: int
    tier: Tier
    item_idx: int
    slide_idx: int
    t_raised: float
    #: Other places the band might be, best first: (item_idx, slide_idx).
    alternatives: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class AssistEvent:
    """`raise`/`update` carry the suggestion; `clear` its id; `reanchor` asks
    the caller to re-aim the engine on (item_idx, slide_idx) — a dismissal is
    the operator saying the screen is right."""

    kind: Literal["raise", "update", "clear", "reanchor"]
    suggestion: Suggestion | None = None
    cleared_id: int | None = None
    reason: str = ""
    item_idx: int | None = None
    slide_idx: int | None = None


@dataclass
class AssistInterpreter:
    setlist: IndexedSetlist
    config: AssistConfig = field(default_factory=AssistConfig)

    current: Suggestion | None = field(default=None, init=False)
    _displayed: tuple[int, int] | None = field(default=None, init=False)
    _displayed_t: float = field(default=float("-inf"), init=False)
    _engine: tuple[int, int] | None = field(default=None, init=False)
    _engine_t: float = field(default=float("-inf"), init=False)
    _engine_conf: float = field(default=1.0, init=False)
    #: When the engine started disagreeing with the screen, carried across
    #: moves that only step forward through the song — see `engine_move`.
    _pending_since: float | None = field(default=None, init=False)
    #: The current disagreement began with an unconfirmed engine leap.
    _leap: bool = field(default=False, init=False)
    _candidates: tuple[tuple[int, int], ...] = field(default=(), init=False)
    _lost: bool = field(default=False, init=False)
    _muted: dict[Place, float] = field(default_factory=dict, init=False)
    _last_place_raise_t: float = field(default=float("-inf"), init=False)
    _next_id: int = field(default=1, init=False)

    # ------------------------------------------------------------- inputs

    def displayed(self, t: float, item_idx: int, slide_idx: int) -> list[AssistEvent]:
        """The audience screen now shows this slide (any source)."""
        self._displayed = (item_idx, slide_idx)
        self._displayed_t = t
        return self._evaluate(t)

    def engine_move(
        self, t: float, item_idx: int, slide_idx: int, confidence: float = 1.0
    ) -> list[AssistEvent]:
        """The engine would have put this slide on screen now."""
        events: list[AssistEvent] = []
        place = self.place(item_idx, slide_idx)
        cur = self.current
        if cur is not None and place != self.place(cur.item_idx, cur.slide_idx):
            if cur.tier == "place" and self._is_successor(
                (cur.item_idx, cur.slide_idx), (item_idx, slide_idx)
            ):
                # The band is carrying on through the place we're pointing
                # at: follow it rather than clear and wait out a fresh dwell.
                self.current = Suggestion(
                    id=cur.id,
                    tier=cur.tier,
                    item_idx=item_idx,
                    slide_idx=slide_idx,
                    t_raised=cur.t_raised,
                    alternatives=self._alternatives_for((item_idx, slide_idx)),
                )
                events.append(AssistEvent(kind="update", suggestion=self.current))
            else:
                events.append(self._clear("engine_moved"))
        # One disagreement, not one per slide: an engine stepping forward
        # through a song the screen isn't following keeps the wait it has
        # already served. Restarting it on every move meant that with slides
        # a few seconds long it could never run out (seen live, 2026-10-16:
        # the band went 4 → 5 → 6 → 7 with the screen on 1, and nothing was
        # said until the song slowed down).
        prev = self._engine
        new = (item_idx, slide_idx)
        continuing = (
            prev is not None
            and self._pending_since is not None
            and (self.place(*new) == self.place(*prev) or self._is_successor(prev, new))
        )
        shown = self._displayed
        if shown is not None and self.place(*new) == self.place(*shown):
            self._pending_since = None
            self._leap = False
        elif not continuing:
            self._pending_since = t
            # A leap: from agreeing with the screen to somewhere in the same
            # song that isn't the next slide. (Another song isn't a leap here:
            # the engine already demands a lot before it changes song.)
            self._leap = (
                prev is not None
                and shown is not None
                and self.place(*prev) == self.place(*shown)
                and new[0] == shown[0]
                and not self._is_successor(shown, new)
            )
        elif prev is not None and self._is_successor(prev, new):
            # Stepping on from where it landed: the band really is there.
            self._leap = False
        self._engine = new
        self._engine_t = t
        self._engine_conf = confidence
        return events + self._evaluate(t)

    def candidates(
        self, t: float, ranked: list[tuple[int, int]], lost: bool = False
    ) -> list[AssistEvent]:
        """The engine's ranked guesses, best first. Optional input."""
        self._candidates = tuple(ranked)
        self._lost = lost
        return self._evaluate(t)

    def tick(self, t: float) -> list[AssistEvent]:
        """Time passed with nothing new; dwells may have run out."""
        return self._evaluate(t)

    def dismiss(self, t: float, suggestion_id: int) -> list[AssistEvent]:
        cur = self.current
        if cur is None or cur.id != suggestion_id:
            return []
        self._muted[self.place(cur.item_idx, cur.slide_idx)] = t + self.config.mute_s
        events = [self._clear("dismissed")]
        if self._displayed is not None:
            # The engine re-aims on the operator's slide, so it must forget
            # the move that raised this or the next tick would raise it again
            # the moment the mute expires.
            self._engine = self._displayed
            self._engine_t = t
            self._pending_since = None
            self._leap = False
            events.append(
                AssistEvent(
                    kind="reanchor",
                    item_idx=self._displayed[0],
                    slide_idx=self._displayed[1],
                )
            )
        return events

    # ------------------------------------------------------------- places

    def place(self, item_idx: int, slide_idx: int) -> Place:
        item = self.setlist.item(item_idx)
        if item is None:
            return (item_idx, slide_idx)
        same = item.identical_by_slide.get(slide_idx)
        return (item_idx, min(same)) if same else (item_idx, slide_idx)

    def _is_blank(self, item_idx: int, slide_idx: int) -> bool:
        item = self.setlist.item(item_idx)
        slide = item.slide(slide_idx) if item is not None else None
        return slide is None or not slide.words

    def _operator_ahead(self) -> bool:
        """The screen is past where the engine is: later in the same song, or
        on another song (the engine hasn't followed it there yet)."""
        assert self._engine is not None and self._displayed is not None
        if self._displayed[0] != self._engine[0]:
            return True
        return self._displayed[1] > self._engine[1]

    def _is_successor(self, a: tuple[int, int], b: tuple[int, int]) -> bool:
        """`b` is the next *place* after `a` in the same song: the slide after
        it, stepping over any that repeat `a`'s words ("so good to me" twice
        in a row is one place, and the slide after both is still "next")."""
        if a[0] != b[0]:
            return False
        item = self.setlist.item(a[0])
        if item is None:
            return False
        here = self.place(*a)
        j = a[1] + 1
        while j < len(item.slides) and self.place(a[0], j) == here:
            j += 1
        return self.place(*b) == self.place(a[0], j)

    # ------------------------------------------------------------- decision

    def _evaluate(self, t: float) -> list[AssistEvent]:
        cfg = self.config
        events: list[AssistEvent] = []
        if self._engine is None or self._displayed is None:
            return events
        engine_place = self.place(*self._engine)
        shown_place = self.place(*self._displayed)

        cur = self.current
        if cur is not None:
            target = self.place(cur.item_idx, cur.slide_idx)
            if target == shown_place:
                return [self._clear("operator_followed")]
            if engine_place == shown_place:
                return [self._clear("engine_agrees")]
            return events  # one at a time; it stands until it clears

        if engine_place == shown_place or self._is_blank(*self._engine):
            return events
        # An operator who moved *ahead* of the engine after its last move is
        # early, not wrong — the engine hears late. Its next move is fresh
        # evidence and asks again. Moving back, or sideways within the song,
        # is not "ahead": that disagreement counts straight away.
        if self._engine_t < self._displayed_t and (
            cfg.any_operator_move_waits or self._operator_ahead()
        ):
            return events
        # The engine on the slide before the displayed one: operator is ahead.
        if self._is_successor(self._engine, self._displayed):
            return events
        if self._engine_conf < cfg.min_confidence:
            return events
        if self._muted.get(engine_place, float("-inf")) > t:
            return events

        # Both waits restart when the operator moves: a few seconds' grace
        # while someone is clicking through slides.
        waited = t - max(self._engine_t, self._displayed_t)
        pending = t - max(
            self._pending_since if self._pending_since is not None else self._engine_t,
            self._displayed_t,
        )
        if self._is_successor(self._displayed, self._engine):
            if not cfg.next_tier:
                return events
            if waited < cfg.next_late_s or self._engine_conf < cfg.next_min_confidence:
                return events
            return [self._raise(t, "next")]
        dwell = cfg.leap_dwell_s if self._leap else cfg.place_dwell_s
        if self._lost or pending < dwell:
            return events
        if cfg.screen_in_candidates_holds and shown_place in {
            self.place(*c) for c in self._candidates
        }:
            return events
        if t - self._last_place_raise_t < cfg.place_gap_s:
            return events
        self._last_place_raise_t = t
        return [self._raise(t, "place")]

    def _raise(self, t: float, tier: Tier) -> AssistEvent:
        assert self._engine is not None
        item_idx, slide_idx = self._engine
        self.current = Suggestion(
            id=self._next_id,
            tier=tier,
            item_idx=item_idx,
            slide_idx=slide_idx,
            t_raised=t,
            alternatives=(
                self._alternatives_for(self._engine) if tier == "place" else ()
            ),
        )
        self._next_id += 1
        return AssistEvent(kind="raise", suggestion=self.current)

    def _clear(self, reason: str) -> AssistEvent:
        cur = self.current
        self.current = None
        return AssistEvent(
            kind="clear", cleared_id=cur.id if cur else None, reason=reason
        )

    def _alternatives_for(self, suggested: tuple[int, int]) -> tuple[tuple[int, int], ...]:
        seen = {self.place(*suggested)}
        if self._displayed is not None:
            seen.add(self.place(*self._displayed))
        out: list[tuple[int, int]] = []
        for item_idx, slide_idx in self._candidates:
            place = self.place(item_idx, slide_idx)
            if place in seen or self._is_blank(item_idx, slide_idx):
                continue
            seen.add(place)
            out.append((item_idx, slide_idx))
            if len(out) == self.config.max_alternatives:
                break
        return tuple(out)
