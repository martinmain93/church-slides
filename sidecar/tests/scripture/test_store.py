"""The verse store, built from a fixture rather than a download.

`build` is exercised directly with a tiny payload in the same shape the real
translation files use, so these tests need no network and no 14 MB file. What
they are actually checking is the boundary: a reference that resolves comes
back with text, and a reference that doesn't fails loudly instead of returning
something adjacent.
"""

from __future__ import annotations

import sqlite3

import pytest

from auto_presenter.scripture import store as store_mod
from auto_presenter.scripture.references import ReferenceError
from auto_presenter.scripture.store import VerseStore, build

PAYLOAD = {
    "books": [
        {
            "name": "John",
            "chapters": [
                {
                    "chapter": 3,
                    "verses": [
                        {"verse": 16, "text": "For God so loved the world, "},
                        {"verse": 17, "text": "For God didn't send his Son to judge"},
                    ],
                }
            ],
        },
        {
            "name": "Psalms",
            "chapters": [
                {
                    "chapter": 103,
                    "verses": [
                        {"verse": 1, "text": "Praise Yahweh, my soul!"},
                        {"verse": 2, "text": "Praise Yahweh, my soul, and don't forget"},
                        {"verse": 3, "text": "who forgives all your sins"},
                    ],
                }
            ],
        },
    ]
}


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """Build the fixture translation into a temp app-data directory."""
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    count = build("test", PAYLOAD)
    assert count == 5
    return tmp_path


def test_resolve_single_verse(installed):
    with VerseStore("test") as store:
        passage = store.resolve("John 3:16")
    assert passage.reference == "John 3:16"
    assert passage.text.startswith("For God so loved")
    assert len(passage.verses) == 1


def test_resolve_range_joins_the_verses(installed):
    with VerseStore("test") as store:
        passage = store.resolve("Psalm 103:1-3")
    assert len(passage.verses) == 3
    assert "forgives all your sins" in passage.text
    assert passage.reference == "Psalm 103:1–3"


def test_verse_that_does_not_exist_raises(installed):
    """Well-formed, entirely fictional. Must fail like a malformed one."""
    with VerseStore("test") as store, pytest.raises(ReferenceError):
        store.resolve("John 3:99")


def test_book_that_does_not_exist_raises(installed):
    with VerseStore("test") as store, pytest.raises(ReferenceError):
        store.resolve("Hezekiah 4:2")


def test_partial_range_returns_what_exists(installed):
    """Asking for 1-9 of a 3-verse chapter shows three verses, not an error."""
    with VerseStore("test") as store:
        passage = store.resolve("Psalm 103:1-9")
    assert len(passage.verses) == 3


def test_search_finds_a_quotation(installed):
    with VerseStore("test") as store:
        hits = store.search("for God so loved the world")
    assert hits and hits[0].reference == "John 3:16"


def test_the_words_as_typed_outrank_a_verse_that_repeats_one_of_them(tmp_path, monkeypatch):
    """Found in the app: "for God so loved" listed four verses of 1 John 4,
    which says "love" and "God" again and again, before John 3:16."""
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    build("t2", {"books": [
        {"name": "John", "chapters": [{"chapter": 3, "verses": [
            {"verse": 16, "text": "For God so loved the world, that he gave his one and only Son"},
        ]}]},
        {"name": "1 John", "chapters": [{"chapter": 4, "verses": [
            {"verse": 8, "text": "He who doesn't love doesn't know God, for God is love. God loved us."},
            {"verse": 16, "text": "God is love, and he who remains in love remains in God, and God loved."},
        ]}]},
    ]})
    with VerseStore("t2") as store:
        assert store.search("for God so loved")[0].reference == "John 3:16"
        # Words with no phrase or all-words match still find something.
        assert store.search("loved world sinners")


def test_search_of_nothing_but_stopwords_is_empty_not_everything(installed):
    with VerseStore("test") as store:
        assert store.search("the and of to") == []


def test_missing_translation_is_a_clear_error(installed):
    with pytest.raises(FileNotFoundError, match="not installed"):
        VerseStore("nope")


def test_build_is_atomic(installed, monkeypatch):
    """A half-finished build must never look installed.

    The staging file is what guarantees it; this checks the guarantee rather
    than the mechanism, by failing mid-build and asserting the previous store
    is still the one on disk.
    """
    good = store_mod.db_path("test").read_bytes()
    # A value SQLite can't bind fails *during* the inserts, i.e. after the
    # staging file exists — which is the only interesting moment. A payload
    # that fails earlier would pass this test without exercising anything.
    from auto_presenter.scripture.importers import ParsedBible, plan

    parsed = ParsedBible("test")
    parsed.book("John").add(1, 1, 1, "first")
    with pytest.raises(sqlite3.Error):
        store_mod.build_from_report("test", plan(parsed), {"name": object()})  # type: ignore[dict-item]
    assert store_mod.db_path("test").read_bytes() == good
    assert not store_mod.db_path("test").with_suffix(".building").exists()


def test_concurrent_installs_of_one_translation_download_once(tmp_path, monkeypatch):
    """Installs run alongside other requests, and two of the same translation
    used to share one `.building` file — the second unlinked it from under the
    first. The second now waits and finds the store already installed."""
    import contextlib
    import io
    import threading
    import zipfile
    import time

    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    downloads = []

    @contextlib.contextmanager
    def slow_get(url, timeout_seconds):
        downloads.append(url)
        time.sleep(0.2)

        class Response:
            def read(self):
                # The built-in translations come as eBible.org USFM packages.
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    archive.writestr("JHN.usfm", "\\id JHN\n\\c 3\n\\v 16 For God so loved the world.")
                return buffer.getvalue()

        yield Response()

    monkeypatch.setattr(store_mod, "open_get", slow_get)
    results, errors = [], []

    def install():
        try:
            results.append(store_mod.ensure_translation("web"))
        except Exception as exc:  # pragma: no cover - the failure being tested
            errors.append(exc)

    threads = [threading.Thread(target=install) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(downloads) == 1
    assert results == ["web", "web", "web"]
    assert store_mod.db_path("web").exists()
    assert not store_mod.db_path("web").with_suffix(".building").exists()
    with VerseStore("web") as store:
        assert store is not None


def test_a_typed_abbreviation_finds_its_book(installed):
    """"joh", "ps" and a full stop are what people type at a keyboard."""
    with VerseStore("test") as store:
        assert store.resolve("joh 3:16").reference == "John 3:16"
        assert store.resolve("ps 103:1").reference == "Psalm 103:1"
        assert store.resolve("John 3.16").reference == "John 3:16"


def test_an_ambiguous_abbreviation_is_refused(tmp_path, monkeypatch):
    """"Jo" is John, Job, Joel, Jonah or Joshua. Guessing would show the wrong one.

    "Phil", by contrast, is the standard abbreviation of Philippians (Philemon
    is "Phlm"), so it resolves.
    """
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    one = {"chapters": [{"chapter": 1, "verses": [{"verse": 1, "text": "x"}]}]}
    build(
        "test",
        {"books": [{"name": n, **one} for n in ("Philippians", "Philemon", "John", "Job")]},
    )
    with VerseStore("test") as store:
        with pytest.raises(ReferenceError):
            store.resolve("Jo 1:1")
        assert store.resolve("Phil 1:1").reference == "Philippians 1:1"
        assert store.resolve("Philip 1:1").reference == "Philippians 1:1"
        assert store.resolve("Phlm 1:1").reference == "Philemon 1:1"
