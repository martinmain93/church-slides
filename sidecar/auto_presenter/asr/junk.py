"""Drop words Whisper itself tells us are not words.

Traced 2026-09-29 (rec_20260929-231312_993): a near-silent opening came back
from Whisper as a lone "!" — word probability 0.00, segment avg_logprob NaN,
no-speech 0.51. Nothing downstream dropped it. The matcher strips punctuation,
so it became an empty word that matched nothing, yet it still counted as one
observed word: it stepped every hypothesis forward, fed the word rate, and was
the first "evidence" of a session whose song choice it then decided.

Two rules, deliberately unequal:

  * **No content.** A token with no letters or digits once punctuation is
    stripped ("!", "...", "♪") cannot match a lyric. Always dropped.
  * **Whisper doubts the word *and* nobody seems to be singing.** Sung words
    routinely carry low word probability — a sustained vowel over a band is
    not what Whisper was trained on — so low probability alone is not a
    reason to drop anything. It takes a very low word probability *and* the
    app's own singing detector not believing anyone is singing. That second
    half lives downstream of the ASR wrapper, so it is `DoubtfulWordFilter`,
    applied by the server to what the matcher sees and nothing else.

    Whisper's own no-speech probability was the first candidate for "not
    singing" and does not work — see `JunkFilter.min_no_speech`.
"""

from __future__ import annotations

from dataclasses import dataclass

from auto_presenter.asr.types import Word


def has_content(text: str) -> bool:
    """Does any letter or digit survive? Apostrophes alone do not count."""
    return any(c.isalnum() for c in text)


@dataclass(frozen=True)
class JunkFilter:
    #: Word probability at or below which a word is suspect.
    max_word_probability: float = 0.05
    #: Segment no-speech probability at or above which a suspect word is
    #: dropped. `None` (the default) disables this rule: measured 2026-09-30
    #: on rec_20260929-231312_993, clearly *sung* words routinely read
    #: no-speech 0.5-0.74 — the same as the stray "!" (0.52) and a spoken
    #: "Thank you" (0.70) — while real talking read as low as 0.21. Whisper's
    #: no-speech does not separate singing from anything here.
    min_no_speech: float | None = None

    def is_junk(self, word: Word) -> bool:
        if not has_content(word.text):
            return True
        return (
            self.min_no_speech is not None
            and word.no_speech is not None
            and word.confidence <= self.max_word_probability
            and word.no_speech >= self.min_no_speech
        )

    def keep(self, words: list[Word]) -> list[Word]:
        return [w for w in words if not self.is_junk(w)]


@dataclass(frozen=True)
class DoubtfulWordFilter:
    """Very low word probability while the room does not sound like singing.

    Measured 2026-09-30 with live windowing (MLX small.en): over 627 words of
    a ten-minute sung recording, 0.16% sat at probability <= 0.02 — a single
    "Music", itself a Whisper artifact. Talking in the 2026-09-29 sessions
    sat right there: "Thank" 0.004 and 0.013, "Okay." 0.010. Words between
    0.02 and 0.1 are real sung words ("a", "of", "Jesus'") and are kept.

    Applied only to the matcher's copy of an event. The singing detector and
    spoken instructions keep every word — a command can transcribe with low
    probability, and removing talk from the detector's input would make talk
    look less like talk.
    """

    max_word_probability: float = 0.02
    #: Smoothed talking score at or above which the room is taken as not
    #: singing. About the 90th percentile of real singing (0.29).
    min_talking_score: float = 0.3

    def keep(
        self,
        words: list[Word],
        *,
        talking_score: float,
        silent: bool,
        singing_established: bool,
    ) -> list[Word]:
        not_singing = (
            silent
            or not singing_established
            or talking_score >= self.min_talking_score
        )
        if not not_singing:
            return list(words)
        return [w for w in words if w.confidence > self.max_word_probability]
