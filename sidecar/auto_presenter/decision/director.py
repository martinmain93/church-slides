"""Director — bridges the matcher to the rest of the world.

Responsibilities:

  1. Subscribe to `MatcherDecision`s and (in `active` mode) translate
     them into `SlideChangeMessage` broadcasts via an injected
     `on_decision_broadcast` callback. In `shadow` mode the decision is
     logged but not broadcast — the UI stays manual. In `assist` mode it
     goes to `on_assist_move` instead: the operator drives, and the assist
     layer (`decision.assist`) decides whether it's worth a suggestion.

  2. Maintain the `idle | tracking | following_human` AI state. The
     state transitions are deliberately simple:

       - `idle`           — no transcript seen yet in this session, or
                            the matcher is set to `mode="off"`.
       - `tracking`       — matcher is producing decisions (or about to).
       - `following_human`— operator just took manual control via an
                            arrow-key / click; the matcher pauses
                            decision broadcasts for `cooldown_seconds`
                            and then returns to `tracking`.

  3. Broadcast `AIStatus` updates whenever the state or mode changes so
     the UI pill can render.

The director is decoupled from the WebSocket protocol: callers inject
`on_decision_broadcast` and `on_status_broadcast` callables. The server
module wires those to the actual `_broadcast(SlideChangeMessage)` /
`_broadcast(AIStatusEvent)` helpers. Tests pass list-append closures.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from auto_presenter.decision.engine import MatchingEngine
from auto_presenter.decision.types import AIMode, AIState, AIStatus
from auto_presenter.matcher.types import MatcherDecision

log = logging.getLogger(__name__)


DecisionBroadcast = Callable[[MatcherDecision], None]
StatusBroadcast = Callable[[AIStatus], None]


@dataclass
class Director:
    """Glue between the matcher, the AI state pill, and the WS layer.

    `matcher` is anything satisfying the `MatchingEngine` protocol (the
    production engine, `decision.production.build_engine`). The Director only reads/writes `current_item_idx` and consumes the
    decisions the matcher's `update` returns.
    """

    matcher: MatchingEngine
    on_decision_broadcast: DecisionBroadcast
    on_status_broadcast: StatusBroadcast
    mode: AIMode = "shadow"
    #: Assist mode's destination for engine moves. Optional: without it,
    #: assist behaves like shadow.
    on_assist_move: DecisionBroadcast | None = None
    #: How long to stay in `following_human` after an operator override.
    cooldown_seconds: float = 5.0

    _state: AIState = field(default="idle", init=False)
    _last_decision_t: float | None = field(default=None, init=False)
    _last_override_t: float | None = field(default=None, init=False)
    #: When the cooldown started, and the latest time we know of — both on
    #: the *session* clock, so the comparison has two ends that mean the same
    #: thing.
    #:
    #: It previously had one end on each of two clocks: the spoken-instruction
    #: path stamps the override with the audio clock (seconds into the
    #: session) while `_now_provider` is `time.monotonic` (seconds since
    #: boot), which compared a difference of some 2.7 million seconds against
    #: a 5 second cooldown.
    #:
    #: The session clock rather than the wall clock because every decision
    #: already carries one, and because a wall-clock cooldown makes replays
    #: depend on how fast they run — a faster-than-real-time eval would
    #: suppress commits a live service would allow.
    _cooldown_started: float | None = field(default=None, init=False)
    _session_now: float | None = field(default=None, init=False)
    _now_provider: Callable[[], float] = field(
        default_factory=lambda: _default_now,
        repr=False,
    )

    def __post_init__(self) -> None:
        # Broadcast the initial status so the UI starts from a known state.
        self._broadcast_status()

    # ----------------------------------------------------------- public API

    @property
    def state(self) -> AIState:
        """Currently observed state, with cooldown expiry applied lazily."""
        self._maybe_expire_cooldown()
        return self._state

    def set_mode(self, mode: AIMode) -> None:
        """Operator toggled off/shadow/active from Settings."""
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "off":
            self._state = "idle"
        elif self._state == "idle":
            self._state = "tracking"
        self._broadcast_status()

    def on_decision(self, decision: MatcherDecision) -> bool:
        """Called from the audio session whenever the matcher emits. True when
        the decision went to the screen (active mode, outside a cooldown):
        otherwise the caller puts the engine's record of the screen back."""
        if self.mode == "off":
            return False
        # In `following_human`, suppress AI commits during the cooldown.
        # The decision is still recorded for telemetry so the eval harness
        # can see what the matcher *would* have done — that's the whole
        # point of shadow mode.
        #
        # Through the property, not `_state`: expiry is applied lazily inside
        # it, and reading the raw field skips that. Nothing in this class used
        # to read `self.state`, so the only thing that could ever end a
        # cooldown was an outside caller happening to ask — and the one caller
        # sits behind a short-circuit (`"uncertain" if is_lost else
        # director.state`), so a lost engine never asked. A spoken song import
        # routes through `on_override`, and the screen then stayed frozen for
        # the rest of the service with every commit suppressed.
        self.note_session_time(decision.t_seconds)
        suppressed = self.state == "following_human"
        shown = False
        self._last_decision_t = decision.t_seconds

        if self.mode == "active" and not suppressed:
            try:
                self.on_decision_broadcast(decision)
                shown = True
            except Exception:  # pragma: no cover - defensive
                log.exception("decision broadcast callback raised")
        elif self.mode == "assist" and not suppressed and self.on_assist_move is not None:
            try:
                self.on_assist_move(decision)
            except Exception:  # pragma: no cover - defensive
                log.exception("assist move callback raised")

        # Transition idle → tracking on the first decision.
        if self._state == "idle":
            self._state = "tracking"
            self._broadcast_status()
        else:
            # Still publish on every decision so the UI gets fresh
            # `last_decision_t_seconds`. Cheap.
            self._broadcast_status()
        return shown

    def note_session_time(self, t_seconds: float) -> None:
        """The session clock moved on (a decision, or the 2 s health tick).
        Lets a cooldown end on time when no decision arrives, and gives an
        operator's click a time on the session clock."""
        if self._session_now is None or t_seconds > self._session_now:
            self._session_now = t_seconds
        if self._state == "following_human":
            self._maybe_expire_cooldown()

    def on_override(self, item_idx: int, slide_idx: int, *, t_seconds: float | None = None) -> None:
        """Called when the operator presses an arrow / clicks during a session.

        The presentation state machine has already advanced in the UI
        (Phase 2 keeps the source of truth on the client); the director
        just needs to (a) re-aim the matcher at the new item, and
        (b) flip the pill to "following human" for the cooldown window.
        """
        now = t_seconds if t_seconds is not None else self._now_provider()
        self._last_override_t = now
        # A click carries no session time of its own; the server stamps it
        # with the audio clock it last heard (`note_session_time`). Timing it
        # from the AI's last decision instead let the cooldown expire before
        # it began whenever the AI had last moved more than 5 s earlier.
        if t_seconds is not None:
            self.note_session_time(t_seconds)
        self._cooldown_started = self._session_now
        # Re-aim the matcher so the next decision targets the right slide.
        # Operators can override within the current item (slide change) as
        # well as across items (next song), so use the full re-anchor.
        try:
            self.matcher.commit_external(item_idx, slide_idx)
        except Exception:  # pragma: no cover - defensive
            log.exception("matcher.commit_external raised; falling back to set_current_item")
            if item_idx != self.matcher.current_item_idx:
                self.matcher.set_current_item(item_idx)
        # If the AI is off, the override is purely informational.
        if self.mode == "off":
            return
        self._state = "following_human"
        self._broadcast_status()

    @property
    def last_override_t(self) -> float | None:
        """When the operator last took over, or None if they haven't."""
        return self._last_override_t

    def now_seconds(self) -> float:
        """Exposed for tests that inject a deterministic clock."""
        return self._now_provider()

    def set_now_provider(self, fn: Callable[[], float]) -> None:
        """Override the wall-clock source. Tests use this."""
        self._now_provider = fn

    # ------------------------------------------------------------ internals

    def _maybe_expire_cooldown(self) -> None:
        if self._state != "following_human":
            return
        if self._session_now is None:
            # Nothing has decided anything yet, so there is no stream of
            # commits for the cooldown to be holding back.
            return
        if self._cooldown_started is None:
            # An override landed before any decision, so it had no session
            # clock to start from — an operator clicking a slide at the top of
            # a service. Start it from the first session time we learn about
            # rather than never, which would hold the cooldown open forever.
            self._cooldown_started = self._session_now
            return
        if self._session_now - self._cooldown_started < self.cooldown_seconds:
            return
        self._state = "tracking"
        self._broadcast_status()

    def _broadcast_status(self) -> None:
        status = AIStatus(
            mode=self.mode,
            state=self._state,
            last_decision_t_seconds=self._last_decision_t,
            last_override_t_seconds=self._last_override_t,
        )
        try:
            self.on_status_broadcast(status)
        except Exception:  # pragma: no cover - defensive
            log.exception("status broadcast callback raised")


def _default_now() -> float:
    """Default wall-clock source — `time.monotonic()`.

    Module-level so the dataclass default_factory above can reference it
    without capturing instance state.
    """
    import time

    return time.monotonic()
