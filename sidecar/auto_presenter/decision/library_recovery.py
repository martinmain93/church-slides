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
    #: Query fingerprints the winning song must share (over all its slides)...
    min_votes: int = 4
    #: ...spread over at least this many different words. A Whisper loop
    #: ("hi it's hi it's ...") repeats one pair, and a lyric that repeats the
    #: same word matches it; measured 2026-11-20 on a meeting recording.
    min_distinct_words: int = 4
    #: ...as at least this share of the query's pairs. Chance wins on talk
    #: hold 6-9% of them, wrong-song wins on singing 14-15%, genuine ones
    #: mostly 22-100% (2026-11-20 probe and speech clips).
    min_share: float = 0.2
    #: ...and how many times more than any other song, setlist included.
    vote_ratio: float = 2.0
    #: Consecutive winning checks for the same song, at least this far apart
    #: from first to last, before it goes on screen.
    confirm_checks: int = 2
    confirm_span_seconds: float = 3.0
    #: Two songs sharing this share of the smaller one's word pairs are the
    #: same song — the library holds 53 such pairs ("Song", "Song (2)",
    #: "Song - Artist") — and count as one. If any of them is in the setlist,
    #: the setlist song is being sung. Judged on whole songs, not on the
    #: query: a library copy can hold a bridge tonight's copy leaves out.
    same_song: float = 0.5
    #: Smoothed talking score at or above which no check counts. Not the
    #: 0.45 of the old watcher: the score rises when the setlist match is
    #: lost (low evidence is one of its terms), which is exactly when an
    #: off-setlist song is being sung, and at 0.45 it held recovery back by
    #: up to 100 s. Sung events run p95 0.41 / p99 0.67 (2026-09-28).
    max_talking_score: float = 0.6
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
    _fps_cache: dict[tuple[str, int], frozenset] = field(default_factory=dict, init=False)

    def reset(self) -> None:
        self._recent.clear()
        self._last_check = -1e9
        self._wins.clear()
        self._cooldown_until = 0.0
        self._setlist_key = None
        self._setlist_index = None
        self._fps_cache.clear()

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
        # Per song, every query pair any of its slides holds. Pooled over the
        # song, not its best slide: twelve seconds of singing often spans two
        # slides, and the same lines split into smaller slides (tonight's copy
        # of a song vs the library's) must not look like a weaker match. That
        # made a library duplicate outvote the setlist copy being sung (two
        # church recordings, 2026-11-20).
        pooled: dict[str, set] = {}
        slide_of: dict[str, tuple[int, int]] = {}

        def take(matches, prefix=""):
            for (sid, slide_idx), m in matches.items():
                sid = sid.removeprefix(prefix)
                pooled.setdefault(sid, set()).update(m)
                if not prefix and len(m) > slide_of.get(sid, (0, -1))[1]:
                    slide_of[sid] = (slide_idx, len(m))

        take(self.index.slide_matches(fps))
        # The setlist's own slides too: an item may not be a library song, or
        # may have been edited for tonight.
        take(self._setlist_fingerprints(setlist).slide_matches(fps), "set:")
        if not pooled:
            return None
        in_set = {item.song_id for item in setlist.items}
        ranked = sorted(pooled.items(), key=lambda kv: (-len(kv[1]), kv[0] not in in_set))
        top, top_m = ranked[0]
        group = {top} | {
            sid for sid, _m in ranked[1:] if self._same_song(top, sid, setlist)
        }
        if group & in_set:
            return None
        rival = max((len(m) for sid, m in ranked if sid not in group), default=0)
        votes = len(top_m)
        if votes < cfg.min_votes or votes < cfg.vote_ratio * rival:
            return None
        if votes < cfg.min_share * len(fps):
            return None
        if len({code for a, b, _k in top_m for code in (a, b)}) < cfg.min_distinct_words:
            return None
        if top not in self.index.songs:
            return None
        return top, slide_of.get(top, (0, 0))[0], votes, rival, len(fps)

    def _same_song(self, a: str, b: str, setlist: IndexedSetlist) -> bool:
        fa, fb = self._song_fps(a, setlist), self._song_fps(b, setlist)
        if not fa or not fb:
            return False
        return len(fa & fb) / min(len(fa), len(fb)) >= self.config.same_song

    def _song_fps(self, song_id: str, setlist: IndexedSetlist) -> frozenset:
        """Every word pair in a song: tonight's copy and the library's."""
        key = (song_id, id(setlist))
        if key not in self._fps_cache:
            fps: set = set()
            set_index = self._setlist_fingerprints(setlist)
            for idx, sid in ((self.index, song_id), (set_index, "set:" + song_id)):
                song = idx.songs.get(sid)
                for words in song.slides if song else ():
                    fps.update(fp for fp, _slot in fingerprints_from_words(words))
            if len(self._fps_cache) > 2000:
                self._fps_cache.clear()
            self._fps_cache[key] = frozenset(fps)
        return self._fps_cache[key]

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
