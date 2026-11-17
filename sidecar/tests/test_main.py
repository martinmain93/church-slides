"""End-to-end test: spawn the sidecar as a subprocess, ping it over WebSocket."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import websockets

pytestmark = pytest.mark.asyncio


async def test_sidecar_ping_pong():
    repo_root = Path(__file__).resolve().parents[1]

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "auto_presenter",
        "--port",
        "0",
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        port = await _read_port(proc)
        async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
            await ws.send(json.dumps({"kind": "ping", "id": 42}))
            reply = json.loads(await ws.recv())

        assert reply["kind"] == "pong"
        assert reply["id"] == 42
        assert isinstance(reply["sidecar_pid"], int)
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()
            await proc.wait()


async def _read_port(proc: asyncio.subprocess.Process) -> int:
    assert proc.stdout is not None
    for _ in range(50):
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        if not line:
            raise RuntimeError("sidecar exited before announcing port")
        try:
            payload = json.loads(line.decode())
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "listening":
            return int(payload["port"])
    raise RuntimeError("did not see listening line in sidecar output")


def test_numpy_blas_is_single_threaded_by_default():
    """Set by the package before numpy loads (see auto_presenter/__init__)."""
    import subprocess
    import sys

    code = (
        "import os; os.environ.pop('OPENBLAS_NUM_THREADS', None); "
        "import auto_presenter, numpy; "
        "print(os.environ['OPENBLAS_NUM_THREADS'])"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k != "OPENBLAS_NUM_THREADS"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.stdout.strip() == "1"
