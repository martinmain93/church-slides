"""One slow request must not hold up the rest of the UI.

The UI sends every request over one shared socket. When the server handled a
socket's messages strictly one after another, a first service start
downloading the speech model held that socket for minutes: the device list
behind it timed out (Settings showed no microphones) and so did the
model-status check (Settings stuck on "Checking…"). Session messages still
have to apply in the order they were sent.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from auto_presenter import server as server_mod
from auto_presenter.audio import devices as devices_mod


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(server_mod._state, "clients", set())
    async with websockets.serve(server_mod._handle_client, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            yield ws


async def _reply(ws, request_id: int, timeout: float = 2.0) -> dict:
    async def wait() -> dict:
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == request_id:
                return msg

    return await asyncio.wait_for(wait(), timeout)


START = {"kind": "start_audio_session", "backend": "mock", "model_size": "tiny.en"}


@pytest.mark.asyncio
async def test_the_device_list_answers_while_a_session_start_is_downloading(
    client, monkeypatch
):
    release = asyncio.Event()

    async def slow_start(ws, msg):
        await release.wait()

    monkeypatch.setattr(server_mod, "_handle_start_audio", slow_start)
    monkeypatch.setattr(devices_mod, "list_input_devices", lambda **_: [])

    await client.send(json.dumps({**START, "id": 1}))
    await client.send(json.dumps({"kind": "list_audio_devices", "id": 2}))
    try:
        reply = await _reply(client, 2)
    finally:
        release.set()
    assert reply["kind"] == "list_audio_devices_response"


@pytest.mark.asyncio
async def test_session_messages_still_apply_in_send_order(client, monkeypatch):
    order: list[str] = []
    release = asyncio.Event()

    async def slow_start(ws, msg):
        await release.wait()
        order.append("start")

    async def stop(ws, msg):
        order.append("stop")
        await server_mod._send(ws, server_mod.PongMessage(id=msg.id))

    monkeypatch.setattr(server_mod, "_handle_start_audio", slow_start)
    monkeypatch.setattr(server_mod, "_handle_stop_audio", stop)

    await client.send(json.dumps({**START, "id": 1}))
    await client.send(json.dumps({"kind": "stop_audio_session", "id": 2}))
    await asyncio.sleep(0.05)
    assert order == []
    release.set()
    await _reply(client, 2)
    assert order == ["start", "stop"]
