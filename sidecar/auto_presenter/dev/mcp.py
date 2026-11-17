"""stdio MCP server so Cursor can inject audio and watch the live session.

Talks to the debug sidecar over the existing WebSocket. Does nothing if
the sidecar was started without `--dev-tools` (no lockfile).
"""

from __future__ import annotations

import json
import sys
from typing import Any

from auto_presenter.dev.client import DevSidecarError, request

SERVER_INFO = {"name": "auto-presenter-dev", "version": "0.0.1"}
PROTOCOL_VERSION = "2024-11-05"

_next_id = 0


def _id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


TOOLS = [
    {
        "name": "list_recordings",
        "description": (
            "List captured song recordings the sidecar can inject as "
            "simulated microphone input."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "inject_audio",
        "description": (
            "Feed a recording or WAV into the live presentation as if it "
            "were the microphone. Start presenting in the app first. "
            "The real ASR/matcher/scripture pipeline runs; slides move "
            "in the operator and audience windows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording_id": {
                    "type": "string",
                    "description": "Id from list_recordings",
                },
                "wav_path": {
                    "type": "string",
                    "description": "Absolute path to a WAV, if not using a stored recording",
                },
                "realtime": {
                    "type": "boolean",
                    "description": "Pace to wall clock (default true)",
                    "default": True,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "session_snapshot",
        "description": (
            "Read the live session: current slide, matcher belief, AI mode, "
            "whether audio is coming from the mic or an injected file."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "wait_for_slide",
        "description": (
            "Block until the operator window reports a given item/slide, "
            "or until timeout."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_idx": {"type": "integer"},
                "slide_idx": {"type": "integer"},
                "timeout_seconds": {"type": "number", "default": 60},
            },
            "required": ["item_idx", "slide_idx"],
            "additionalProperties": False,
        },
    },
]


def _ok(id_: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tool_text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


async def _call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    try:
        if name == "list_recordings":
            reply = await request({"kind": "list_recordings", "id": _id()})
        elif name == "inject_audio":
            reply = await request(
                {
                    "kind": "inject_audio",
                    "id": _id(),
                    "recording_id": args.get("recording_id"),
                    "wav_path": args.get("wav_path"),
                    "realtime": args.get("realtime", True),
                },
                timeout=180.0,
            )
        elif name == "session_snapshot":
            reply = await request({"kind": "session_snapshot", "id": _id()})
        elif name == "wait_for_slide":
            timeout = float(args.get("timeout_seconds", 60))
            reply = await request(
                {
                    "kind": "wait_for_slide",
                    "id": _id(),
                    "item_idx": args["item_idx"],
                    "slide_idx": args["slide_idx"],
                    "timeout_seconds": timeout,
                },
                timeout=timeout + 5,
            )
        else:
            return _tool_text(f"unknown tool: {name}")
    except DevSidecarError as exc:
        return _tool_text(str(exc))
    except Exception as exc:  # pragma: no cover - transport
        return _tool_text(f"{type(exc).__name__}: {exc}")
    return _tool_text(json.dumps(reply, indent=2))


async def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    id_ = message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        return _ok(
            id_,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "notifications/initialized" or method == "notifications/cancelled":
        return None
    if method == "ping":
        return _ok(id_, {})
    if method == "tools/list":
        return _ok(id_, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        result = await _call_tool(name, params.get("arguments"))
        return _ok(id_, result)
    if id_ is not None:
        return _err(id_, -32601, f"method not found: {method}")
    return None


async def main() -> None:
    import asyncio

    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        raw = line.decode("utf-8").strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        reply = await _handle(message)
        if reply is None:
            continue
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
