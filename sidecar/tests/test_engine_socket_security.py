"""The engine socket is for the app (engine review 2026-11-10, finding 3).

Browsers let any web page open a WebSocket to 127.0.0.1, and a request could
name `../library` as a recording to delete. Web pages are refused by their
Origin, and recording ids must be plain folder names."""

from __future__ import annotations

import inspect

import pytest
import websockets
from pydantic import ValidationError

from auto_presenter import server as server_mod
from auto_presenter.protocol import DeleteRecordingRequest, LoadRecordingRequest
from auto_presenter.recording.sink import delete_recording


@pytest.mark.parametrize("bad", ["..", "../library", "/Users/x", "a/b", ".hidden", "a\\b", ""])
def test_a_recording_id_must_be_a_plain_folder_name(bad):
    with pytest.raises(ValidationError):
        DeleteRecordingRequest(recording_id=bad)
    with pytest.raises(ValidationError):
        LoadRecordingRequest(recording_id=bad)


def test_real_recording_ids_still_pass():
    for ok in ("rec_20260818-213141_334", "rec_20261110-005040_719", "bootstrap.v2"):
        DeleteRecordingRequest(recording_id=ok)


def test_delete_never_leaves_the_recordings_folder(tmp_path):
    root = tmp_path / "recordings"
    (root / "rec_1").mkdir(parents=True)
    keep = tmp_path / "library"
    keep.mkdir()
    assert not delete_recording(root, "../library")
    assert keep.is_dir()
    assert delete_recording(root, "rec_1") and not (root / "rec_1").exists()


def test_serve_restricts_origins():
    assert "origins=APP_ORIGINS" in inspect.getsource(server_mod.serve)


async def _connects(origin: str | None) -> bool:
    async with websockets.serve(
        server_mod._handle_client, "127.0.0.1", 0, origins=server_mod.APP_ORIGINS
    ) as srv:
        port = srv.sockets[0].getsockname()[1]
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}", origin=origin):
                return True
        except websockets.exceptions.InvalidStatus:
            return False


@pytest.mark.parametrize(
    "origin, allowed",
    [
        (None, True),  # the app shell, tools, tests
        ("tauri://localhost", True),  # the app window on macOS
        ("http://tauri.localhost", True),  # the app window on Windows
        ("https://evil.example", False),
        ("http://localhost:8080", False),
    ],
)
async def test_only_the_app_may_connect(origin, allowed, monkeypatch):
    monkeypatch.setattr(server_mod._state, "clients", set())
    assert await _connects(origin) is allowed
