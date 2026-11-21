"""Recover a song that is not in tonight's setlist.

Two callers, one ranking:

  * Spontaneous singing — the band starts a chorus that was never planned.
    The watcher keeps asking the fingerprint index, with the setlist's own
    slides in the contest, and imports a library song that keeps winning
    clearly while we hear singing (and, when the chord analyser is on,
    harmony).
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
    fingerprints_from_words,
    pick_clear_winner,
)
from auto_presenter.index.library import build_index
from auto_presenter.index.phonetic import normalize
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
    #: `search_lyrics(reason="spontaneous")` needs a clearer winner than a
    #: spoken request. The watcher no longer calls it (it ranks by votes).
    spontaneous_min_score: float = 0.65
    #: Spontaneous recovery: how often to ask the library, and over how much
    #: of what was just heard.
    check_every_seconds: float = 2.0
    window_seconds: float = 12.0
    #: Content words (3+ letters) in the window before a check is worth it.
    min_words: int = 8
    #: Query fingerprints the winning song's best slide must share...
    min_votes: int = 4
    #: ...spread over at least this many different words. A Whisper loop
    #: ("hi it's hi it's ...") repeats one pair, and a lyric that repeats the
    #: same word matches it; measured 2026-11-20 on a meeting recording.
    min_distinct_words: int = 4
    #: ...and how many times more than any other song, setlist included.
    vote_ratio: float = 2.0
    #: Consecutive winning checks for the same song, at least this far apart
    #: from first to last, before it goes on screen.
    confirm_checks: int = 2
    confirm_span_seconds: float = 3.0
    #: Songs whose matched fingerprints overlap this much (Jaccard) are the
    #: same lines — a library holding "Song" and "Song (2)" — and count as
    #: one. If any of them is in the setlist, the setlist song is being sung.
    same_lines: float = 0.6
    #: Smoothed talking score at or above which no check counts.
    max_talking_score: float = 0.45
    cooldown_seconds: float = 20.0
    #: Chord evidence is required only when the analyser has been producing
    #: it. Rooms with the music panel off still recover from lyrics.
    chord_max_age_seconds: float = 2.5


@dataclass
class OffSetlistWatcher:
    """Notices a song the band is singing that is not in tonight's set.

    It asks the library directly. Every couple of seconds of sound, the last
    few seconds of words are fingerprinted and matched against every library
    song *and* every setlist item. A library song that holds clearly more of
    those word pairs than anything else — the setlist included — and keeps
    winning for a few seconds is imported.

    It used to wait for the tracker to look lost (`display_marginal` under
    0.18 for 8 s) before searching. That never happened on real recordings:
    the marginal is conditional on the top song, so inside the wrong song it
    stays above about one over its slide count (2026-11-20, 7 church
    recordings with the sung song taken out of the setlist: refused on
    93-100% of events, no import in any). Putting the setlist in the contest
    answers the question that gate stood in for — "is this a setlist song?" —
    from the words themselves.
    """

    index: FingerprintIndex
    config: LibrarySearchConfig = field(default_factory=LibrarySearchConfig)

    _recent: list[tuple[float, str]] = field(default_factory=list, init=False)
    _last_check: float = field(default=-1e9, init=False)
    _wins: list[tuple[float, str]] = field(default_factory=list, init=False)
    _cooldown_until: float = field(default=0.0, init=False)
    _setlist_key: object = field(default=None, init=False)
    _setlist_index: FingerprintIndex | None = field(default=None, init=False)

    def reset(self) -> None:
        self._recent.clear()
        self._last_check = -1e9
        self._wins.clear()
        self._cooldown_until = 0.0
        self._setlist_key = None
        self._setlist_index = None

    def observe(
        self,
        *,
        words: Sequence[str],
        t_seconds: float,
        talking_score: float,
        silent: bool,
        setlist: IndexedSetlist,
        chord: str | None,
        chord_t: float | None,
        chords_enabled: bool,
    ) -> ImportSong | None:
        cfg = self.config
        self._recent.extend((t_seconds, w) for w in words)
        self._recent = [(t, w) for t, w in self._recent if t_seconds - t <= cfg.window_seconds]

        if silent or t_seconds - self._last_check < cfg.check_every_seconds:
            return None
        if t_seconds < self._cooldown_until:
            return None
        if talking_score >= cfg.max_talking_score:
            self._wins.clear()
            return None
        if not self._hearing_music(chords_enabled, chord, chord_t, t_seconds):
            return None
        query = [w for _t, w in self._recent]
        if sum(1 for w in query if len(normalize(w)) >= 3) < cfg.min_words:
            return None
        self._last_check = t_seconds

        winner = self._contest(query, setlist)
        if winner is None:
            self._wins.clear()
            return None
        song_id, slide_idx, votes, rival, n_fps = winner
        if self._wins and self._wins[-1][1] != song_id:
            self._wins.clear()
        self._wins.append((t_seconds, song_id))
        if len(self._wins) < cfg.confirm_checks:
            return None
        if t_seconds - self._wins[0][0] < cfg.confirm_span_seconds:
            return None
        self._wins.clear()
        self._cooldown_until = t_seconds + cfg.cooldown_seconds
        return _to_import(
            self.index, song_id, setlist, section_key="", slide_idx=slide_idx,
            query=" ".join(query), score=votes / n_fps, runner_up=rival / n_fps,
            reason="spontaneous",
        )

    def _contest(
        self, query: Sequence[str], setlist: IndexedSetlist
    ) -> tuple[str, int, int, int, int] | None:
        """(song, slide, its votes, best rival's votes, query fingerprints) for
        a clear off-setlist winner, else None."""
        cfg = self.config
        fps = frozenset(fp for fp, _slot in fingerprints_from_words(query))
        if not fps:
            return None
        best: dict[str, tuple[frozenset, int]] = {}

        def take(matches, prefix=""):
            for (sid, slide_idx), m in matches.items():
                sid = sid.removeprefix(prefix)
                if len(m) > len(best.get(sid, (frozenset(), 0))[0]):
                    best[sid] = (frozenset(m), slide_idx)

        take(self.index.slide_matches(fps))
        # The setlist's own slides too: an item may not be a library song, or
        # may have been edited for tonight.
        take(self._setlist_fingerprints(setlist).slide_matches(fps), "set:")
        if not best:
            return None
        in_set = {item.song_id for item in setlist.items}
        ranked = sorted(best.items(), key=lambda kv: (-len(kv[1][0]), kv[0] not in in_set))
        top, (top_m, top_slide) = ranked[0]
        group = {
            sid for sid, (m, _s) in ranked
            if len(top_m & m) / len(top_m | m) >= cfg.same_lines
        }
        if group & in_set:
            return None
        rival = max((len(m) for sid, (m, _s) in ranked if sid not in group), default=0)
        votes = len(top_m)
        if votes < cfg.min_votes or votes < cfg.vote_ratio * rival:
            return None
        if len({code for a, b, _k in top_m for code in (a, b)}) < cfg.min_distinct_words:
            return None
        if top not in self.index.songs:
            return None
        return top, top_slide, votes, rival, len(fps)

    def _setlist_fingerprints(self, setlist: IndexedSetlist) -> FingerprintIndex:
        if self._setlist_key is not setlist or self._setlist_index is None:
            self._setlist_key = setlist
            self._setlist_index = FingerprintIndex.from_songs(
                CatalogSong(
                    song_id="set:" + item.song_id,
                    title=item.title,
                    slides=tuple(slide.words for slide in item.slides),
                    slide_sections=tuple(slide.section_key for slide in item.slides),
                )
                for item in setlist.items
            )
        return self._setlist_index

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
