"""Shazam-style phonetic pair fingerprints over a song library.

Scoring every library slide on every transcript event does not fit the
budget — 311 songs, 2828 slides. Retrieval has to be a vote over a sparse
index, then a shortlist gets the real phonemic scorer.

Fingerprints are pairs of nearby Double Metaphone codes plus their offset,
the same trick Phase 8 described. A single common word ("the", "Lord") is
everywhere; the pair "reckless-love-offset-1" is not. The index never
consults phonemes: codes have to be hashable and cheap. Ranking the
shortlist *does* use `phonetic_match_score`, which is phonemic where the
dictionary knows the words.

Title search is a separate path. Spoken commands are mostly title-driven
("bring up Agnus Dei") and ASR will misspell them; we score the query
against each title with the same phonemic metric rather than FTS5, which
needs the spelling to be close.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from auto_presenter.index.phonetic import metaphone, normalize, phonetic_match_score
from auto_presenter.library.parser import tokenize
from auto_presenter.library.sequence import resolve_slide_sequence, section_key_of
from auto_presenter.protocol import SetlistItemContent

#: Pair a code with the next 1..K neighbours. 3 is the Phase 8 default;
#: longer offsets add recall on dropped words and noise on common grammar.
MAX_OFFSET = 3
#: Function words generate millions of single-word hits and almost as many
#: pair hits. Dropping them from fingerprints (not from ranking) keeps the
#: votes about content.
_SKIP = frozenset(
    "a an the and or of to in on at for my your our his her it is be we i you"
    .split()
)


def fingerprints_from_words(words: Sequence[str]) -> list[tuple[tuple[str, str, int], int]]:
    """`(fingerprint, slot)` pairs for one slide's words.

    `fingerprint` is `(code_i, code_{i+k}, k)` with `k in 1..MAX_OFFSET`.
    `slot` is the index of the first word of the pair, so a later rescoring
    pass can still know where in the slide the vote came from.
    """
    codes: list[tuple[int, str]] = []
    for i, word in enumerate(words):
        text = normalize(word)
        if not text or text in _SKIP or len(text) < 3:
            continue
        primary, _secondary = metaphone(text)
        code = primary or text
        if not code:
            continue
        codes.append((i, code))

    out: list[tuple[tuple[str, str, int], int]] = []
    for i, (slot, a) in enumerate(codes):
        for k in range(1, MAX_OFFSET + 1):
            j = i + k
            if j >= len(codes):
                break
            _slot_b, b = codes[j]
            # Offset is in *kept* words, not raw tokens — a dropped "the"
            # between two content words must not change the fingerprint.
            out.append(((a, b, k), slot))
    return out


@dataclass(frozen=True)
class CatalogSong:
    """One library song, expanded the way the matcher will see it."""

    song_id: str
    title: str
    slides: tuple[tuple[str, ...], ...]
    slide_sections: tuple[str, ...]

    def item_content(self, item_idx: int) -> SetlistItemContent:
        return SetlistItemContent(
            item_idx=item_idx,
            song_id=self.song_id,
            title=self.title,
            slides=[list(s) for s in self.slides],
            slide_sections=list(self.slide_sections),
        )

    def first_lyric_idx(self) -> int:
        for i, words in enumerate(self.slides):
            if words:
                return i
        return 0

    def slide_idx_for_section(self, section_key: str) -> int | None:
        for i, key in enumerate(self.slide_sections):
            if key == section_key:
                return i
        return None


def catalog_song_from_body(
    song_id: str, title: str, body: str, slide_order: str | None = None
) -> CatalogSong:
    slides = resolve_slide_sequence(body, song_id, slide_order, leading_blank=True)
    return CatalogSong(
        song_id=song_id,
        title=title,
        slides=tuple(tuple(s.words) for s in slides),
        slide_sections=tuple(section_key_of(s) for s in slides),
    )


@dataclass(frozen=True)
class LyricHit:
    song_id: str
    slide_idx: int
    score: float
    votes: float


@dataclass(frozen=True)
class TitleHit:
    song_id: str
    score: float


@dataclass
class FingerprintIndex:
    """Inverted pair-fingerprint index plus a title list for spoken search."""

    songs: dict[str, CatalogSong] = field(default_factory=dict)
    _postings: dict[tuple[str, str, int], list[tuple[str, int]]] = field(
        default_factory=lambda: defaultdict(list), repr=False
    )

    @classmethod
    def from_songs(cls, songs: Iterable[CatalogSong]) -> FingerprintIndex:
        index = cls()
        for song in songs:
            index.add_song(song)
        return index

    def add_song(self, song: CatalogSong) -> None:
        self.songs[song.song_id] = song
        for slide_idx, words in enumerate(song.slides):
            if not words:
                continue
            for fp, _slot in fingerprints_from_words(words):
                self._postings[fp].append((song.song_id, slide_idx))

    def query_lyrics(
        self, words: Sequence[str], *, top_k: int = 8
    ) -> list[LyricHit]:
        """Vote with fingerprints, then phonemically rescore the shortlist."""
        votes: dict[tuple[str, int], float] = defaultdict(float)
        fps = fingerprints_from_words(words)
        if not fps:
            return []
        for fp, _slot in fps:
            for song_id, slide_idx in self._postings.get(fp, ()):
                votes[(song_id, slide_idx)] += 1.0
        if not votes:
            return []
        # Shortlist by vote count before the expensive rescoring pass.
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])[: max(top_k * 3, 12)]
        observed = [w for w in words if len(normalize(w)) >= 3]
        hits: list[LyricHit] = []
        for (song_id, slide_idx), n in ranked:
            song = self.songs.get(song_id)
            if song is None or slide_idx >= len(song.slides):
                continue
            score = _rescore(observed, song.slides[slide_idx])
            hits.append(LyricHit(song_id, slide_idx, score, n))
        hits.sort(key=lambda h: (h.score, h.votes), reverse=True)
        return hits[:top_k]

    def slide_matches(
        self, fps: frozenset[tuple[str, str, int]]
    ) -> dict[tuple[str, int], set[tuple[str, str, int]]]:
        """Which of the query's fingerprints each slide holds.

        Raw votes, before any rescoring. A slide the band is singing shares a
        run of word pairs with the query; one that only shares a common word
        or two does not, so the count separates them far better than
        `_rescore`, which gives any slide with the same few words ~0.75.
        """
        out: dict[tuple[str, int], set[tuple[str, str, int]]] = defaultdict(set)
        for fp in fps:
            for song_id, slide_idx in self._postings.get(fp, ()):
                out[(song_id, slide_idx)].add(fp)
        return out

    def query_title(self, query: str, *, top_k: int = 8) -> list[TitleHit]:
        q = query.strip()
        if not q:
            return []
        hits = [
            TitleHit(song.song_id, _title_score(q, song.title))
            for song in self.songs.values()
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return [h for h in hits[:top_k] if h.score > 0.0]


def _rescore(observed: Sequence[str], expected: Sequence[str]) -> float:
    if not observed or not expected:
        return 0.0
    scores = [
        max(phonetic_match_score(w, v) for v in expected)
        for w in observed
    ]
    return sum(scores) / len(scores)


def _title_score(query: str, title: str) -> float:
    qw = tokenize(query)
    tw = tokenize(title)
    if not qw or not tw:
        return phonetic_match_score(query, title)
    per_word = [
        max(phonetic_match_score(q, t) for t in tw) for q in qw
    ]
    return sum(per_word) / len(per_word)


def pick_clear_winner(
    scores: Sequence[tuple[str, float]],
    *,
    min_score: float,
    min_gap: float,
) -> tuple[str, float] | None:
    """Return the top id if it is a clear win, else None.

    A tie between two plausible songs must not move the screen — the
    operator can search by hand. One candidate above the bar with the rest
    well below is a win even if the numeric gap is small.
    """
    ranked = sorted(scores, key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < min_score:
        return None
    if len(ranked) == 1:
        return ranked[0]
    if ranked[1][1] < min_score:
        return ranked[0]
    if ranked[0][1] - ranked[1][1] < min_gap:
        return None
    return ranked[0]
