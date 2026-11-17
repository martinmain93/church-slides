"""Thin WebSocket client the MCP server uses to reach the sidecar."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets

from auto_presenter.dev.lockfile import read_lockfile


class DevSidecarError(RuntimeError):
    pass


async def request(payload: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
    info = read_lockfile()
    if not info:
        raise DevSidecarError(
            "debug sidecar is not running — start the app with `pnpm tauri:dev`"
        )
    port = int(info["port"])
    req_id = payload.get("id")
    uri = f"ws://127.0.0.1:{port}"
    async with websockets.connect(uri, open_timeout=5, close_timeout=2) as ws:
        await ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if req_id is not None and msg.get("id") != req_id:
                continue
            return msg
