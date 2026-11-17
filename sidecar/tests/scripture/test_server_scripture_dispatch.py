"""Dispatch tests for the scripture handlers.

`test_protocol.py::test_every_message_is_reachable_through_a_union` already
guarantees these message kinds can arrive at all — a real bug the
spoken-instruction work hit, where a handler was dispatched on but its message
was never added to the union. What these add is the handlers' own contract, and
the part of it that matters most is what happens when things are *missing*: no
translation installed, no API key, a reference that isn't real. A feature that
is off, or half-installed, must degrade to "nothing on screen" rather than to
an exception on the socket.
"""

from __future__ import annotations

import base64
import json

import pytest

from auto_presenter import server_scripture as server_mod
from auto_presenter.protocol import (
    CommitScriptureImportRequest,
    InstallScriptureTranslationRequest,
    ListScriptureTranslationsRequest,
    PreviewScriptureImportRequest,
    RemoveScriptureTranslationRequest,
    ResolvePassagesRequest,
    SearchScriptureRequest,
)
from auto_presenter.scripture import store as store_mod
from auto_presenter.scripture.store import build

PAYLOAD = {
    "books": [
        {
            "name": "Psalms",
            "chapters": [
                {
                    "chapter": 103,
                    "verses": [
                        {"verse": 1, "text": "Praise Yahweh, my soul!"},
                        {"verse": 2, "text": "and don't forget all his benefits"},
                    ],
                }
            ],
        }
    ]
}


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest.fixture
def empty_store_dir(tmp_path, monkeypatch):
    """No translation installed — the state a fresh machine is in."""
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def installed(empty_store_dir, monkeypatch):
    build("web", PAYLOAD)
    return empty_store_dir


class TestResolvePassages:
    @pytest.mark.asyncio
    async def test_resolves_what_it_can_and_reports_what_it_cannot(self, installed):
        """One bad reference must not cost the good ones."""
        ws = _FakeWS()
        await server_mod._handle_resolve_passages(
            ws,
            ResolvePassagesRequest(
                id=1, references=["Psalm 103:1", "Hezekiah 4:2", "not a reference"]
            ),
        )
        reply = ws.sent[0]
        assert [p["reference"] for p in reply["passages"]] == ["Psalm 103:1"]
        assert set(reply["errors"]) == {"Hezekiah 4:2", "not a reference"}
        assert reply["passages"][0]["text"].startswith("Praise Yahweh")
        assert reply["passages"][0]["attribution"]

    @pytest.mark.asyncio
    async def test_range_comes_back_as_one_passage(self, installed):
        ws = _FakeWS()
        await server_mod._handle_resolve_passages(
            ws, ResolvePassagesRequest(id=1, references=["Psalm 103:1-2"])
        )
        passage = ws.sent[0]["passages"][0]
        assert len(passage["verses"]) == 2
        assert passage["reference"] == "Psalm 103:1–2"

    @pytest.mark.asyncio
    async def test_says_which_passage_each_typed_reference_became(self, installed):
        """What was typed is rarely the canonical form ("ps 103:1-2" comes back
        as "Psalm 103:1–2", en dash and all). Keyed only by the canonical
        reference, a typed one never found its passage — and since it *had*
        resolved, it wasn't in `errors` either: it silently never showed."""
        ws = _FakeWS()
        await server_mod._handle_resolve_passages(
            ws, ResolvePassagesRequest(id=1, references=["ps 103:1-2", "Hezekiah 4:2"])
        )
        assert ws.sent[0]["resolved"] == {"ps 103:1-2": "Psalm 103:1–2"}

    @pytest.mark.asyncio
    async def test_no_translation_installed_reports_every_reference_as_an_error(
        self, empty_store_dir
    ):
        """Not an exception on the socket — the caller needs a usable answer."""
        ws = _FakeWS()
        await server_mod._handle_resolve_passages(
            ws, ResolvePassagesRequest(id=1, references=["Psalm 103:1"])
        )
        reply = ws.sent[0]
        assert reply["passages"] == []
        assert "Psalm 103:1" in reply["errors"]


class TestSearchScripture:
    @pytest.mark.asyncio
    async def test_finds_a_quotation(self, installed):
        ws = _FakeWS()
        await server_mod._handle_search_scripture(
            ws, SearchScriptureRequest(id=1, text="praise Yahweh my soul")
        )
        assert ws.sent[0]["passages"][0]["reference"] == "Psalm 103:1"

    @pytest.mark.asyncio
    async def test_without_a_translation_returns_empty_rather_than_failing(
        self, empty_store_dir
    ):
        ws = _FakeWS()
        await server_mod._handle_search_scripture(
            ws, SearchScriptureRequest(id=1, text="anything")
        )
        assert ws.sent[0]["passages"] == []


class TestListTranslations:
    @pytest.mark.asyncio
    async def test_reports_the_catalog_with_install_state(self, installed):
        ws = _FakeWS()
        await server_mod._handle_list_scripture_translations(
            ws, ListScriptureTranslationsRequest(id=1)
        )
        reply = ws.sent[0]
        by_id = {t["id"]: t for t in reply["translations"]}
        assert by_id["web"]["installed"] is True
        assert by_id["kjv"]["installed"] is False
        assert reply["default_translation"] == "web"
        # Every catalog entry carries the credit line it must be shown with.
        assert all(t["attribution"] for t in reply["translations"])


class TestInstallTranslation:
    @pytest.mark.asyncio
    async def test_unknown_translation_is_an_error_not_a_download(
        self, empty_store_dir
    ):
        ws = _FakeWS()
        await server_mod._handle_install_scripture_translation(
            ws, InstallScriptureTranslationRequest(id=1, translation="niv")
        )
        reply = ws.sent[0]
        assert reply["installed"] is False
        assert "unknown translation" in reply["error"]

    @pytest.mark.asyncio
    async def test_already_installed_is_a_no_op_success(self, installed, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("must not re-download an installed translation")

        monkeypatch.setattr(store_mod, "open_get", explode)
        ws = _FakeWS()
        await server_mod._handle_install_scripture_translation(
            ws, InstallScriptureTranslationRequest(id=1, translation="web")
        )
        assert ws.sent[0]["installed"] is True


_IMPORT_FILE = "\n".join(
    [
        "Book,Chapter,Verse,Text",
        "John,3,16,For God so loved the world.",
        "Psalm,23,1,The LORD is my shepherd.",
    ]
).encode()


def _preview(data: bytes = _IMPORT_FILE, name: str = "church-bible.csv"):
    return PreviewScriptureImportRequest(
        id=1, file_name=name, data_base64=base64.b64encode(data).decode()
    )


class TestImportFlow:
    """Preview, then commit — and nothing installed until the commit."""

    @pytest.mark.asyncio
    async def test_preview_reports_without_installing(self, empty_store_dir):
        ws = _FakeWS()
        await server_mod._handle_preview_scripture_import(ws, _preview())
        reply = ws.sent[0]
        assert reply["error"] is None
        assert reply["token"]
        assert reply["report"]["format"] == "CSV"
        assert reply["report"]["verse_count"] == 2
        assert {b["code"] for b in reply["report"]["books"]} == {"JHN", "PSA"}
        assert list(empty_store_dir.glob("*.db")) == []

    @pytest.mark.asyncio
    async def test_commit_installs_and_the_translation_lists_and_resolves(
        self, empty_store_dir
    ):
        ws = _FakeWS()
        await server_mod._handle_preview_scripture_import(ws, _preview())
        token = ws.sent[0]["token"]
        await server_mod._handle_commit_scripture_import(
            ws,
            CommitScriptureImportRequest(
                id=2,
                token=token,
                name="Our Church Bible",
                abbreviation="OCB",
                attribution="Our Church Bible © the publisher. Used by permission.",
                rights_confirmed=True,
            ),
        )
        translation = ws.sent[1]["translation"]
        assert ws.sent[1]["error"] is None
        assert translation == "my-ocb"

        await server_mod._handle_list_scripture_translations(
            ws, ListScriptureTranslationsRequest(id=3)
        )
        listed = {t["id"]: t for t in ws.sent[2]["translations"]}
        assert listed["my-ocb"]["imported"] is True
        assert listed["my-ocb"]["installed"] is True
        assert listed["my-ocb"]["abbreviation"] == "OCB"

        await server_mod._handle_resolve_passages(
            ws, ResolvePassagesRequest(id=4, references=["Ps 23:1"], translation=translation)
        )
        passage = ws.sent[3]["passages"][0]
        assert passage["text"] == "The LORD is my shepherd."
        assert passage["abbreviation"] == "OCB"
        assert passage["attribution"].startswith("Our Church Bible ©")

    @pytest.mark.asyncio
    async def test_commit_needs_the_rights_confirmation_and_a_credit_line(
        self, empty_store_dir
    ):
        ws = _FakeWS()
        await server_mod._handle_preview_scripture_import(ws, _preview())
        token = ws.sent[0]["token"]
        base = dict(id=2, token=token, name="X", abbreviation="X", attribution="credit")
        await server_mod._handle_commit_scripture_import(
            ws, CommitScriptureImportRequest(**base, rights_confirmed=False)
        )
        assert "right to use" in ws.sent[1]["error"]
        await server_mod._handle_commit_scripture_import(
            ws, CommitScriptureImportRequest(**{**base, "attribution": " "}, rights_confirmed=True)
        )
        assert "credit line" in ws.sent[2]["error"]
        assert list(empty_store_dir.glob("*.db")) == []

    @pytest.mark.asyncio
    async def test_an_unknown_token_says_to_choose_the_file_again(self, empty_store_dir):
        ws = _FakeWS()
        await server_mod._handle_commit_scripture_import(
            ws,
            CommitScriptureImportRequest(
                id=1, token="gone", name="X", abbreviation="X", attribution="x",
                rights_confirmed=True,
            ),
        )
        assert "choose the file again" in ws.sent[0]["error"]

    @pytest.mark.asyncio
    async def test_an_unreadable_file_comes_back_as_an_error_not_an_exception(
        self, empty_store_dir
    ):
        ws = _FakeWS()
        await server_mod._handle_preview_scripture_import(
            ws, _preview(b"<html><body>not a bible</body></html>", "page.xml")
        )
        assert ws.sent[0]["report"] is None
        assert "isn't a Bible format" in ws.sent[0]["error"]

    @pytest.mark.asyncio
    async def test_remove(self, installed):
        ws = _FakeWS()
        await server_mod._handle_remove_scripture_translation(
            ws, RemoveScriptureTranslationRequest(id=1, translation="web")
        )
        assert ws.sent[0]["removed"] is True
        assert not (installed / "web.db").exists()
        await server_mod._handle_remove_scripture_translation(
            ws, RemoveScriptureTranslationRequest(id=2, translation="../escape")
        )
        assert ws.sent[1]["removed"] is False
        assert ws.sent[1]["error"]
