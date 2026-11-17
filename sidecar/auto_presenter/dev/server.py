"""Dev-tools WebSocket handlers.

`try_dispatch` is the only function production `server._dispatch` calls,
and only after the production protocol rejects the payload and only when
`_state.dev_tools` is True.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection

from auto_presenter.dev import inject as inject_mod
from auto_presenter.dev.protocol import (
    InjectAudioRequest,
    InjectAudioResponse,
    SessionSnapshotRequest,
    SessionSnapshotResponse,
    WaitForSlideRequest,
    WaitForSlideResponse,
    adapter,
)
from auto_presenter.dev.snapshot import build_snapshot
from auto_presenter.server_state import _send, _send_error, _state

log = logging.getLogger(__name__)


async def try_dispatch(ws: ServerConnection, payload: Any) -> bool:
    """Handle a payload if it is a known dev-tools message.

    Returns False when `kind` is not a dev verb, so the production
    dispatcher can keep its original validation error.
    """
    try:
        msg = adapter.validate_python(payload)
    except ValidationError:
        kind = payload.get("kind") if isinstance(payload, dict) else None
        if kind in {"inject_audio", "session_snapshot", "wait_for_slide"}:
            err = "invalid dev-tools message"
            try:
                # Surface the pydantic detail when we know the kind is ours.
                adapter.validate_python(payload)
            except ValidationError as exc:
                err = f"invalid message: {exc.errors()[0]['msg']}"
            await _send_error(ws, payload.get("id") if isinstance(payload, dict) else None, err)
            return True
        return False

    if isinstance(msg, InjectAudioRequest):
        await _handle_inject(ws, msg)
        return True
    if isinstance(msg, SessionSnapshotRequest):
        await _send(ws, SessionSnapshotResponse(id=msg.id, snapshot=build_snapshot()))
        return True
    if isinstance(msg, WaitForSlideRequest):
        await _handle_wait(ws, msg)
        return True
    return False


async def _handle_inject(ws: ServerConnection, msg: InjectAudioRequest) -> None:
    last = _state.last_start_audio
    if msg.session is not None:
        from auto_presenter.protocol import StartAudioSessionRequest

        try:
            last = StartAudioSessionRequest.model_validate(msg.session)
        except ValidationError as exc:
            await _send(
                ws,
                InjectAudioResponse(
                    id=msg.id, wav_path="", error=f"invalid session: {exc.errors()[0]['msg']}"
                ),
            )
            return
    if last is None:
        await _send(
            ws,
            InjectAudioResponse(
                id=msg.id,
                wav_path="",
                error="no audio session has been started — start presenting first",
            ),
        )
        return
    try:
        capture = inject_mod.make_capture(
            recording_id=msg.recording_id,
            wav_path=msg.wav_path,
            realtime=msg.realtime,
        )
    except (ValueError, FileNotFoundError) as exc:
        await _send(
            ws,
            InjectAudioResponse(id=msg.id, wav_path="", error=str(exc)),
        )
        return

    # The screen hasn't moved: only the microphone has been swapped. Start the
    # engine where the screen is, and remember it, rather than at the slide
    # the service began on (assist mode compares against what's showing).
    shown = _state.last_slide
    update: dict = {"id": msg.id, "record": False}
    if shown is not None:
        update.update(start_item_idx=shown[0], start_slide_idx=shown[1])
    restart_msg = last.model_copy(update=update)
    from auto_presenter.server import restart_with_capture

    try:
        ok = await restart_with_capture(ws, restart_msg, capture, ack=False)
        if ok and shown is not None and _state.last_slide is None:
            _state.last_slide = shown
    except Exception as exc:
        log.exception("inject_audio restart failed")
        inject_mod.clear()
        await _send(
            ws,
            InjectAudioResponse(
                id=msg.id,
                wav_path=str(capture.path),
                recording_id=msg.recording_id,
                error=f"failed to start file capture: {exc}",
            ),
        )
        return
    if not ok:
        inject_mod.clear()
        return
    await _send(
        ws,
        InjectAudioResponse(
            id=msg.id,
            wav_path=str(capture.path),
            recording_id=msg.recording_id,
        ),
    )


async def _handle_wait(ws: ServerConnection, msg: WaitForSlideRequest) -> None:
    deadline = time.monotonic() + max(0.0, msg.timeout_seconds)
    started = time.monotonic()
    while True:
        shown = _state.last_slide
        if shown == (msg.item_idx, msg.slide_idx):
            await _send(
                ws,
                WaitForSlideResponse(
                    id=msg.id,
                    reached=True,
                    item_idx=shown[0],
                    slide_idx=shown[1],
                    elapsed_seconds=time.monotonic() - started,
                ),
            )
            return
        if time.monotonic() >= deadline:
            await _send(
                ws,
                WaitForSlideResponse(
                    id=msg.id,
                    reached=False,
                    item_idx=None if shown is None else shown[0],
                    slide_idx=None if shown is None else shown[1],
                    elapsed_seconds=time.monotonic() - started,
                    error="timeout",
                ),
            )
            return
        await asyncio.sleep(0.05)

