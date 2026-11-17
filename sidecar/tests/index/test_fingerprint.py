"""Phonetic fingerprint index: retrieval + title search + clear-winner bar."""

from __future__ import annotations

from auto_presenter.decision.library_recovery import (
    LibrarySearchConfig,
    OffSetlistWatcher,
    append_imported_song,
    search_lyrics,
    search_title,
)
from auto_presenter.index.fingerprint import (
    FingerprintIndex,
    catalog_song_from_body,
    fingerprints_from_words,
    pick_clear_winner,
)
from auto_presenter.index.library import build_index
from auto_presenter.protocol import SetlistContent, SetlistItemContent

AGNUS = catalog_song_from_body(
    "agnus",
    "Agnus Dei",
    "[chorus]\nHoly holy\nAre you Lord God almighty\n\n[verse 1]\nYou are holy",
)
MERCY = catalog_song_from_body(
    "mercy",
    "Unending Mercy",
    "[chorus]\nOh the deep unending mercy of God\n\n"
    "[verse 1]\nLong before the dawn you were calling out to me",
)
GRACE = catalog_song_from_body(
    "grace",
    "Amazing Grace",
    "[verse 1]\nAmazing grace how sweet the sound\nthat saved a wretch like me",
)


def _index(*songs):
    return FingerprintIndex.from_songs(songs)


def _setlist(*items: tuple[str, list[list[str]]]):
    return build_index(
        SetlistContent(
            items=[
                SetlistItemContent(item_idx=i, song_id=sid, slides=slides, title=sid)
                for i, (sid, slides) in enumerate(items)
            ]
        )
    )


def test_fingerprints_skip_short_function_words():
    fps = fingerprints_from_words(["oh", "the", "deep", "unending", "mercy"])
    assert fps, "content words should still produce pairs"
    assert all(k >= 1 for _fp, _slot in fps for _a, _b, k in [(_fp[0], _fp[1], _fp[2])])


def test_lyric_query_finds_the_sung_slide():
    index = _index(AGNUS, MERCY, GRACE)
    hits = index.query_lyrics(
        "oh the deep unending mercy of god".split()
    )
    assert hits
    assert hits[0].song_id == "mercy"


def test_title_search_survives_a_spelling_mishap():
    index = _index(AGNUS, MERCY, GRACE)
    hits = index.query_title("agnes day")
    assert hits
    assert hits[0].song_id == "agnus"
    hits2 = index.query_title("unendin mersy")
    assert hits2[0].song_id == "mercy"


def test_clear_winner_rejects_a_tie():
    assert pick_clear_winner(
        [("a", 0.7), ("b", 0.68)], min_score=0.55, min_gap=0.12
    ) is None
    assert pick_clear_winner(
        [("a", 0.8), ("b", 0.4)], min_score=0.55, min_gap=0.12
    ) == ("a", 0.8)


def test_spoken_title_search_imports_a_song_not_in_the_set():
    index = _index(AGNUS, MERCY, GRACE)
    setlist = _setlist(("grace", [["amazing", "grace"]]))
    found = search_title(index, "Agnus Dei", setlist=setlist, section_key="chorus:1")
    assert found is not None
    assert found.song_id == "agnus"
    assert found.already_in_setlist is False
    assert found.section_key == "chorus:1"
    indexed, item_idx = append_imported_song(build_index(SetlistContent(items=[
        SetlistItemContent(item_idx=0, song_id="grace", slides=[["amazing"]], title="Amazing Grace")
    ])), found)
    assert item_idx == 1
    assert indexed.item(1).song_id == "agnus"


def test_spoken_title_of_a_setlist_song_is_flagged_already_in():
    index = _index(AGNUS, GRACE)
    setlist = _setlist(("agnus", [["holy", "holy"]]))
    found = search_title(index, "Agnus Dei", setlist=setlist)
    assert found is not None
    assert found.already_in_setlist is True
    assert found.existing_item_idx == 0


def test_spontaneous_search_ignores_songs_already_in_the_set():
    index = _index(MERCY, GRACE)
    setlist = _setlist(("mercy", [["oh", "the", "deep", "unending", "mercy"]]))
    found = search_lyrics(
        index, "oh the deep unending mercy of god".split(),
        setlist=setlist, reason="spontaneous",
    )
    assert found is None


def test_watcher_needs_a_sustained_lost_singing_window():
    index = _index(MERCY, GRACE)
    watcher = OffSetlistWatcher(index, LibrarySearchConfig(min_lost_seconds=2.0, min_words=4))
    setlist = _setlist(("grace", [["amazing", "grace"]]))
    words = "oh the deep unending mercy of god".split()
    kwargs = dict(
        display_marginal=0.05,
        talking_score=0.2,
        silent=False,
        setlist=setlist,
        chord=None,
        chord_t=None,
        chords_enabled=False,
    )
    assert watcher.observe(words=words, t_seconds=1.0, **kwargs) is None
    hit = watcher.observe(words=words, t_seconds=4.0, **kwargs)
    assert hit is not None
    assert hit.song_id == "mercy"
    assert hit.reason == "spontaneous"


def test_watcher_will_not_search_during_a_sermon():
    index = _index(MERCY)
    watcher = OffSetlistWatcher(index, LibrarySearchConfig(min_lost_seconds=1.0, min_words=4))
    setlist = _setlist(("grace", [["amazing"]]))
    kwargs = dict(
        words="oh the deep unending mercy".split(),
        display_marginal=0.05,
        talking_score=0.9,
        silent=False,
        setlist=setlist,
        chord=None,
        chord_t=None,
        chords_enabled=False,
    )
    assert watcher.observe(t_seconds=1.0, **kwargs) is None
    assert watcher.observe(t_seconds=5.0, **kwargs) is None


def test_watcher_default_refuses_conversation_that_is_only_half_talky():
    """Reported live 2026-09-28: background conversation moved the screen. A
    smoothed talking score of 0.5 passed the old 0.55 bar; sung events run
    median 0.03 / p95 0.41."""
    index = _index(MERCY)
    watcher = OffSetlistWatcher(index, LibrarySearchConfig(min_lost_seconds=1.0, min_words=4))
    setlist = _setlist(("grace", [["amazing"]]))
    kwargs = dict(
        words="oh the deep unending mercy".split(),
        display_marginal=0.05,
        talking_score=0.5,
        silent=False,
        setlist=setlist,
        chord=None,
        chord_t=None,
        chords_enabled=False,
    )
    assert watcher.observe(t_seconds=1.0, **kwargs) is None
    assert watcher.observe(t_seconds=5.0, **kwargs) is None


def test_watcher_requires_a_recent_chord_when_the_analyser_is_on():
    index = _index(MERCY)
    watcher = OffSetlistWatcher(index, LibrarySearchConfig(min_lost_seconds=1.0, min_words=4))
    setlist = _setlist(("grace", [["amazing"]]))
    words = "oh the deep unending mercy of god".split()
    kwargs = dict(
        words=words,
        display_marginal=0.05,
        talking_score=0.2,
        silent=False,
        setlist=setlist,
        chords_enabled=True,
    )
    assert watcher.observe(t_seconds=1.0, chord=None, chord_t=None, **kwargs) is None
    assert watcher.observe(t_seconds=3.0, chord=None, chord_t=None, **kwargs) is None
    hit = watcher.observe(t_seconds=3.1, chord="G", chord_t=3.0, **kwargs)
    assert hit is not None
