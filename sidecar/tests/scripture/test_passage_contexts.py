"""Offering the passage around a verse, through the engine's handler."""

from __future__ import annotations

import json

import pytest

from auto_presenter import server_scripture as server_mod
from auto_presenter.protocol import PassageContextsRequest
from auto_presenter.scripture import store as store_mod
from auto_presenter.scripture.store import build

PAYLOAD = {"books": [{"name": "John", "chapters": [{"chapter": 3, "verses": [
    {"verse": v, "text": f"John three {v}."} for v in range(1, 37)
]}]}]}


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    build("web", PAYLOAD)


async def _contexts(reference: str) -> list[dict]:
    ws = _FakeWS()
    await server_mod._handle_passage_contexts(ws, PassageContextsRequest(id=1, reference=reference))
    return ws.sent[0]["passages"]


@pytest.mark.asyncio
async def test_a_verse_comes_with_its_section_and_chapter(installed):
    got = await _contexts("john 3:16")
    assert [(p["reference"], p.get("heading")) for p in got] == [
        ("John 3:16–21", None),
        ("John 3:1–21", "Jesus and Nicodemus"),
        ("John 3:1–36", None),
    ]
    assert got[0]["verses"][0]["text"] == "John three 16."
    assert len(got[2]["verses"]) == 36


@pytest.mark.asyncio
async def test_only_a_single_verse_gets_contexts(installed):
    assert await _contexts("John 3:16-18") == []
    assert await _contexts("Hezekiah 4:2") == []


@pytest.mark.asyncio
async def test_no_bible_installed_is_no_contexts(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    assert await _contexts("John 3:16") == []
