"""Sidecar port lockfile for the debug MCP server.

Lives next to this package at `sidecar/.dev/sidecar.json` so a Cursor
MCP process started from the repo root can find a running debug sidecar
without scraping stdout. Removed when the sidecar exits.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_LOCK_DIR = Path(__file__).resolve().parents[2] / ".dev"
_LOCK_PATH = _LOCK_DIR / "sidecar.json"


def write_lockfile(port: int) -> None:
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"port": port, "pid": os.getpid()})
    tmp = _LOCK_PATH.with_suffix(".tmp")
    tmp.write_text(payload)
    tmp.replace(_LOCK_PATH)


def remove_lockfile() -> None:
    try:
        _LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def read_lockfile() -> dict | None:
    try:
        data = json.loads(_LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "port" not in data:
        return None
    return data
