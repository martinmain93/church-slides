"""Dispatch tests for the spoken-instructions model catalog handlers.

Written alongside fixing a real bug: `SetSpokenInstructionsRequest` was
defined, imported, and dispatched on, but never added to the `ClientMessage`
union the server actually validates incoming frames against — so no real
client message of that kind could ever arrive, even though a test that
called the handler directly (bypassing validation, as tests calling
`_handle_*` functions by name always do) would look like it worked. See
`test_protocol.py::test_every_message_is_reachable_through_a_union`, which
catches that class of bug generically. These tests exercise the handlers
themselves — the two together are what "this actually works end to end"
requires.
"""

from __future__ import annotations

import json

import pytest

from auto_presenter import server_instructions as server_mod
from auto_presenter.decision import ollama_manager
from auto_presenter.decision.instruction_models import DEFAULT_MODEL_ID
from auto_presenter.decision.instructions import (
    BackendUnavailable,
    FallbackBackend,
    GenericLLMBackend,
    InstructionEngine,
    OllamaBackend,
)
from auto_presenter.protocol import (
    DownloadInstructionModelRequest,
    ListInstructionModelsRequest,
    SetInstructionModelApiKeyRequest,
    SetInstructionModelRequest,
)


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Keep every test off the real ollama server and the real app data dir."""
    monkeypatch.setattr(
        "auto_presenter.api_keys.app_data_dir", lambda: tmp_path
    )
    monkeypatch.delenv("AUTO_PRESENTER_GROQ_API_KEY", raising=False)
    monkeypatch.delenv("AUTO_PRESENTER_GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(server_mod._state, "instruction_model_id", DEFAULT_MODEL_ID)
    monkeypatch.setattr(server_mod._state, "spoken_instructions_enabled", False)
    monkeypatch.setattr(server_mod._state, "instruction_engine", None)


class TestListInstructionModels:
    @pytest.mark.asyncio
    async def test_reports_every_catalog_entry_and_the_active_id(self, monkeypatch):
        monkeypatch.setattr(ollama_manager, "is_available", lambda: True)
        monkeypatch.setattr(ollama_manager, "installed_models", lambda: {"llama3.2:3b"})
        ws = _FakeWS()
        await server_mod._handle_list_instruction_models(ws, ListInstructionModelsRequest(id=1))
        body = ws.sent[0]
        assert body["kind"] == "list_instruction_models_response"
        assert body["active_model_id"] == DEFAULT_MODEL_ID
        assert body["ollama_available"] is True
        by_id = {m["id"]: m for m in body["models"]}
        assert by_id["llama3.2:3b"]["installed"] is True
        assert by_id["qwen2.5:3b-instruct"]["installed"] is False

    @pytest.mark.asyncio
    async def test_reports_ollama_unavailable_distinctly_from_a_model_not_installed(
        self, monkeypatch
    ):
        monkeypatch.setattr(ollama_manager, "is_available", lambda: False)
        monkeypatch.setattr(ollama_manager, "installed_models", lambda: set())
        ws = _FakeWS()
        await server_mod._handle_list_instruction_models(ws, ListInstructionModelsRequest(id=1))
        body = ws.sent[0]
        assert body["ollama_available"] is False

    @pytest.mark.asyncio
    async def test_hosted_models_report_whether_a_key_is_on_file(self, monkeypatch):
        monkeypatch.setattr(ollama_manager, "is_available", lambda: True)
        monkeypatch.setattr(ollama_manager, "installed_models", lambda: set())
        from auto_presenter.api_keys import save_key

        save_key("groq", "gk_test")
        ws = _FakeWS()
        await server_mod._handle_list_instruction_models(ws, ListInstructionModelsRequest(id=1))
        by_id = {m["id"]: m for m in ws.sent[0]["models"]}
        groq_entries = [m for m in by_id.values() if m["provider"] == "groq"]
        assert groq_entries and groq_entries[0]["has_api_key"] is True


class TestSetInstructionModel:
    @pytest.mark.asyncio
    async def test_switches_the_active_model(self):
        ws = _FakeWS()
        await server_mod._handle_set_instruction_model(
            ws, SetInstructionModelRequest(id=1, model_id="qwen2.5:7b-instruct")
        )
        assert server_mod._state.instruction_model_id == "qwen2.5:7b-instruct"
        assert ws.sent[0]["kind"] == "pong"

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_model_id_without_changing_state(self):
        ws = _FakeWS()
        await server_mod._handle_set_instruction_model(
            ws, SetInstructionModelRequest(id=1, model_id="not-a-real-model")
        )
        assert server_mod._state.instruction_model_id == DEFAULT_MODEL_ID
        assert ws.sent[0]["kind"] == "error"

    @pytest.mark.asyncio
    async def test_rebuilds_the_engine_immediately_if_already_enabled(self, monkeypatch):
        monkeypatch.setattr(server_mod._state, "spoken_instructions_enabled", True)
        monkeypatch.setattr(
            server_mod._state, "instruction_engine", InstructionEngine(backend=None)
        )
        ws = _FakeWS()
        await server_mod._handle_set_instruction_model(
            ws, SetInstructionModelRequest(id=1, model_id="llama3.2:3b")
        )
        # A local model needs no key or availability check to *build* a
        # backend for — only calling it can fail — so the engine should be
        # a fresh, real one now, not the placeholder.
        assert server_mod._state.instruction_engine is not None
        assert server_mod._state.instruction_engine.backend is not None

    @pytest.mark.asyncio
    async def test_does_not_build_an_engine_if_the_feature_is_off(self):
        ws = _FakeWS()
        await server_mod._handle_set_instruction_model(
            ws, SetInstructionModelRequest(id=1, model_id="llama3.2:3b")
        )
        assert server_mod._state.instruction_engine is None


class TestDownloadInstructionModel:
    @pytest.mark.asyncio
    async def test_rejects_a_hosted_model_id(self):
        ws = _FakeWS()
        await server_mod._handle_download_instruction_model(
            ws, DownloadInstructionModelRequest(id=1, model_id="groq-llama-3.1-8b-instant")
        )
        assert ws.sent[0]["kind"] == "error"

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_model_id(self):
        ws = _FakeWS()
        await server_mod._handle_download_instruction_model(
            ws, DownloadInstructionModelRequest(id=1, model_id="not-a-real-model")
        )
        assert ws.sent[0]["kind"] == "error"

    @pytest.mark.asyncio
    async def test_pulls_the_local_model_and_broadcasts_progress(self, monkeypatch):
        calls = []

        def fake_pull(pull_name, *, progress_callback=None):
            calls.append(pull_name)
            if progress_callback:
                progress_callback(
                    ollama_manager.PullProgressEvent("llama3.2:3b", 1, 1, "completed", "done")
                )

        monkeypatch.setattr(ollama_manager, "pull", fake_pull)
        broadcasts = []

        async def fake_broadcast(msg):
            broadcasts.append(msg)

        monkeypatch.setattr(server_mod, "_broadcast", fake_broadcast)
        ws = _FakeWS()
        await server_mod._handle_download_instruction_model(
            ws, DownloadInstructionModelRequest(id=1, model_id="llama3.2:3b")
        )
        assert calls == ["llama3.2:3b"]
        assert any(
            getattr(b, "kind", None) == "instruction_model_download_progress"
            and b.state == "completed"
            for b in broadcasts
        )


class TestSetInstructionModelApiKey:
    @pytest.mark.asyncio
    async def test_saves_the_key(self):
        from auto_presenter.api_keys import get_key

        ws = _FakeWS()
        await server_mod._handle_set_instruction_model_api_key(
            ws, SetInstructionModelApiKeyRequest(id=1, provider="groq", api_key="gk_new")
        )
        assert get_key("groq") == "gk_new"
        assert ws.sent[0]["kind"] == "pong"

    @pytest.mark.asyncio
    async def test_rebuilds_the_engine_when_the_active_hosted_model_gets_its_key(
        self, monkeypatch
    ):
        monkeypatch.setattr(server_mod._state, "instruction_model_id", "groq-llama-3.1-8b-instant")
        monkeypatch.setattr(server_mod._state, "spoken_instructions_enabled", True)
        monkeypatch.setattr(server_mod._state, "instruction_engine", None)
        ws = _FakeWS()
        await server_mod._handle_set_instruction_model_api_key(
            ws, SetInstructionModelApiKeyRequest(id=1, provider="groq", api_key="gk_new")
        )
        assert server_mod._state.instruction_engine is not None

    @pytest.mark.asyncio
    async def test_does_not_rebuild_for_a_different_providers_key(self, monkeypatch):
        monkeypatch.setattr(server_mod._state, "instruction_model_id", "groq-llama-3.1-8b-instant")
        monkeypatch.setattr(server_mod._state, "spoken_instructions_enabled", True)
        monkeypatch.setattr(server_mod._state, "instruction_engine", None)
        ws = _FakeWS()
        await server_mod._handle_set_instruction_model_api_key(
            ws, SetInstructionModelApiKeyRequest(id=1, provider="gemini", api_key="gm_new")
        )
        assert server_mod._state.instruction_engine is None


def test_the_default_still_works_with_no_key_and_no_network(monkeypatch):
    """The invariant the old `test_default_model_is_local` was protecting.

    The shipped default is hosted now — it measured 15/15 against the best
    local model's 14/15 and four times faster — but trying the feature must
    still not require a key. With none on file the builder falls all the way
    back to the local model rather than returning nothing, because a hosted
    default that silently does nothing until someone pastes a key is worse
    than quietly running on-device.
    """
    monkeypatch.setattr(server_mod, "get_instruction_api_key", lambda _p: None)
    monkeypatch.setattr(
        ollama_manager, "installed_models", lambda: {server_mod.FALLBACK_MODEL_ID}
    )

    backend = server_mod._build_instruction_backend(DEFAULT_MODEL_ID)
    assert backend is not None, "no key must not disable the feature"
    assert isinstance(backend, OllamaBackend)


def test_a_key_gets_the_hosted_model_with_the_local_one_behind_it(monkeypatch):
    monkeypatch.setattr(server_mod, "get_instruction_api_key", lambda _p: "key-123")
    monkeypatch.setattr(
        ollama_manager, "installed_models", lambda: {server_mod.FALLBACK_MODEL_ID}
    )

    backend = server_mod._build_instruction_backend(DEFAULT_MODEL_ID)
    assert isinstance(backend, FallbackBackend)
    assert isinstance(backend.primary, GenericLLMBackend)
    assert isinstance(backend.secondary, OllamaBackend)


def test_an_unreachable_hosted_model_falls_through_but_a_refusal_does_not(monkeypatch):
    """The distinction the whole fallback rests on.

    `decide` returns None both when the model declines and when the request
    never reached it. Re-asking a weaker model to second-guess a considered
    refusal invites it to invent an action the better one correctly declined —
    and most of what this feature hears is not a request.
    """
    asked: list[str] = []

    class Primary:
        def decide_strict(self, utterance, context):
            asked.append("primary")
            if utterance == "unreachable":
                raise BackendUnavailable("503")
            return None  # a considered refusal

        def decide(self, utterance, context):  # pragma: no cover - unused
            raise AssertionError("FallbackBackend must use the strict path")

    class Secondary:
        def decide(self, utterance, context):
            asked.append("secondary")
            return None

    backend = FallbackBackend(primary=Primary(), secondary=Secondary())

    asked.clear()
    backend.decide("unreachable", None)
    assert asked == ["primary", "secondary"], "an unreachable primary must fall through"

    asked.clear()
    backend.decide("just chatting", None)
    assert asked == ["primary"], "a refusal must not be second-guessed"
