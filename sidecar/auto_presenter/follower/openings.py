"""Listen for the other songs' opening lines: fast, safe song changes.

Song changes were the production engine's alone, decided from Whisper's free
transcript, and 31% of the remaining wrong time was the wrong song on screen
(30-50 s episodes): on the two multi-song goldens it made 3 of 7 changes,
9-41 s late, and never the other 4.

When a band starts another song it sings one of that song's lines, usually an
opening one, and the span scorer hears a clearly sung line sharply even when
the free transcript is garbage. So every `CADENCE_S` the scorer also scores
each other setlist song's *entry* spans (spans of `MIN_WORDS`+ words ending in
the first `ENTRY_WORDS` words of a slide) beside the current song's own spans.

Raw scores can't be compared across lines: Whisper is also a language model,
and "hallelujah hallelujah i know" scores -2 on almost any audio. Each span is
scored once on silence and once on noise at session start; its *gain* is its
score minus the higher of those (how much the audio added — a pointwise
mutual information). Real sung lines gain +3 to +5.5; background -1 to +1.5.

A song is proposed when one of its entry spans gains `MIN_GAIN` or more and
beats the current song's best gain (all its `MIN_WORDS`+ spans) by `MARGIN`.

Measured offline on all 8 goldens (research/spikes/music_only/song_openings.py,
the production scorer): 7 of 7 golden song changes within 10 s (production 3,
9-41 s late), 0 false proposals in 33 min of single-song audio with 10 decoy
songs and in 23 min of multi-song audio, nothing during talking. Thresholds
were chosen on these recordings and the good region is narrow (gain 2.0-2.5,
margin 1.0-1.5); "2 hits in 3 passes" is the conservative fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from auto_presenter.follower.scorer import WINDOW_S, Scorer, spans_for

log = logging.getLogger(__name__)

#: Every 2 s: a song's first line runs ~4 s, so a 6 s window every 4 s may
#: never hold it whole (the live set's Doxology: caught +1 s at one timing,
#: +31 s at the other). At 2 s, offline: 7/7 within 9 s, total 14 s, 0 false,
#: the same for gain 2.5-2.75 and margin 1.0-1.25. The spans ride along in
#: the score follower's own pass when there is one (one encoding).
CADENCE_S = 2.0
#: Entry spans end in a slide's first `ENTRY_WORDS` words. 12 -> 6 finds the
#: same changes at the same delays with no new false ones (the replay above),
#: with 60 spans instead of 158 on the live set (at 4 it misses 3 of 5). Every
#: span rides along in the follower's GPU pass: 650 spans took 1.2 s.
ENTRY_WORDS = 6
#: At most this many other songs' entry spans per pass: a long setlist takes
#: turns (the next song in the setlist always), rather than growing the pass.
MAX_ENTRY_SPANS = 160
MIN_WORDS = 4
MIN_GAIN = 2.5
#: How far another song's line must beat the current song's best — the
#: "while the song on screen is hardly being heard" half of a song change.
#: Raised from 1.0 on 2026-10-16 after a live set made three wrong mid-song
#: jumps on lines worship songs share, each heard once while the current song
#: still scored: «the son of god» 2.8 vs 1.1, «holy holy holy all» 3.0 vs 1.5,
#: «holy holy holy though» 3.1 vs 2.0 (and, replayed, «holy holy holy all
#: the» 2.8 vs 1.5). Every one beat the current song by 1.7 or less; the real
#: changes seen in real time beat it by 2.1 (Agnus Dei), 2.3 (Doxology), 3.2
#: and 4.5. Requiring 5-word lines instead also stopped them, but cost
#: Doxology 43 s in real time (its opening is short) and still let the 5-word
#: variant through. Offline (research/spikes/music_only/openings_strict.py) the
#: 8 tuning recordings keep 7/7 and 0 false at 2.0.
MARGIN = 2.0
#: Added to both `MIN_GAIN` and `MARGIN` when the line heard is not the
#: song's first sung slide: "are we really sure we want to land mid-song?".
#: A nudge, not a wall: the real mid-song entries on the goldens cleared the
#: bars by 1.1+ (gain 4.1-4.6, lead 3.1-3.2), the false ones heard in sermons
#: by 0.5 (gain 3.3-3.4, lead 2.5-2.6). Tunable `openings_mid_song_nudge`.
MID_SONG_NUDGE = 0.5


@dataclass(frozen=True)
class SongHeard:
    """The band is playing another setlist song."""

    t_seconds: float
    item_idx: int
    slide_idx: int
    span: str
    gain: float
    current_best_gain: float


@dataclass
class _Song:
    item_idx: int
    #: span text -> the first slide carrying it as an entry span
    entry: dict[str, int]
    #: every span of MIN_WORDS+ words
    own: list[str]

    @property
    def first_slide(self) -> int:
        """The song's first sung slide (its usual start)."""
        return min(self.entry.values(), default=0)


def _songs(items: list[tuple[int, list[list[str]]]]) -> list[_Song]:
    out = []
    for item_idx, slides in items:
        entry: dict[str, int] = {}
        own: set[str] = set()
        for (si, j), text in sorted(spans_for(slides).items()):
            if len(text.split()) < MIN_WORDS:
                continue
            own.add(text)
            if j < ENTRY_WORDS and text not in entry:
                entry[text] = si
        if own:
            out.append(_Song(item_idx, entry, sorted(own)))
    return out


def _pink_noise(n: int) -> np.ndarray:
    rng = np.random.default_rng(0)
    white = rng.standard_normal(n + 1)
    pink = np.cumsum(white)[1:] - np.cumsum(white)[:-1] * 0.98
    return (pink / np.abs(pink).max() * 0.05).astype(np.float32)


#: scorer model -> span text -> its prior (`OpeningsWatch.warm_up`)
_PRIORS: dict[object, dict[str, float]] = {}


@dataclass
class OpeningsWatch:
    songs: list[_Song]
    scorer: Scorer
    prior: dict[str, float] = field(default_factory=dict)
    _last_t: float = -1e9
    passes: int = 0
    #: where the long-setlist rotation starts next (`wanted`)
    _turn: int = 0
    #: `MID_SONG_NUDGE`, set from the tunable when the session builds the watch.
    mid_song_nudge: float = MID_SONG_NUDGE
    #: The latest pass, for diagnostics: (t_end, current item, current best
    #: gain, {item: (best entry gain, its span)}).
    last: tuple | None = None

    @classmethod
    def build(
        cls, items: list[tuple[int, list[list[str]]]], scorer: Scorer
    ) -> OpeningsWatch | None:
        """None when there is no other song to listen for."""
        songs = _songs(items)
        if len(songs) < 2:
            return None
        return cls(songs=songs, scorer=scorer)

    def warm_up(self) -> None:
        """Score every span once on silence and on noise: its language-model
        prior. A span's prior depends only on its text, so a setlist edit
        scores only the new songs' spans (`_PRIORS`)."""
        texts = sorted({t for s in self.songs for t in s.own})
        key = getattr(self.scorer, "repo", None) or id(self.scorer)
        known = _PRIORS.setdefault(key, {})
        todo = [t for t in texts if t not in known]
        if todo:
            n = int(WINDOW_S * 16000)
            sil = self.scorer.score(np.zeros(n, np.float32), todo)
            noi = self.scorer.score(_pink_noise(n), todo)
            if sil is None or noi is None:
                return
            for t, a, b in zip(todo, sil.scores, noi.scores, strict=True):
                known[t] = float(max(a, b))
        self.prior = {t: known[t] for t in texts}

    def due(self, t_end: float) -> bool:
        return bool(self.prior) and t_end - self._last_t >= CADENCE_S - 1e-6

    def wanted(self, current_item: int | None) -> list[str]:
        """The spans one pass needs: the current song's and the others' openings."""
        cur = next((s for s in self.songs if s.item_idx == current_item), None)
        others = [s for s in self.songs if s.item_idx != current_item]
        if not others:
            return []
        if sum(len(s.entry) for s in others) > MAX_ENTRY_SPANS:
            # the next song first, then the rest in turns
            after = [s for s in others if current_item is None or s.item_idx > current_item]
            nxt = after[0] if after else others[0]
            rest = [s for s in others if s is not nxt]
            k = self._turn % len(rest) if rest else 0
            rest = rest[k:] + rest[:k]
            picked, n = [nxt], len(nxt.entry)
            for s in rest:
                # the first in turn always comes along (else a song bigger
                # than what's left beside the next one was never heard)
                if n + len(s.entry) > MAX_ENTRY_SPANS and len(picked) > 1:
                    break
                picked.append(s)
                n += len(s.entry)
            self._turn += max(1, len(picked) - 1)
            others = picked
        return sorted({*(cur.own if cur else ()), *(t for s in others for t in s.entry)})

    def listen(self, audio: np.ndarray, t_end: float, current_item: int | None) -> SongHeard | None:
        """One pass of its own over the trailing window: is another song being sung?"""
        texts = self.wanted(current_item)
        if not texts:
            self._last_t = t_end
            return None
        result = self.scorer.score(audio, texts)
        if result is None:
            return None
        return self.hear(
            {t: float(sc) for t, sc in zip(texts, result.scores, strict=True)}, t_end, current_item
        )

    def hear(
        self, scores: dict[str, float], t_end: float, current_item: int | None
    ) -> SongHeard | None:
        """Decide from a pass's span scores (its own, or the follower's with ours added)."""
        self._last_t = t_end
        self.passes += 1
        cur = next((s for s in self.songs if s.item_idx == current_item), None)
        others = [s for s in self.songs if s.item_idx != current_item]
        gain = {t: sc - self.prior.get(t, 0.0) for t, sc in scores.items() if t in self.prior}
        cur_best = max((gain[t] for t in cur.own if t in gain), default=-np.inf) if cur else -np.inf
        best_of = {}
        for s in others:
            cands = [(gain[t], t) for t in s.entry if t in gain]
            if cands:
                best_of[s.item_idx] = max(cands)
        self.last = (t_end, current_item, float(cur_best), best_of)
        return self.decide(gain, t_end, cur, others, self.mid_song_nudge)

    @staticmethod
    def decide(
        gain: dict[str, float],
        t_end: float,
        cur: _Song | None,
        others: list[_Song],
        mid_song_nudge: float = MID_SONG_NUDGE,
    ) -> SongHeard | None:
        if cur is None:
            # The song on screen is not one this watch knows (added after it
            # was built): there is nothing to compare a line against, and
            # "beats nothing" let any line through — live, 2026-10-15, and
            # again 2026-10-17 (What a Beautiful Name, just imported by voice,
            # cut to Build My Life's "Jesus the name above").
            return None
        cur_best = max((gain[t] for t in cur.own if t in gain), default=-np.inf)
        best: SongHeard | None = None
        for s in others:
            first = s.first_slide
            for text, slide in s.entry.items():
                g = gain.get(text)
                nudge = 0.0 if slide == first else mid_song_nudge
                if g is None or g < MIN_GAIN + nudge or g - cur_best < MARGIN + nudge:
                    continue
                if best is None or g > best.gain:
                    best = SongHeard(t_end, s.item_idx, slide, text, g, float(cur_best))
        return best
