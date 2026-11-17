"""Dispatch tests for the recordings-management handlers.

`list`, `delete` and `export` had no coverage at all. The `server.py` split
did not break them — it made the gap visible, since they now sit in a module
whose whole contents can be seen at once.

Every one of them has an early return for `recordings_root is None`, which is
the state the sidecar is genuinely in before `serve()` runs, and each answers
it differently (empty list / `deleted=False` / an error). Those branches are
what these tests are mostly for: they are the paths a client hits when
something upstream has gone wrong, and none of them had ever been executed.
"""

from __future__ import annotations

import json
import wave
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from auto_presenter import server_recordings as mod
from auto_presenter.audio.types import AudioFrame
from auto_presenter.protocol import (
    DeleteRecordingRequest,
    ExportRecordingRequest,
    ListRecordingsRequest,
)
from auto_presenter.recording.golden import save_golden
from auto_presenter.recording.sink import RecordingSink


@pytest.fixture()
def fake_ws():
    ws = MagicMock()
    sent: list[dict] = []

    async def _send(data):
        sent.append(json.loads(data))

    ws.send = MagicMock(side_effect=_send)
    ws.sent = sent
    return ws


@pytest.fixture()
def recordings_root(tmp_path: Path):
    original = mod._state.recordings_root
    mod._state.recordings_root = tmp_path
    yield tmp_path
    mod._state.recordings_root = original


@pytest.fixture()
def no_recordings_root():
    original = mod._state.recordings_root
    mod._state.recordings_root = None
    yield
    mod._state.recordings_root = original


def _seed(root: Path, rec_id: str, *, setlist_id: str = "sl_test") -> Path:
    sink = RecordingSink(rec_id, root / rec_id, setlist_id)
    sink.open()
    sink.write_frame(
        AudioFrame(samples=np.zeros(1600, dtype=np.float32), t_start=0.0, t_end=0.1)
    )
    sink.record_slide_change(item_idx=0, slide_idx=1)
    sink.close()
    return root / rec_id


class TestListRecordings:
    @pytest.mark.asyncio
    async def test_lists_every_finalized_recording(self, fake_ws, recordings_root):
        _seed(recordings_root, "rec_a")
        _seed(recordings_root, "rec_b")
        await mod._handle_list_recordings(fake_ws, ListRecordingsRequest(id=1))
        body = fake_ws.sent[0]
        assert body["kind"] == "list_recordings_response"
        assert {r["id"] for r in body["recordings"]} == {"rec_a", "rec_b"}
        assert all(r["ground_truth_count"] == 1 for r in body["recordings"])

    @pytest.mark.asyncio
    async def test_reports_golden_status_per_recording(self, fake_ws, recordings_root):
        """The Recordings list uses this to show which goldens are approved,
        so it has to survive the round trip, not just exist on disk."""
        _seed(recordings_root, "rec_plain")
        _seed(recordings_root, "rec_signed")
        save_golden(
            recordings_root / "rec_signed", [], recording_id="rec_signed", approved=True
        )
        await mod._handle_list_recordings(fake_ws, ListRecordingsRequest(id=1))
        by_id = {r["id"]: r for r in fake_ws.sent[0]["recordings"]}
        assert by_id["rec_signed"]["golden_approved"] is True
        assert by_id["rec_plain"]["has_golden"] is False

    @pytest.mark.asyncio
    async def test_answers_with_an_empty_list_before_serve_has_run(
        self, fake_ws, no_recordings_root
    ):
        await mod._handle_list_recordings(fake_ws, ListRecordingsRequest(id=1))
        assert fake_ws.sent[0]["recordings"] == []


class TestDeleteRecording:
    @pytest.mark.asyncio
    async def test_removes_the_directory(self, fake_ws, recordings_root):
        rec_dir = _seed(recordings_root, "rec_doomed")
        await mod._handle_delete_recording(
            fake_ws, DeleteRecordingRequest(id=1, recording_id="rec_doomed")
        )
        assert fake_ws.sent[0]["deleted"] is True
        assert not rec_dir.exists()

    @pytest.mark.asyncio
    async def test_an_unknown_id_reports_false_rather_than_erroring(
        self, fake_ws, recordings_root
    ):
        await mod._handle_delete_recording(
            fake_ws, DeleteRecordingRequest(id=1, recording_id="rec_ghost")
        )
        assert fake_ws.sent[0]["deleted"] is False

    @pytest.mark.asyncio
    async def test_deletes_nothing_before_serve_has_run(
        self, fake_ws, no_recordings_root
    ):
        await mod._handle_delete_recording(
            fake_ws, DeleteRecordingRequest(id=1, recording_id="rec_a")
        )
        assert fake_ws.sent[0]["deleted"] is False


class TestExportRecording:
    @pytest.mark.asyncio
    async def test_writes_a_zip_beside_the_recordings(self, fake_ws, recordings_root):
        _seed(recordings_root, "rec_export")
        await mod._handle_export_recording(
            fake_ws, ExportRecordingRequest(id=1, recording_id="rec_export")
        )
        body = fake_ws.sent[0]
        assert body.get("error") is None
        out = Path(body["zip_path"])
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        assert "audio.wav" in names and "recording.json" in names

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_an_error_not_an_empty_zip(
        self, fake_ws, recordings_root
    ):
        await mod._handle_export_recording(
            fake_ws, ExportRecordingRequest(id=1, recording_id="rec_ghost")
        )
        assert "unknown recording" in fake_ws.sent[0]["error"]

    @pytest.mark.asyncio
    async def test_errors_before_serve_has_run(self, fake_ws, no_recordings_root):
        await mod._handle_export_recording(
            fake_ws, ExportRecordingRequest(id=1, recording_id="rec_a")
        )
        assert "recordings root not set" in fake_ws.sent[0]["error"]

    @pytest.mark.asyncio
    async def test_a_failure_while_zipping_is_reported_not_raised(
        self, fake_ws, recordings_root, monkeypatch
    ):
        """The handler runs the zip on a worker thread; an exception there
        must come back as an error field, since the client is awaiting a
        reply and a raise would leave it waiting for the timeout."""
        _seed(recordings_root, "rec_export")

        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(mod, "export_recording_zip", boom)
        await mod._handle_export_recording(
            fake_ws, ExportRecordingRequest(id=1, recording_id="rec_export")
        )
        assert "disk full" in fake_ws.sent[0]["error"]


def test_wave_fixture_sanity(tmp_path: Path):
    """Guards the helper above: if `RecordingSink` stops writing a readable
    wav, the export test would pass on an empty archive."""
    rec_dir = _seed(tmp_path, "rec_sanity")
    with wave.open(str(rec_dir / "audio.wav"), "rb") as w:
        assert w.getnframes() > 0
