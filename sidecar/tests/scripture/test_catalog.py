"""eBible.org's catalogue: public domain only, and the ids it installs under."""

# ruff: noqa: E501 — the CSV fixture rows are kept whole.

from __future__ import annotations

import json

import pytest

from auto_presenter import server_scripture
from auto_presenter.protocol import SearchScriptureCatalogRequest
from auto_presenter.scripture import catalog
from auto_presenter.scripture import store as store_mod

HEADER = (
    '"languageCode","translationId","languageNameInEnglish","title","description",'
    '"Copyright","downloadable","OTbooks","NTbooks","DCbooks"'
)
ROWS = [
    '"eng","engwebp","English","World English Bible","Modern","public domain","True","39","27","0"',
    '"eng","engbsb","English","Berean Standard Bible","Readable","public domain","True","39","27","0"',
    '"eng","engnet","English","NET Bible","Notes","Copyright © 1996-2016","True","39","27","0"',
    '"eng","engamp","English","Amplified","x","Copyright © 2015","False","39","27","0"',
    '"spa","spaRV1909","Spanish","Reina Valera 1909","Clásica","public domain","True","39","27","0"',
    '"deu","deu1912","German, Standard","Luther 1912","x","public domain","True","39","27","0"',
]
CSV = "﻿" + "\n".join([HEADER, *ROWS])


def test_only_public_domain_downloadable_translations_are_offered():
    entries = catalog.parse_catalog(CSV)
    assert [e.id for e in entries] == ["engbsb", "engwebp", "deu1912", "spaRV1909"]


def test_store_ids_and_abbreviations():
    entries = {e.id: e for e in catalog.parse_catalog(CSV)}
    # The built-in ids existing settings already name.
    assert entries["engwebp"].store_id == "web"
    assert entries["engwebp"].abbreviation == "WEB"
    assert entries["engbsb"].store_id == "engbsb"
    assert entries["engbsb"].abbreviation == "BSB"
    assert entries["spaRV1909"].abbreviation == "RV1909"
    assert entries["deu1912"].abbreviation == "DEU1912"


def test_search_by_language_title_or_abbreviation():
    entries = catalog.parse_catalog(CSV)
    assert [e.id for e in catalog.search(entries, "spanish")] == ["spaRV1909"]
    assert [e.id for e in catalog.search(entries, "bsb")] == ["engbsb"]
    assert len(catalog.search(entries, "")) == 4


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest.fixture
def offline_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    monkeypatch.setattr(catalog, "_fetch", lambda url, timeout=60.0: CSV.encode())
    return tmp_path


async def test_search_handler_marks_what_is_installed(offline_catalog):
    store_mod.build(
        "web",
        {
            "books": [
                {
                    "name": "John",
                    "chapters": [
                        {"chapter": 3, "verses": [{"verse": 16, "text": "For God so loved"}]}
                    ],
                }
            ]
        },
    )
    ws = _FakeWS()
    await server_scripture._handle_search_scripture_catalog(
        ws, SearchScriptureCatalogRequest(id=1, query="english")
    )
    entries = {e["id"]: e for e in ws.sent[0]["entries"]}
    assert entries["engwebp"]["installed"] is True
    assert entries["engbsb"]["installed"] is False


def test_a_spoken_translation_name_matches_one_installed(offline_catalog):
    one = {
        "books": [
            {"name": "John", "chapters": [{"chapter": 3, "verses": [{"verse": 16, "text": "x"}]}]}
        ]
    }
    store_mod.build("kjv", one)
    store_mod.build("web", one)
    assert store_mod.match_translation("King James") == "kjv"
    assert store_mod.match_translation("the KJV") == "kjv"
    assert store_mod.match_translation("K.J.V.") == "kjv"
    assert store_mod.match_translation("World English Bible") == "web"
    assert store_mod.match_translation("NIV") is None
