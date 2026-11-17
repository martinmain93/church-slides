"""Recover a song that is not in tonight's setlist.

Two callers, one ranking:

  * Spontaneous singing — the band starts a chorus that was never planned.
    The watcher waits until we are still producing sung words, match
    confidence on the setlist has collapsed, and (when the chord analyser
    is on) harmony is present. Then it queries the fingerprint index and
    imports a clear winner.
  * Spoken `search_library` — "let's bring up Agnus Dei". Title-first,
    lyrics as a fallback, same clear-winner bar.

Importing appends the song to the matcher's indexed setlist so later
switches treat it as an ordinary setlist item. The UI is told to persist
the same append so the operator's grid catches up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from auto_presenter.index.fingerprint import (
    CatalogSong,
    FingerprintIndex,
    pick_clear_winner,
)
from auto_presenter.index.library import build_index
from auto_presenter.index.types import IndexedSetlist
from auto_presenter.protocol import SetlistContent, SetlistItemContent


@dataclass(frozen=True)
class ImportSong:
    """Append this library song and land on this slide."""

    song_id: str
    title: str
    slides: tuple[tuple[str, ...], ...]
    slide_sections: tuple[str, ...]
    slide_idx: int
    section_key: str
    query: str
    score: float
    runner_up_score: float
    reason: Literal["spoken", "spontaneous"]
    already_in_setlist: bool = False
    existing_item_idx: int | None = None

    def item_content(self, item_idx: int) -> SetlistItemContent:
        return SetlistItemContent(
            item_idx=item_idx,
            song_id=self.song_id,
            title=self.title,
            slides=[list(s) for s in self.slides],
            slide_sections=list(self.slide_sections),
        )


@dataclass
class LibrarySearchConfig:
    min_score: float = 0.55
    min_gap: float = 0.12
    #: Spontaneous (unprompted) imports need a clearer winner than a spoken
    #: request, which already says the operator wants a song. Raised
    #: 2026-09-28 with the rest of this block after background conversation
    #: moved the screen in live use; the one genuine spontaneous import
    #: measured scored 0.84 against a 0.68 runner-up, so it still passes.
    spontaneous_min_score: float = 0.65
    #: How long the setlist match has to stay collapsed before we search.
    min_lost_seconds: float = 8.0
    max_display_marginal: float = 0.18
    #: Enough words that a fingerprint query is about a line, not a shout.
    min_words: int = 12
    query_window_words: int = 20
    #: Smoothed talking score must stay below this — we want singing, not a
    #: sermon that also fails to match the setlist. Lowered from 0.55 on
    #: 2026-09-28. Sung events run median 0.03 / p95 0.41, and the score
    #: rises when the setlist match is lost (low evidence is one of its two
    #: terms) — exactly when this watcher runs — so going much lower than
    #: this risks refusing a genuinely sung off-setlist song.
    max_talking_score: float = 0.45
    cooldown_seconds: float = 20.0
    #: Chord evidence is required only when the analyser has been producing
    #: it. Rooms with the music panel off still recover from lyrics.
    chord_max_age_seconds: float = 2.5


@dataclass
class OffSetlistWatcher:
    """Decides when a lost, still-singing performance should search the library."""

    index: FingerprintIndex
    config: LibrarySearchConfig = field(default_factory=LibrarySearchConfig)

    _lost_since: float | None = field(default=None, init=False)
    _recent: list[tuple[float, str]] = field(default_factory=list, init=False)
    _cooldown_until: float = field(default=0.0, init=False)

    def reset(self) -> None:
        self._lost_since = None
        self._recent.clear()
        self._cooldown_until = 0.0

    def observe(
        self,
        *,
        words: Sequence[str],
        t_seconds: float,
        display_marginal: float,
        talking_score: float,
        silent: bool,
        setlist: IndexedSetlist,
        chord: str | None,
        chord_t: float | None,
        chords_enabled: bool,
    ) -> ImportSong | None:
        self._recent.extend((t_seconds, w) for w in words)
        if len(self._recent) > 80:
            self._recent = self._recent[-80:]

        if silent or talking_score >= self.config.max_talking_score:
            self._lost_since = None
            return None
        if display_marginal > self.config.max_display_marginal:
            self._lost_since = None
            return None
        if self._lost_since is None:
            self._lost_since = t_seconds
            return None
        if t_seconds - self._lost_since < self.config.min_lost_seconds:
            return None
        if t_seconds < self._cooldown_until:
            return None
        if not self._hearing_music(chords_enabled, chord, chord_t, t_seconds):
            return None

        query = [w for _t, w in self._recent[-self.config.query_window_words :]]
        if len(query) < self.config.min_words:
            return None

        result = search_lyrics(
            self.index, query, setlist=setlist, config=self.config, reason="spontaneous"
        )
        self._cooldown_until = t_seconds + self.config.cooldown_seconds
        self._lost_since = None
        return result

    def _hearing_music(
        self,
        chords_enabled: bool,
        chord: str | None,
        chord_t: float | None,
        now: float,
    ) -> bool:
        if not chords_enabled:
            return True
        if chord is None or chord_t is None:
            return False
        return (now - chord_t) <= self.config.chord_max_age_seconds


def search_title(
    index: FingerprintIndex,
    query: str,
    *,
    setlist: IndexedSetlist,
    section_key: str = "",
    config: LibrarySearchConfig | None = None,
) -> ImportSong | None:
    cfg = config or LibrarySearchConfig()
    hits = index.query_title(query, top_k=8)
    winner = pick_clear_winner(
        [(h.song_id, h.score) for h in hits],
        min_score=cfg.min_score,
        min_gap=cfg.min_gap,
    )
    if winner is None:
        # Title was inconclusive; try the same words as lyrics. Spoken
        # commands may name a song that *is* in the set — don't exclude it.
        return search_lyrics(
            index, query.split(), setlist=setlist, section_key=section_key,
            config=cfg, reason="spoken", query_text=query, exclude_setlist=False,
        )
    song_id, score = winner
    runner = hits[1].score if len(hits) > 1 else 0.0
    return _to_import(
        index, song_id, setlist, section_key=section_key, slide_idx=None,
        query=query, score=score, runner_up=runner, reason="spoken",
    )


def search_lyrics(
    index: FingerprintIndex,
    words: Sequence[str],
    *,
    setlist: IndexedSetlist,
    section_key: str = "",
    config: LibrarySearchConfig | None = None,
    reason: Literal["spoken", "spontaneous"] = "spontaneous",
    query_text: str | None = None,
    exclude_setlist: bool = True,
) -> ImportSong | None:
    cfg = config or LibrarySearchConfig()
    hits = index.query_lyrics(words, top_k=12)
    if exclude_setlist:
        in_set = {item.song_id for item in setlist.items}
        hits = [h for h in hits if h.song_id not in in_set]
    winner = pick_clear_winner(
        [(h.song_id, h.score) for h in hits],
        min_score=(
            max(cfg.min_score, cfg.spontaneous_min_score)
            if reason == "spontaneous" else cfg.min_score
        ),
        min_gap=cfg.min_gap,
    )
    if winner is None:
        return None
    song_id, score = winner
    # Prefer the slide the votes actually landed on, not just "the song".
    best_slide = next((h.slide_idx for h in hits if h.song_id == song_id), None)
    runner = 0.0
    for h in hits:
        if h.song_id != song_id:
            runner = h.score
            break
    return _to_import(
        index, song_id, setlist, section_key=section_key, slide_idx=best_slide,
        query=query_text or " ".join(words), score=score, runner_up=runner,
        reason=reason,
    )


def _to_import(
    index: FingerprintIndex,
    song_id: str,
    setlist: IndexedSetlist,
    *,
    section_key: str,
    slide_idx: int | None,
    query: str,
    score: float,
    runner_up: float,
    reason: str,
) -> ImportSong | None:
    song = index.songs.get(song_id)
    if song is None:
        return None
    existing = _item_idx_for_song(setlist, song_id)
    target = _resolve_slide(song, section_key, slide_idx)
    return ImportSong(
        song_id=song.song_id,
        title=song.title,
        slides=song.slides,
        slide_sections=song.slide_sections,
        slide_idx=target,
        section_key=song.slide_sections[target] if song.slide_sections else "",
        query=query,
        score=score,
        runner_up_score=runner_up,
        reason=reason,
        already_in_setlist=existing is not None,
        existing_item_idx=existing,
    )


def _resolve_slide(song: CatalogSong, section_key: str, slide_idx: int | None) -> int:
    if section_key:
        found = song.slide_idx_for_section(section_key)
        if found is not None:
            return found
    if slide_idx is not None and 0 <= slide_idx < len(song.slides):
        return slide_idx
    return song.first_lyric_idx()


def _item_idx_for_song(setlist: IndexedSetlist, song_id: str) -> int | None:
    for item in setlist.items:
        if item.song_id == song_id:
            return item.item_idx
    return None


def setlist_content_from_indexed(indexed: IndexedSetlist) -> SetlistContent:
    return SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=item.item_idx,
                song_id=item.song_id,
                title=item.title,
                slides=[list(s.words) for s in item.slides],
                slide_sections=[s.section_key for s in item.slides],
            )
            for item in indexed.items
        ]
    )


def append_imported_song(
    indexed: IndexedSetlist, imported: ImportSong
) -> tuple[IndexedSetlist, int]:
    """Return a new index with `imported` appended, and its item_idx.

    If the song is already in the setlist, the index is unchanged.
    """
    if imported.existing_item_idx is not None:
        return indexed, imported.existing_item_idx
    content = setlist_content_from_indexed(indexed)
    item_idx = len(content.items)
    content.items.append(imported.item_content(item_idx))
    return build_index(content), item_idx
