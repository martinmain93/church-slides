"""AI-assisted mode's server side: feed the assist layer, publish what it says.

The interpreter (`decision.assist.AssistInterpreter`) is pure; this module is
its plumbing. It sees three things, all of which the server already has:

  - the engine's moves, from `Director.on_assist_move` (assist mode routes
    them here instead of to the screen);
  - what the screen shows, from the UI's slide-change notifications — which
    in assist mode are NOT forwarded to the engine as overrides, so the
    engine runs free and can disagree with an operator's mistake;
  - the engine's ranked guesses and lost flag, when it has them.

One clock for everything the interpreter sees: seconds since the session
started, by the wall clock, stamped when each thing *arrives* — the same as
the yardstick's tapes. Its waits are "how long the engine has disagreed", so
they're measured from when the engine said so, and a timer checks them four
times a second. (They used to be checked only when new words came in, on
the words' own clock; the engine's next move came in the same batch and
restarted the wait, so with short slides it could never run out.)

Everything here runs on the event loop thread: engine moves can arrive from
other threads, so `engine_move_threadsafe` hops onto the loop first.

See docs/done/AI_ASSISTED_MODE.md.
"""

from __future__ import annotations

import asyncio
import logging
import time

from auto_presenter.decision.assist import AssistConfig, AssistEvent, AssistInterpreter
from auto_presenter.matcher.types import MatcherDecision
from auto_presenter.protocol import (
    AssistClearMessage,
    AssistDismissRequest,
    AssistSuggestionMessage,
    PongMessage,
    SlideRef,
)
from auto_presenter.server_state import (
    _broadcast,
    _indexed_setlist,
    _send,
    _state,
)

log = logging.getLogger(__name__)

_interp: AssistInterpreter | None = None
#: Latest time on the evidence (audio) clock — what the Director's own
#: timestamps are on, for a dismissal's re-anchor.
_evidence_now: float = 0.0
#: `time.monotonic()` when the session started: `_clock()`'s zero.
_t0: float = time.monotonic()
_ticker: asyncio.Task | None = None
TICK_SECONDS = 0.25
#: In the Assist-only tier (a slower computer), engine moves are ignored while
#: the pipeline runs this far behind real time: on top of speech's own ~3 s
#: delivery lag and a pass every 2 s, a suggestion would rest on evidence
#: more than ~6 s old, and a late one teaches volunteers to ignore the AI
#: (Launch Plan field note 6). Saying nothing is better than saying it late.
ASSIST_TIER_MAX_LAG_S = 2.0
#: What the screen shows, kept across a rebuild of the interpreter.
_shown: tuple[int, int] | None = None
#: Broadcasts in flight. The loop holds tasks weakly, so an unreferenced one
#: can be collected before it runs.
_tasks: set[asyncio.Future] = set()


def _clock() -> float:
    return time.monotonic() - _t0


def _send_later(message) -> None:
    task = asyncio.ensure_future(_broadcast(message))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _interpreter() -> AssistInterpreter | None:
    """The interpreter for the current setlist, rebuilt when it changes (a
    reindex swaps the setlist object, and slide numbers with it)."""
    global _interp, _shown
    director, matcher = _state.director, _state.matcher
    if director is None or matcher is None or director.mode != "assist":
        return None
    setlist = _indexed_setlist(matcher)
    if setlist is None:
        return None
    if _interp is None or _interp.setlist is not setlist:
        if _interp is not None:
            log.info("assist: the setlist changed — starting over")
        _interp = AssistInterpreter(setlist=setlist, config=_config())
        _shown = _shown or _state.last_slide or _session_start()
        if _shown is not None:
            _interp.displayed(_clock(), *_shown)
        _start_ticker()
    return _interp


def _config() -> AssistConfig:
    """The eager defaults; on a slower computer (the Assist-only tier) without
    the "next slide is due" pulse."""
    from auto_presenter.server_follower import assist_tier

    return AssistConfig(next_tier=not assist_tier())


def _stale() -> bool:
    """A slower computer's engine is running too far behind to suggest."""
    from auto_presenter.server_follower import assist_tier

    return _state.pipeline_lag_s > ASSIST_TIER_MAX_LAG_S and assist_tier()


def _start_ticker() -> None:
    global _ticker
    if _ticker is not None and not _ticker.done():
        return
    try:
        _ticker = asyncio.get_running_loop().create_task(_tick_loop())
    except RuntimeError:
        _ticker = None  # no loop (a unit test calling in directly)


async def _tick_loop() -> None:
    while True:
        await asyncio.sleep(TICK_SECONDS)
        interp = _interpreter()
        if interp is None:
            return
        _publish(interp.tick(_clock()))


def _session_start() -> tuple[int, int] | None:
    """Where the screen was when the session started — what it shows until
    the UI says otherwise (it reports moves, not the starting slide)."""
    start = _state.last_start_audio
    if start is None or start.start_item_idx is None:
        return None
    return (start.start_item_idx, start.start_slide_idx)


def engine_move_threadsafe(loop: asyncio.AbstractEventLoop, decision: MatcherDecision) -> None:
    """`Director.on_assist_move`: may be called off the loop thread."""
    loop.call_soon_threadsafe(_engine_move, decision)


def _engine_move(decision: MatcherDecision) -> None:
    global _evidence_now
    _evidence_now = max(_evidence_now, decision.t_seconds)
    interp = _interpreter()
    if interp is None:
        return
    if _stale():
        log.info("assist: engine move to item %d slide %d ignored, %.1f s behind",
                 decision.item_idx, decision.slide_idx, _state.pipeline_lag_s)
        return
    # What the engine would have shown, against what is up — the trail a
    # "why didn't it say so sooner?" needs (suggestions alone don't show it).
    log.info("assist: engine on item %d slide %d at %.1fs (screen %s)",
             decision.item_idx, decision.slide_idx, _clock(), _shown)
    _publish(interp.engine_move(
        _clock(), decision.item_idx, decision.slide_idx, decision.confidence
    ))


def displayed(item_idx: int, slide_idx: int) -> None:
    """The screen now shows this slide (any source). Loop thread."""
    global _shown
    _shown = (item_idx, slide_idx)
    interp = _interpreter()
    if interp is not None:
        log.info("assist: screen on item %d slide %d at %.1fs", item_idx, slide_idx, _clock())
        _publish(interp.displayed(_clock(), item_idx, slide_idx))


async def observe_assist(matcher) -> None:
    """Per transcript event: the engine's ranked guesses, and a tick so dwells
    can run out between moves. Optional inputs — read defensively, so an
    engine without them just gets ticks."""
    global _evidence_now
    interp = _interpreter()
    if interp is None:
        return
    belief = getattr(matcher, "belief", None)
    t = getattr(belief, "t_seconds", None)
    if t is not None:
        _evidence_now = max(_evidence_now, float(t))
    top = getattr(belief, "top_3", None)
    if top:
        lost = getattr(getattr(matcher, "filter", None), "is_lost", False)
        conf = getattr(matcher, "confidence_state", None)
        if conf is not None:
            lost = conf.is_lost
        ranked = [(int(item), int(slide)) for item, slide, _m in top]
        _publish(interp.candidates(_clock(), ranked, lost=bool(lost)))


async def handle_dismiss(ws, msg: AssistDismissRequest) -> None:
    interp = _interpreter()
    if interp is not None:
        _publish(interp.dismiss(_clock(), msg.suggestion_id))
    await _send(ws, PongMessage(id=msg.id))


def mode_changed(mode: str) -> None:
    """Leaving assist: take down whatever is showing and forget it."""
    global _interp
    if mode == "assist" or _interp is None:
        return
    if _interp.current is not None:
        _send_later(
            AssistClearMessage(suggestion_id=_interp.current.id, reason="mode_changed")
        )
    _interp = None


def reset() -> None:
    """A new session: nothing carries over."""
    global _interp, _evidence_now, _shown, _t0, _ticker
    if _ticker is not None:
        _ticker.cancel()
    _interp, _evidence_now, _shown, _t0, _ticker = None, 0.0, None, time.monotonic(), None


def _publish(events: list[AssistEvent]) -> None:
    for ev in events:
        if ev.kind in ("raise", "update") and ev.suggestion is not None:
            s = ev.suggestion
            if ev.kind == "raise":
                log.info("assist: %s suggestion %d -> item %d slide %d",
                         s.tier, s.id, s.item_idx, s.slide_idx)
            _send_later(AssistSuggestionMessage(
                suggestion_id=s.id,
                tier=s.tier,
                item_idx=s.item_idx,
                slide_idx=s.slide_idx,
                alternatives=[SlideRef(item_idx=i, slide_idx=j) for i, j in s.alternatives],
            ))
        elif ev.kind == "clear" and ev.cleared_id is not None:
            _send_later(AssistClearMessage(suggestion_id=ev.cleared_id, reason=ev.reason))
        elif ev.kind == "reanchor" and ev.item_idx is not None and ev.slide_idx is not None:
            # A dismissal: the operator says the screen is right, so the
            # engine re-aims there (the same path a spoken request takes).
            director = _state.director
            if director is not None:
                director.on_override(
                    item_idx=ev.item_idx, slide_idx=ev.slide_idx, t_seconds=_evidence_now
                )
