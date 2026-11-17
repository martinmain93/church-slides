"""Reachability, the installed-models list, and pull-progress parsing."""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO

import pytest

from auto_presenter.decision import ollama_manager


class _Resp(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _json_response(body) -> _Resp:
    return _Resp(json.dumps(body).encode())


class TestAvailability:
    def test_available_when_the_server_answers(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *a, **k: _json_response({"version": "0.1"})
        )
        assert ollama_manager.is_available() is True

    def test_unavailable_when_connection_is_refused(self, monkeypatch):
        def raise_refused(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", raise_refused)
        assert ollama_manager.is_available() is False

    def test_unavailable_on_timeout(self, monkeypatch):
        def raise_timeout(*a, **k):
            raise TimeoutError()

        monkeypatch.setattr("urllib.request.urlopen", raise_timeout)
        assert ollama_manager.is_available() is False


class TestInstalledModels:
    def test_returns_the_tag_names(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **k: _json_response(
                {"models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5:3b-instruct"}]}
            ),
        )
        assert ollama_manager.installed_models() == {"llama3.2:3b", "qwen2.5:3b-instruct"}

    def test_empty_set_not_an_error_when_unreachable(self, monkeypatch):
        def raise_refused(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", raise_refused)
        assert ollama_manager.installed_models() == set()

    def test_empty_set_on_a_malformed_response(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(b"not json"))
        assert ollama_manager.installed_models() == set()


class TestPullProgressParsing:
    def test_a_downloading_line_carries_bytes_and_fraction(self):
        lines = [json.dumps({"status": "pulling manifest", "total": 100, "completed": 25})]
        events = list(ollama_manager._iter_pull_lines("m", lines))
        assert len(events) == 1
        assert events[0].state == "downloading"
        assert events[0].bytes_downloaded == 25
        assert events[0].bytes_total == 100
        assert events[0].fraction == 0.25

    def test_a_lifecycle_line_with_no_byte_counts_is_still_downloading_zero_progress(self):
        lines = [json.dumps({"status": "verifying sha256 digest"})]
        events = list(ollama_manager._iter_pull_lines("m", lines))
        assert events[0].state == "downloading"
        assert events[0].bytes_total == 0
        assert events[0].detail == "verifying sha256 digest"

    def test_blank_lines_are_skipped(self):
        lines = ["", "   ", json.dumps({"status": "ok", "total": 1, "completed": 1})]
        events = list(ollama_manager._iter_pull_lines("m", lines))
        assert len(events) == 1

    def test_unparseable_lines_are_skipped_not_raised(self):
        lines = ["not json at all", json.dumps({"status": "ok", "total": 1, "completed": 1})]
        events = list(ollama_manager._iter_pull_lines("m", lines))
        assert len(events) == 1

    def test_an_error_line_yields_a_failed_event_and_stops(self):
        lines = [
            json.dumps({"error": "model not found"}),
            json.dumps({"status": "should not be reached"}),
        ]
        events = list(ollama_manager._iter_pull_lines("m", lines))
        assert len(events) == 1
        assert events[0].state == "failed"
        assert "model not found" in events[0].detail


class TestPull:
    """`pull()` itself, not just `_iter_pull_lines` — this is where a real
    bug lived: an in-stream error was correctly turned into a "failed"
    event by `_iter_pull_lines`, but `pull()` unconditionally emitted a
    "completed" event right after the loop regardless of what happened
    inside it, so a failed download was reported to the UI as successful.
    """

    def test_an_in_stream_error_raises_and_never_reports_completed(self, monkeypatch):
        lines = [json.dumps({"error": "model not found"})]
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *a, **k: _Resp("\n".join(lines).encode())
        )
        events = []
        with pytest.raises(RuntimeError, match="model not found"):
            ollama_manager.pull("m", progress_callback=events.append)
        assert [e.state for e in events] == ["starting", "failed"]

    def test_a_clean_stream_reports_completed_exactly_once(self, monkeypatch):
        lines = [json.dumps({"status": "success", "total": 1, "completed": 1})]
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *a, **k: _Resp("\n".join(lines).encode())
        )
        events = []
        ollama_manager.pull("m", progress_callback=events.append)
        assert [e.state for e in events] == ["starting", "downloading", "completed"]

    def test_a_connection_failure_raises_and_never_reports_completed(self, monkeypatch):
        def raise_refused(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", raise_refused)
        events = []
        with pytest.raises(urllib.error.URLError):
            ollama_manager.pull("m", progress_callback=events.append)
        assert [e.state for e in events] == ["starting", "failed"]


class TestPullProgressEvent:
    def test_fraction_is_zero_when_total_is_unknown(self):
        event = ollama_manager.PullProgressEvent("m", 0, 0, "starting")
        assert event.fraction == 0.0

    def test_fraction_is_clamped_to_one(self):
        event = ollama_manager.PullProgressEvent("m", 150, 100, "downloading")
        assert event.fraction == 1.0
