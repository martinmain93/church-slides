"""Phonetic fingerprint index: retrieval + title search + clear-winner bar."""

from __future__ import annotations

from auto_presenter.decision.library_recovery import (
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


# Spontaneous recovery. Lines below are invented, not from any real song.
LANTERN = catalog_song_from_body(
    "lantern",
    "Harbour Lantern",
    "[verse 1]\nLantern burning on the harbour wall\nguide the weary sailors home tonight\n\n"
    "[chorus]\nShining river carry me along\nevery morning brings a brand new song",
)
LANTERN_COPY = catalog_song_from_body(
    "lantern_2",
    "Harbour Lantern (2)",
    "[verse 1]\nLantern burning on the harbour wall\nguide the weary sailors home tonight",
)
MEADOW = catalog_song_from_body(
    "meadow",
    "Golden Meadow",
    "[verse 1]\nGolden meadow under silver skies\nquiet waters where the swallows fly",
)
SUNG = "lantern burning on the harbour wall guide the weary sailors home tonight".split()
OTHER_SET = [["golden", "meadow", "under", "silver", "skies"]]


def _watch(watcher, setlist, times=(1.0, 3.0, 5.0), words=SUNG, **over):
    kwargs = dict(talking_score=0.1, silent=False, setlist=setlist,
                  chord=None, chord_t=None, chords_enabled=False) | over
    hits = [watcher.observe(words=words, t_seconds=t, **kwargs) for t in times]
    return [h for h in hits if h is not None]


def test_watcher_imports_a_song_that_keeps_winning_off_the_setlist():
    watcher = OffSetlistWatcher(_index(LANTERN, MEADOW))
    hits = _watch(watcher, _setlist(("meadow", OTHER_SET)), times=(1.0, 3.0, 5.0))
    assert len(hits) == 1
    assert hits[0].song_id == "lantern"
    assert hits[0].reason == "spontaneous"
    assert hits[0].score > hits[0].runner_up_score


def test_watcher_needs_more_than_one_win():
    watcher = OffSetlistWatcher(_index(LANTERN, MEADOW))
    assert _watch(watcher, _setlist(("meadow", OTHER_SET)), times=(1.0,)) == []


def test_watcher_leaves_a_setlist_song_alone_even_when_not_in_the_library():
    """The setlist's own slides are in the contest: an item edited for tonight,
    or never saved to the library, still wins over nothing."""
    watcher = OffSetlistWatcher(_index(MEADOW))
    setlist = _setlist(("tonight_only", [SUNG[:6], SUNG[6:]]))
    assert _watch(watcher, setlist) == []


def test_watcher_treats_a_library_copy_of_a_setlist_song_as_that_song():
    watcher = OffSetlistWatcher(_index(LANTERN, LANTERN_COPY, MEADOW))
    assert _watch(watcher, _setlist(("lantern", [list(s) for s in LANTERN.slides]))) == []


def test_watcher_imports_a_song_the_library_holds_twice():
    """Duplicates ("Song", "Song (2)") must not tie each other out."""
    watcher = OffSetlistWatcher(_index(LANTERN, LANTERN_COPY, MEADOW))
    hits = _watch(watcher, _setlist(("meadow", OTHER_SET)))
    assert [h.song_id for h in hits] in (["lantern"], ["lantern_2"])


def test_watcher_will_not_search_during_a_sermon():
    watcher = OffSetlistWatcher(_index(LANTERN, MEADOW))
    assert _watch(watcher, _setlist(("meadow", OTHER_SET)), talking_score=0.9) == []


def test_watcher_needs_enough_words():
    watcher = OffSetlistWatcher(_index(LANTERN, MEADOW))
    assert _watch(watcher, _setlist(("meadow", OTHER_SET)), words=SUNG[:4],
                  times=(1.0, 1.1)) == []


def test_watcher_requires_a_recent_chord_when_the_analyser_is_on():
    watcher = OffSetlistWatcher(_index(LANTERN, MEADOW))
    setlist = _setlist(("meadow", OTHER_SET))
    assert _watch(watcher, setlist, chords_enabled=True) == []
    hits = _watch(watcher, setlist, times=(7.0, 9.0, 11.0), chords_enabled=True,
                  chord="G", chord_t=10.5)
    assert [h.song_id for h in hits] == ["lantern"]


def test_watcher_ignores_a_transcription_loop():
    """Whisper on near-silence repeats itself ("hi it's hi it's ..."); a lyric
    that repeats a word matches that one pair over and over."""
    loop = catalog_song_from_body("loop", "Echo", "[chorus]\nit's yours it's yours it's yours it's yours")
    watcher = OffSetlistWatcher(_index(loop, MEADOW))
    words = "hi it's yours hi it's yours hi it's yours hi it's yours".split()
    assert _watch(watcher, _setlist(("meadow", OTHER_SET)), words=words) == []
