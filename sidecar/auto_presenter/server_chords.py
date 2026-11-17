"""Chord editor request handlers: next-chord suggestions and voice charting.

Editor-time only — the song editor can't be open during a service. Both
calls go to a hosted model and block for seconds, so they run off the event
loop; the logic itself lives in `auto_presenter.chords`.
"""

from __future__ import annotations

import asyncio

from websockets.asyncio.server import ServerConnection

from auto_presenter.chords.suggest import suggest
from auto_presenter.chords.voice import run_voice_chart
from auto_presenter.protocol import (
    SuggestChordsRequest,
    SuggestChordsResponse,
    VoiceChartRequest,
)
from auto_presenter.server_state import _send


async def _handle_suggest_chords(ws: ServerConnection, msg: SuggestChordsRequest) -> None:
    placements, phrase, error = await asyncio.to_thread(suggest, msg.song_key, list(msg.lines))
    await _send(
        ws,
        SuggestChordsResponse(id=msg.id, placements=placements, phrase=phrase, error=error),
    )


async def _handle_voice_chart(ws: ServerConnection, msg: VoiceChartRequest) -> None:
    await _send(ws, await asyncio.to_thread(run_voice_chart, msg))
