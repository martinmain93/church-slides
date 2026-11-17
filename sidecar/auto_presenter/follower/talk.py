"""Is someone talking rather than singing this song? The follower's own check.

Talk over a song's slides moved the screen: a 2026-10-24 recording of two
people chatting with Reckless Love up made seven slide changes in 2.6 minutes
and nobody sang a note. The span scorer rates some conversation above its
"a line was heard" level (-2.6 and -2.0 against -3.0), which lifts the lyric
hold, and the words themselves pull the belief to whichever lines share them.

`decision.singing.SingingDetector` already recognises talk, but it was built
to *blank* the screen and is deliberately slow and lenient for that: about
six seconds of smoothing and a 0.8 bar. Through the first 40 s of that
conversation it scored 0.4-0.68 and said "singing".

This check is lexical and fast. Over the words that arrived in the last
`WINDOW_S`: talk is *many different words*, almost none of them in a
two-word phrase from this song's lyrics. Singing that Whisper hears well is
full of lyric phrases; singing it hears badly yields few words, or loops of
one hallucinated phrase ("no, no, no, ...", "Silent, Silent, ..."), which is
why it counts distinct words and not words.

Measured on readouts (every 0.5 s) of the 8 live golden traces against that
recording, 8 s window, at least 6 distinct non-stop words, under 20% of words
in lyric phrases: flagged 75% of the talk; 0-1.5% of single songs' singing,
5.6% of the medley, and the live set's real between-song talk. The talk
missed is its pauses, which is why talk *latches*: the screen holds until
this song's words are heard again (`RELEASE_WORDS` words in lyric phrases),
not merely until the talk pauses.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from itertools import pairwise

#: Words too common to make a phrase on their own (`eval.visible.STOP_WORDS`).
STOP_WORDS = frozenset(
    "a an and are as at be but by for from he his i i'm im in is it its me my "
    "of oh on our so that the their there this to us was we were what when "
    "who will with you your yeah ooh whoa ah".split()
)
#: How far back, by arrival, the words are judged.
WINDOW_S = 8.0
#: Talk needs at least this many different non-stop words in the window ...
MIN_DISTINCT = 8
#: ... with fewer than this share of all its words in lyric phrases.
MAX_LYRIC_SHARE = 0.1
#: Words in lyric phrases, arrived since the talk was noticed, that release
#: the latch: a three-word run, or two separate two-word phrases.
RELEASE_WORDS = 3
#: Two words further apart than this (audio time) are not a phrase.
PAIR_GAP_S = 3.0
#: Words kept, by arrival, for the window and the release.
KEEP_S = 30.0


def norm(word: str) -> str:
    return re.sub(r"[^a-z]", "", word.lower())


def lyric_pairs(slides: Iterable[Sequence[str]]) -> frozenset[tuple[str, str]]:
    """Every adjacent word pair in the slides, but pairs of two stop words."""
    out: set[tuple[str, str]] = set()
    for slide in slides:
        ws = [w for w in (norm(x) for x in slide) if w]
        for a, b in pairwise(ws):
            if not (a in STOP_WORDS and b in STOP_WORDS):
                out.add((a, b))
    return frozenset(out)


class TalkGate:
    """Words in, `talking(wall)` out: True while the screen should hold."""

    def __init__(self, pairs: frozenset[tuple[str, str]]):
        self.pairs = pairs
        #: [t_audio, t_arrival, word, in a lyric phrase]
        self._words: list[list] = []
        self.latched = False
        self._latched_at = 0.0
        #: Arrival of the first word heard in a lyric phrase.
        self._ever_lyric: float | None = None

    def push(self, t_audio: float, t_arrival: float, text: str) -> None:
        w = norm(text)
        if not w:
            return
        row = [t_audio, t_arrival, w, False]
        prev = self._words[-1] if self._words else None
        if prev is not None and 0 <= t_audio - prev[0] <= PAIR_GAP_S and (prev[2], w) in self.pairs:
            prev[3] = row[3] = True
            if self._ever_lyric is None or t_arrival < self._ever_lyric:
                self._ever_lyric = t_arrival
        self._words.append(row)

    def note_lyrics(self, t_arrival: float) -> None:
        """A line of this song was heard some other way (the line scorer)."""
        if self._ever_lyric is None or t_arrival < self._ever_lyric:
            self._ever_lyric = t_arrival

    def lyrics_heard(self, wall: float) -> bool:
        """Has any two-word phrase of this song arrived by `wall`?"""
        return self._ever_lyric is not None and self._ever_lyric <= wall

    def talking(self, wall: float) -> bool:
        """Is the screen to hold at `wall` (the follower's audio clock)?"""
        self._words = [r for r in self._words if r[1] > wall - KEEP_S]
        recent = [r for r in self._words if wall - WINDOW_S < r[1] <= wall]
        distinct = {r[2] for r in recent if r[2] not in STOP_WORDS}
        lyric = sum(1 for r in recent if r[3])
        if len(distinct) >= MIN_DISTINCT and lyric < MAX_LYRIC_SHARE * len(recent):
            self.latched, self._latched_at = True, wall
        elif self.latched:
            heard = sum(1 for r in self._words if r[3] and self._latched_at < r[1] <= wall)
            if heard >= RELEASE_WORDS:
                self.latched = False
        return self.latched
