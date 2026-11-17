"""The optional low-latency second listener: wiring, broadcasting, and the
toggle that lets it feed the matcher.

Split out of `server.py` as one of its more self-contained groups — see
that module's docstring, and `server_state.py`'s, for why the split is
shaped the way it is. `_frame_fanout` is the one piece still called from
audio-session setup in `server.py` (it also writes to the recording sink),
but everything fast-channel-specific about it — feeding the model, tracking
latency, broadcasting, optionally handing words to the matcher — lives here.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import time
from pathlib import Path

from websockets.asyncio.server import ServerConnection

from auto_presenter.asr.fast_channel import FastChannel, default_model_dir
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.protocol import (
    FastChannelMessage,
    FastWordPayload,
    PongMessage,
    SetFastChannelRequest,
)
from auto_presenter.server_music import observe_frame as observe_music
from auto_presenter.server_state import _broadcast, _send, _state
from auto_presenter.tracker.forward import ForwardConfig

log = logging.getLogger(__name__)

#: When the panel was last refreshed, so the figures stay live between words
#: without flooding the socket — see `_frame_fanout`.
FAST_STATUS_INTERVAL = 0.25

#: Segment ids for fast-channel events, offset so they cannot collide with
#: Whisper's. Only used to keep the two streams distinguishable in logs.
_FAST_SEGMENT_BASE = 1_000_000


#: Where the optional fast-channel model lives. Absent by default — the feature
#: is a diagnostic and the model is a large download.
def _fast_channel_dir() -> Path:
    """The model directory. Env var wins; otherwise where setup put it."""
    override = os.environ.get("AUTO_PRESENTER_FAST_MODEL")
    return Path(override) if override else default_model_dir()


def _frame_fanout(recording_sink):
    """Send every captured frame to the recorder and to the fast channel.

    Every optional listener hangs off this one fan-out. It used to return
    early when the fast channel was unavailable, which was harmless while the
    fast channel was the only consumer and would have silently starved the
    music analyser of frames on every machine without the fast-channel model.

    The fast channel is a **diagnostic**: it never reaches the matcher unless
    the operator turns that on. On the approved goldens it transcribes sung
    audio far worse than Whisper (word error 0.85 against 0.22), so letting
    it decide anything would make the app worse. Inference itself is also
    off until the debug tab asks — session start only probes whether the
    model is installed, so a live service does not spend ~2% of a core on
    a panel nobody is watching.
    """
    write = recording_sink.write_frame if recording_sink else None

    def observe(frame):
        if write is not None:
            write(frame)
        # Live chord/beat analysis, when the debug panel has asked for it. It
        # is checked per frame rather than captured at setup so the toggles
        # take effect mid-session without restarting the audio pipeline.
        observe_music(frame)
        fast = _state.fast_channel
        if (
            fast is None
            or not fast.available
            or not (_state.fast_listen or _state.fast_feeds_matcher)
        ):
            return
        try:
            words = fast.feed(frame.samples)
        except Exception:
            log.exception("fast channel raised; disabling it for this session")
            _state.fast_channel = None
            return
        _state.fast_latencies.extend(w.latency for w in words)
        del _state.fast_latencies[:-50]
        loop = _state.loop
        if loop is None:
            return
        # Refresh on a steady cadence, not only when a word lands.
        #
        # Broadcasting only on new words tied the panel's update rate to the
        # *speech* rate — about 0.44 words a second — so the latency and CPU
        # figures froze for seconds at a time and the panel looked stalled while
        # the channel was working perfectly. Words still go out the moment they
        # arrive; this just keeps the numbers alive between them.
        now = time.monotonic()
        if not words and now - _state.fast_last_broadcast < FAST_STATUS_INTERVAL:
            return
        _state.fast_last_broadcast = now
        asyncio.run_coroutine_threadsafe(_broadcast_fast_channel(words), loop)
        if not words:
            return
        if _state.fast_feeds_matcher:
            # Through the same handler Whisper's words take, so a fast word is
            # ordinary evidence — it commits, broadcasts belief and records to
            # the golden exactly as a slow one does. Segment ids are offset so
            # the two streams cannot collide.
            event = TranscriptEvent(
                words=[
                    Word(text=w.text, t_start=w.t_start, t_end=w.t_start)
                    for w in words
                ],
                is_final=True,
                segment_id=_FAST_SEGMENT_BASE + len(_state.fast_latencies),
            )
            handler = _state.transcript_handler
            if handler is not None:
                asyncio.run_coroutine_threadsafe(handler(event), loop)

    return observe


async def _broadcast_fast_channel(words) -> None:
    fast = _state.fast_channel
    lat = sorted(_state.fast_latencies)
    await _broadcast(
        FastChannelMessage(
            words=[
                FastWordPayload(text=w.text, t_start=w.t_start, latency=w.latency)
                for w in words
            ],
            median_latency=lat[len(lat) // 2] if lat else 0.0,
            realtime_factor=fast.realtime_factor if fast else 0.0,
            available=fast is not None and fast.available,
            feeds_matcher=_state.fast_feeds_matcher,
            status=fast.status if fast else "model_missing",
        )
    )


async def _ensure_fast_channel_started() -> None:
    """Load sherpa-onnx if the debug tab (or matcher feed) has asked for it."""
    if _state.fast_channel is None:
        _state.fast_channel = FastChannel(model_dir=_fast_channel_dir())
    channel = _state.fast_channel
    if channel.available:
        return
    await asyncio.to_thread(channel.start)


async def _handle_set_fast_channel(
    ws: ServerConnection, msg: SetFastChannelRequest
) -> None:
    """Start/stop listening, and optionally turn words into matcher evidence.

    Also re-tunes how far the posterior travels per word. The transition model's
    advance rate is calibrated to a word *density*, and adding a second stream
    roughly doubles it — leave it alone and the posterior runs ahead of the
    band. Measured: with both streams, advance 0.33 -> 0.50 was worth 0.3 s of
    timing.
    """
    _state.fast_feeds_matcher = msg.feed_matcher
    if msg.feed_matcher or msg.listen is True:
        _state.fast_listen = True
        await _ensure_fast_channel_started()
    elif msg.listen is False:
        _state.fast_listen = False
    matcher = _state.matcher
    inner = getattr(getattr(matcher, "filter", None), "filter", None)
    config = getattr(inner, "config", None)
    if config is not None and hasattr(config, "advance"):
        # `ForwardConfig` is frozen, so swap the whole object rather than
        # poking at fields.
        shipped = ForwardConfig()
        inner.config = (
            dataclasses.replace(config, advance=0.50, stay=0.38)
            if msg.feed_matcher
            else dataclasses.replace(
                config, advance=shipped.advance, stay=shipped.stay
            )
        )
    log.info(
        "fast channel listen=%s feeds_matcher=%s",
        _state.fast_listen,
        _state.fast_feeds_matcher,
    )
    await _send(ws, PongMessage(id=msg.id))
    await _broadcast_fast_channel([])
