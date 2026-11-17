"""Finding the passages in a pastor's message (`scripture.extract`).

The store is the judge in every test: a phrase or a model's answer only
becomes a passage if the installed Bible holds it.
"""

from __future__ import annotations

import json

import pytest

from auto_presenter import server_scripture as server_mod
from auto_presenter.protocol import FindScriptureReferencesRequest
from auto_presenter.scripture import extract
from auto_presenter.scripture import store as store_mod
from auto_presenter.scripture.store import VerseStore, build


def _chapter(n: int, count: int, text: str) -> dict:
    return {
        "chapter": n,
        "verses": [{"verse": v, "text": f"{text} {v}."} for v in range(1, count + 1)],
    }


PAYLOAD = {
    "books": [
        {"name": "John", "chapters": [_chapter(3, 21, "John three")]},
        {"name": "1 John", "chapters": [_chapter(3, 24, "First John three")]},
        {"name": "Romans", "chapters": [_chapter(5, 21, "Romans five")]},
        {"name": "Ephesians", "chapters": [_chapter(2, 22, "Ephesians two")]},
        {"name": "Psalms", "chapters": [_chapter(23, 6, "Psalm twenty-three")]},
    ]
}


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def installed(store_dir):
    build("web", PAYLOAD)
    return store_dir


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(server_mod, "_hosted_model", lambda: None)


def _refs(text: str) -> list[str]:
    with VerseStore("web") as store:
        return [p.reference for p in extract.read_phrases(text, store.resolve)]


def test_reads_the_passages_out_of_a_message(installed):
    text = "Sermon: John 3:1-21, also Rom 5:8 and Eph 2:8-9. Thanks!"
    assert _refs(text) == ["John 3:1–21", "Romans 5:8", "Ephesians 2:8–9"]


def test_one_per_line_with_labels(installed):
    text = "Call to worship - Psalm 23:1-3\nReading: 1 John 3:16\nBenediction Ephesians 2:10"
    assert _refs(text) == ["Psalm 23:1–3", "1 John 3:16", "Ephesians 2:10"]


def test_a_message_pasted_into_one_line_still_finds_each(installed):
    """A single-line box turns the line breaks into spaces."""
    text = "Call to worship - Psalm 23:1-3 Reading: 1 John 3:16 Benediction Ephesians 2:10"
    assert _refs(text) == ["Psalm 23:1–3", "1 John 3:16", "Ephesians 2:10"]


def test_a_whole_chapter_is_the_verses_that_exist(installed):
    assert _refs("This week we're in Psalm 23") == ["Psalm 23:1–6"]


def test_the_longest_reading_of_a_phrase_wins(installed):
    """"1 John 3:16" must not also produce "John 3:16"."""
    assert _refs("1 John 3:16") == ["1 John 3:16"]


def test_a_passage_named_twice_is_offered_once(installed):
    assert _refs("John 3:16; and again John 3:16") == ["John 3:16"]


def test_prose_without_references_finds_nothing(installed):
    assert _refs("Can we have the lights dimmed for the prayer time?") == []


class TestHandler:
    @pytest.mark.asyncio
    async def test_without_a_model_it_reads_phrase_by_phrase(self, installed, no_model):
        ws = _FakeWS()
        await server_mod._handle_find_scripture_references(
            ws, FindScriptureReferencesRequest(id=1, text="Rom 5:8 and Eph 2:8-9")
        )
        reply = ws.sent[0]
        assert [p["reference"] for p in reply["passages"]] == ["Romans 5:8", "Ephesians 2:8–9"]
        assert reply["used_model"] is False
        assert reply["passages"][0]["verses"][0]["text"] == "Romans five 8."

    @pytest.mark.asyncio
    async def test_a_models_references_are_checked_against_the_bible(self, installed, monkeypatch):
        monkeypatch.setattr(server_mod, "_hosted_model", lambda: ("http://x", "m", "k"))
        monkeypatch.setattr(
            extract, "ask_model",
            lambda text, **_: ["John 3:16", "Hezekiah 4:2", "Psalm 23"],
        )
        ws = _FakeWS()
        await server_mod._handle_find_scripture_references(
            ws, FindScriptureReferencesRequest(id=1, text="anything")
        )
        reply = ws.sent[0]
        assert [p["reference"] for p in reply["passages"]] == ["John 3:16", "Psalm 23:1–6"]
        assert reply["unresolved"] == ["Hezekiah 4:2"]
        assert reply["used_model"] is True

    @pytest.mark.asyncio
    async def test_an_unreachable_model_falls_back_to_phrases(self, installed, monkeypatch):
        monkeypatch.setattr(server_mod, "_hosted_model", lambda: ("http://x", "m", "k"))
        monkeypatch.setattr(extract, "ask_model", lambda text, **_: None)
        ws = _FakeWS()
        await server_mod._handle_find_scripture_references(
            ws, FindScriptureReferencesRequest(id=1, text="John 3:16")
        )
        assert [p["reference"] for p in ws.sent[0]["passages"]] == ["John 3:16"]
        assert ws.sent[0]["used_model"] is False

    @pytest.mark.asyncio
    async def test_no_bible_installed_is_an_empty_answer(self, store_dir, no_model):
        ws = _FakeWS()
        await server_mod._handle_find_scripture_references(
            ws, FindScriptureReferencesRequest(id=1, text="John 3:16")
        )
        assert ws.sent[0]["passages"] == []


def test_the_hosted_model_is_found_only_with_its_key(monkeypatch):
    """The real lookup, not a stand-in: the handler tests above replace it,
    and a wrong import in it once crashed every paste."""
    from auto_presenter import api_keys
    from auto_presenter.decision.instruction_models import DEFAULT_MODEL_ID, find_model
    from auto_presenter.server_state import _state

    monkeypatch.setattr(_state, "instruction_model_id", DEFAULT_MODEL_ID)
    spec = find_model(DEFAULT_MODEL_ID)
    assert spec is not None and spec.kind == "hosted"
    monkeypatch.setattr(api_keys, "get_key", lambda provider: None)
    assert server_mod._hosted_model() is None
    monkeypatch.setattr(api_keys, "get_key", lambda provider: "k")
    assert server_mod._hosted_model() == (spec.base_url, spec.model_name, "k")


def test_ask_model_reads_the_tool_call(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    body = {
        "choices": [{"message": {"tool_calls": [{"function": {
            "name": "propose_passages",
            "arguments": json.dumps({"references": ["John 3:16", " ", "Romans 5:8"]}),
        }}]}}]
    }
    monkeypatch.setattr(extract, "open_json_post", lambda *a, **k: _Resp())
    monkeypatch.setattr(extract, "read_json", lambda resp: body)
    got = extract.ask_model("x", base_url="u", model="m", api_key="k")
    assert got == ["John 3:16", "Romans 5:8"]


def test_ask_model_without_a_tool_call_is_not_an_answer(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(extract, "open_json_post", lambda *a, **k: _Resp())
    monkeypatch.setattr(
        extract, "read_json", lambda resp: {"choices": [{"message": {"content": "Sure!"}}]}
    )
    assert extract.ask_model("x", base_url="u", model="m", api_key="k") is None
