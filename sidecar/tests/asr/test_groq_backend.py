"""`GroqWhisperBackend` against a mocked endpoint — no network, no key."""

from __future__ import annotations

import json
import urllib.error
from contextlib import contextmanager
from email.parser import BytesParser
from io import BytesIO

import numpy as np

from auto_presenter.asr.groq_backend import GroqWhisperBackend
from auto_presenter.audio.types import AudioFrame


def _frame(seconds: float, t_start: float = 0.0) -> AudioFrame:
    n = int(seconds * 16_000)
    return AudioFrame(
        samples=np.zeros(n, dtype=np.float32), t_start=t_start, t_end=t_start + seconds
    )


def _verbose_json(words: list[tuple[str, float, float]]) -> dict:
    return {
        "text": " ".join(w for w, _, _ in words),
        "words": [{"word": w, "start": s, "end": e} for w, s, e in words],
    }


@contextmanager
def _mock_urlopen(monkeypatch, response_body: dict | None = None, raises: Exception | None = None):
    captured = {}

    class _Resp(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, **_kwargs):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["timeout"] = timeout
        # Parse the multipart body the same way an HTTP server would, so
        # the test asserts on field values rather than raw bytes.
        content_type = req.get_header("Content-type")
        msg = BytesParser().parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
            + req.data
        )
        fields = {}
        file_bytes = None
        for part in msg.get_payload():
            name = part.get_param("name", header="Content-Disposition")
            if part.get_filename():
                file_bytes = part.get_payload(decode=True)
            else:
                fields[name] = part.get_payload(decode=True).decode()
        captured["fields"] = fields
        captured["file_bytes"] = file_bytes
        if raises is not None:
            raise raises
        return _Resp(json.dumps(response_body).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    yield captured


class TestRequestShape:
    def test_sends_a_multipart_request_with_the_key_and_model(self, monkeypatch):
        with _mock_urlopen(monkeypatch, _verbose_json([])) as captured:
            backend = GroqWhisperBackend(api_key="gk_test", model="whisper-large-v3-turbo")
            list(backend.transcribe([_frame(1.0)]))
        assert captured["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
        assert captured["headers"]["Authorization"] == "Bearer gk_test"
        assert "python-urllib" not in captured["headers"]["User-agent"].lower()
        assert captured["fields"]["model"] == "whisper-large-v3-turbo"
        assert captured["fields"]["response_format"] == "verbose_json"
        assert captured["fields"]["timestamp_granularities[]"] == "word"
        assert captured["file_bytes"][:4] == b"RIFF"  # a real WAV, not raw PCM

    def test_an_empty_frame_list_makes_no_request(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *a, **k: called.append(1) or None
        )
        backend = GroqWhisperBackend(api_key="gk_test")
        assert list(backend.transcribe([])) == []
        assert called == []


class TestResponseParsing:
    def test_words_resolve_with_timestamps_offset_by_the_frame_origin(self, monkeypatch):
        with _mock_urlopen(
            monkeypatch, _verbose_json([("go", 0.1, 0.3), ("home", 0.3, 0.6)])
        ):
            backend = GroqWhisperBackend(api_key="gk_test")
            events = list(backend.transcribe([_frame(1.0, t_start=10.0)]))
        assert len(events) == 1
        assert [w.text for w in events[0].words] == ["go", "home"]
        assert events[0].words[0].t_start == 10.1
        assert events[0].words[0].t_end == 10.3
        assert events[0].is_final is True

    def test_no_words_in_the_response_resolves_to_no_events(self, monkeypatch):
        with _mock_urlopen(monkeypatch, _verbose_json([])):
            backend = GroqWhisperBackend(api_key="gk_test")
            assert list(backend.transcribe([_frame(1.0)])) == []


class TestNetworkFailure:
    def test_connection_failure_yields_nothing_not_a_raise(self, monkeypatch):
        with _mock_urlopen(monkeypatch, raises=urllib.error.URLError("connection refused")):
            backend = GroqWhisperBackend(api_key="gk_test")
            assert list(backend.transcribe([_frame(1.0)])) == []

    def test_timeout_yields_nothing_not_a_raise(self, monkeypatch):
        with _mock_urlopen(monkeypatch, raises=TimeoutError()):
            backend = GroqWhisperBackend(api_key="gk_test")
            assert list(backend.transcribe([_frame(1.0)])) == []


def _too_many_requests(retry_after: str | None) -> urllib.error.HTTPError:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    return urllib.error.HTTPError(
        "https://api.groq.com", 429, "Too Many Requests", headers, None
    )


class TestRateLimit:
    """A free key allows 20 requests a minute; the wrapper asked for 60, and
    a live service lost most of its transcript to 429s (2026-10-13)."""

    def test_the_wrapper_is_asked_for_no_more_than_20_passes_a_minute(self):
        assert GroqWhisperBackend.min_step_seconds >= 60 / 20

    def test_a_429_pauses_calls_for_retry_after(self, monkeypatch):
        clock = [100.0]
        monkeypatch.setattr("auto_presenter.asr.groq_backend.time.monotonic", lambda: clock[0])
        with _mock_urlopen(monkeypatch, raises=_too_many_requests("7")) as captured:
            backend = GroqWhisperBackend(api_key="gk_test")
            assert list(backend.transcribe([_frame(1.0)])) == []
            captured.clear()
            clock[0] += 6.0
            assert list(backend.transcribe([_frame(1.0)])) == []
            assert "url" not in captured  # still waiting: no request sent
            clock[0] += 1.5
            list(backend.transcribe([_frame(1.0)]))
            assert "url" in captured

    def test_a_missing_or_huge_retry_after_stays_within_reason(self, monkeypatch):
        from auto_presenter.asr.groq_backend import _retry_after

        assert _retry_after(_too_many_requests(None)) == GroqWhisperBackend.min_step_seconds
        assert _retry_after(_too_many_requests("86400")) == 60.0
        assert _retry_after(_too_many_requests("0")) == 1.0
