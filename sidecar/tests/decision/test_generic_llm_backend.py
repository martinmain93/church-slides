"""`GenericLLMBackend` against a mocked OpenAI-compatible endpoint.

Never tested with mocks before this — `OllamaBackend` was only ever
measured against a real, running ollama server, which is why the response
parsing changed shape without a single test file needing to change (see
`GenericLLMBackend`'s docstring). Adding real unit coverage now that this
is genuinely reused across local and hosted providers.
"""

from __future__ import annotations

import json
import urllib.error
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO

import pytest

from auto_presenter.decision.instructions import (
    GenericLLMBackend,
    InstructionContext,
    InstructionItem,
    OllamaBackend,
    ToolCall,
)


def _ctx():
    return InstructionContext(
        items=(InstructionItem(0, "Reckless Love", ("verse:1", "chorus:1")),),
        current_item_idx=0,
    )


def _openai_response(tool_name: str | None, arguments: dict | None = None):
    message = {"role": "assistant", "content": ""}
    if tool_name is not None:
        message["tool_calls"] = [
            {"id": "call_1", "type": "function",
             "function": {"name": tool_name, "arguments": json.dumps(arguments or {})}}
        ]
    return {"choices": [{"index": 0, "message": message}]}


@contextmanager
def _mock_urlopen(monkeypatch, response_body: dict | None = None, raises: Exception | None = None):
    captured = {}

    def fake_urlopen(req, timeout=None, **_kwargs):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data)
        if raises is not None:
            raise raises
        return BytesIO(json.dumps(response_body).encode())

    # `BytesIO` doesn't support the `with ... as resp` protocol on its own
    # in the way `urlopen`'s result does; wrap it minimally.
    class _Resp(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def wrapped(req, timeout=None, **_kwargs):
        result = fake_urlopen(req, timeout=timeout)
        return _Resp(result.getvalue())

    monkeypatch.setattr("urllib.request.urlopen", wrapped)
    yield captured


class TestRequestShape:
    def test_sends_the_openai_chat_completions_shape(self, monkeypatch):
        with _mock_urlopen(monkeypatch, _openai_response(None)) as captured:
            backend = GenericLLMBackend(base_url="https://api.example.com/v1", model="test-model")
            backend.decide("hello", _ctx())
        assert captured["url"] == "https://api.example.com/v1/chat/completions"
        assert captured["body"]["model"] == "test-model"
        assert captured["body"]["temperature"] == 0.0
        assert [t["function"]["name"] for t in captured["body"]["tools"]] == [
            "go_to_slide", "search_library", "wait_for_more", "no_action",
        ]
        assert "Authorization" not in captured["headers"]

    def test_sends_a_real_user_agent(self, monkeypatch):
        """Real bug, found testing Groq with a live key: `urllib`'s default
        User-Agent gets a 403 from Cloudflare ("banned by browser
        signature") before the request reaches the provider's API at all.
        Any hosted provider behind similar bot protection hits the same
        wall without this."""
        with _mock_urlopen(monkeypatch, _openai_response(None)) as captured:
            backend = GenericLLMBackend(base_url="https://api.example.com/v1", model="m")
            backend.decide("hello", _ctx())
        # `urllib.request.Request` normalizes header keys to `Title-case`
        # per hyphen-separated word (first letter only) — "User-Agent" as
        # set becomes "User-agent" as stored.
        assert captured["headers"]["User-agent"] != ""
        assert "python-urllib" not in captured["headers"]["User-agent"].lower()

    def test_sends_a_bearer_token_when_an_api_key_is_set(self, monkeypatch):
        with _mock_urlopen(monkeypatch, _openai_response(None)) as captured:
            backend = GenericLLMBackend(
                base_url="https://api.example.com/v1", model="test-model", api_key="sk-123"
            )
            backend.decide("hello", _ctx())
        assert captured["headers"]["Authorization"] == "Bearer sk-123"

    def test_strips_a_trailing_slash_on_base_url(self, monkeypatch):
        with _mock_urlopen(monkeypatch, _openai_response(None)) as captured:
            backend = GenericLLMBackend(base_url="https://api.example.com/v1/", model="m")
            backend.decide("hello", _ctx())
        assert captured["url"] == "https://api.example.com/v1/chat/completions"


class TestResponseParsing:
    def test_a_go_to_slide_call_resolves(self, monkeypatch):
        with _mock_urlopen(
            monkeypatch,
            _openai_response("go_to_slide", {"song_index": None, "section_key": "verse:1"}),
        ):
            backend = GenericLLMBackend(base_url="https://x/v1", model="m")
            assert backend.decide("go back to verse one", _ctx()) == ToolCall(
                name="go_to_slide", song_index=None, section_key="verse:1"
            )

    def test_a_search_library_call_resolves(self, monkeypatch):
        with _mock_urlopen(
            monkeypatch,
            _openai_response(
                "search_library", {"query": "Agnus Dei", "section_key": "chorus:1"}
            ),
        ):
            backend = GenericLLMBackend(base_url="https://x/v1", model="m")
            assert backend.decide("bring up agnus dei", _ctx()) == ToolCall(
                name="search_library", query="Agnus Dei", section_key="chorus:1"
            )

    def test_a_no_action_call_resolves_to_none(self, monkeypatch):
        with _mock_urlopen(monkeypatch, _openai_response("no_action")):
            backend = GenericLLMBackend(base_url="https://x/v1", model="m")
            assert backend.decide("amen", _ctx()) is None

    def test_no_tool_calls_at_all_resolves_to_none(self, monkeypatch):
        with _mock_urlopen(monkeypatch, _openai_response(None)):
            backend = GenericLLMBackend(base_url="https://x/v1", model="m")
            assert backend.decide("amen", _ctx()) is None

    def test_the_literal_string_null_is_treated_as_json_null(self, monkeypatch):
        """Small models reliably get the value right and the type wrong —
        measured on llama3.2:3b, see `_parse_tool_call`."""
        with _mock_urlopen(
            monkeypatch,
            _openai_response("go_to_slide", {"song_index": "null", "section_key": "verse:1"}),
        ):
            backend = GenericLLMBackend(base_url="https://x/v1", model="m")
            assert backend.decide("go back to verse one", _ctx()) == ToolCall(
                name="go_to_slide", song_index=None, section_key="verse:1"
            )

    def test_a_response_with_no_choices_resolves_to_none_not_a_crash(self, monkeypatch):
        with _mock_urlopen(monkeypatch, {"choices": []}):
            backend = GenericLLMBackend(base_url="https://x/v1", model="m")
            assert backend.decide("go back to verse one", _ctx()) is None

    def test_a_malformed_response_resolves_to_none_not_a_crash(self, monkeypatch):
        with _mock_urlopen(monkeypatch, {"unexpected": "shape"}):
            backend = GenericLLMBackend(base_url="https://x/v1", model="m")
            assert backend.decide("go back to verse one", _ctx()) is None


class TestNetworkFailure:
    def test_connection_failure_resolves_to_none_not_a_raise(self, monkeypatch):
        with _mock_urlopen(monkeypatch, raises=urllib.error.URLError("connection refused")):
            backend = GenericLLMBackend(base_url="https://x/v1", model="m")
            assert backend.decide("go back to verse one", _ctx()) is None

    def test_timeout_resolves_to_none_not_a_raise(self, monkeypatch):
        with _mock_urlopen(monkeypatch, raises=TimeoutError()):
            backend = GenericLLMBackend(base_url="https://x/v1", model="m")
            assert backend.decide("go back to verse one", _ctx()) is None


class TestOllamaBackendDelegation:
    def test_ollama_backend_hits_the_v1_path_under_its_host(self, monkeypatch):
        """`OllamaBackend` is a thin, named convenience over the generic
        backend — confirms it still points at the right URL after the
        2026-09-03 refactor that consolidated both onto one implementation."""
        with _mock_urlopen(monkeypatch, _openai_response(None)) as captured:
            OllamaBackend(model="qwen2.5:3b-instruct", host="http://localhost:11434").decide(
                "hello", _ctx()
            )
        assert captured["url"] == "http://localhost:11434/v1/chat/completions"
        assert captured["body"]["model"] == "qwen2.5:3b-instruct"
        assert "Authorization" not in captured["headers"]


class TestABodyThatIsNotJSON:
    """`decide` promises it never raises. A body that will not parse broke
    that promise, and the blast radius was not local.

    The old `except (URLError, TimeoutError, OSError)` list omits
    `json.JSONDecodeError` — a `ValueError`, outside all three — so any
    non-JSON body escaped. `decide` runs via `asyncio.to_thread` inside
    `_observe_spoken_instructions`, which the transcript handler awaited
    *before* `director.on_decision(decision)`. A proxy returning an HTML
    error page for an optional side feature therefore dropped the core
    matcher's slide change for that event, silently.

    The existing coverage in this file only ever fed valid JSON of the wrong
    shape, which the separate `KeyError/IndexError/TypeError` guard already
    handled — so the gap was invisible.
    """

    @pytest.mark.parametrize(
        "body, what",
        [
            (b"<html><body>502 Bad Gateway</body></html>", "a proxy error page"),
            (b'{"choices":[{"mess', "a truncated response"),
            (b"", "an empty body"),
            (b"upstream connect error", "a bare text error"),
        ],
    )
    def test_returns_none_instead_of_raising(self, monkeypatch, body, what):
        class _Resp(BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            "urllib.request.urlopen", lambda req, timeout=None, **_kwargs: _Resp(body)
        )
        backend = GenericLLMBackend(base_url="https://api.example.com/v1", model="m")
        assert backend.decide("go to the chorus", _ctx()) is None, what


class TestScriptureTools:
    def _names(self, captured):
        return [t["function"]["name"] for t in captured["body"]["tools"]]

    def test_scripture_tools_are_offered_only_with_a_verse_store(self, monkeypatch):
        backend = GenericLLMBackend(base_url="https://api.example.com/v1", model="m")
        with _mock_urlopen(monkeypatch, _openai_response(None)) as without:
            backend.decide("hello", _ctx())
        assert "show_scripture" not in self._names(without)
        assert "show_scripture" not in without["body"]["messages"][0]["content"]

        with _mock_urlopen(monkeypatch, _openai_response(None)) as with_store:
            backend.decide("hello", replace(_ctx(), scripture=True))
        assert {"show_scripture", "hide_scripture"} <= set(self._names(with_store))
        assert self._names(with_store)[-1] == "no_action"
        assert "show_scripture" in with_store["body"]["messages"][0]["content"]

    def test_show_scripture_parses_reference_and_display(self, monkeypatch):
        body = _openai_response(
            "show_scripture", {"reference": "Jeremiah 29:11", "display": "beside"}
        )
        with _mock_urlopen(monkeypatch, body):
            call = GenericLLMBackend(base_url="https://x/v1", model="m").decide("x", _ctx())
        assert call == ToolCall(
            name="show_scripture", reference="Jeremiah 29:11", display="beside"
        )

    def test_an_unknown_display_means_full_screen(self, monkeypatch):
        body = _openai_response("show_scripture", {"reference": "John 3:16", "display": "sidebar"})
        with _mock_urlopen(monkeypatch, body):
            call = GenericLLMBackend(base_url="https://x/v1", model="m").decide("x", _ctx())
        assert call.display == "full"

    def test_show_scripture_with_no_reference_is_nothing(self, monkeypatch):
        with _mock_urlopen(monkeypatch, _openai_response("show_scripture", {})):
            assert GenericLLMBackend(base_url="https://x/v1", model="m").decide("x", _ctx()) is None

    def test_hide_scripture_parses(self, monkeypatch):
        with _mock_urlopen(monkeypatch, _openai_response("hide_scripture")):
            call = GenericLLMBackend(base_url="https://x/v1", model="m").decide("x", _ctx())
        assert call == ToolCall(name="hide_scripture")
