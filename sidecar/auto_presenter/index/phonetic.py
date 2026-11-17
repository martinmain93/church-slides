"""Phonetic helpers for the particle filter's emission likelihood.

Worship lyrics rhyme, repeat, and slur — and Whisper hallucinates with
similar acoustic confusion. A literal string match is too brittle: an
operator hears "hosanna" and the ASR transcribes "hose anna" or
"oh-sanna"; if the matcher only compares letter-by-letter it loses the
slide. Double Metaphone collapses both spellings to the same code
(`HSN`), so a phonetic-aware likelihood gives the right slide enough
weight to stay locked.

This module is intentionally small:

  - `normalize(word)` strips punctuation + case + smart quotes so two
    spellings of the same sounded word look the same before encoding.
  - `metaphone(word)` returns the upstream Double Metaphone pair
    `(primary, secondary)` from the `metaphone` package.
  - `phonetic_match_score(observed, expected)` is the actual emission
    likelihood building block: 1.0 for an identical normalized word,
    high for a metaphone-primary match, moderate for a secondary or
    cross match, and a continuous fallback that uses normalized edit
    distance when neither metaphone code exists or matches.

The score is clamped to `[0, 1]`. Phase 6's particle filter applies an
additional floor (`emission_floor`, default 0.05) at the *likelihood*
layer so a single mismatched word can't crash an entire particle's
weight — see `tracker/emission.py`.
"""

from __future__ import annotations

import string
from functools import lru_cache

from metaphone import doublemetaphone
from rapidfuzz.distance import Levenshtein

_SMART_QUOTES = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
    }
)

_PUNCT_STRIP = string.punctuation.replace("'", "") + '"'


def normalize(word: str) -> str:
    """Lowercase, strip outer punctuation (preserve interior apostrophes).

    Matches `library.parser.tokenize` semantics on a single word so the
    same input survives to the same encoded form, whether it came from
    the song lyrics or the ASR transcript.
    """
    if not word:
        return ""
    text = word.translate(_SMART_QUOTES).lower().strip()
    if not text:
        return ""
    # Strip outer punctuation but keep interior apostrophes (it's, don't).
    text = text.strip(_PUNCT_STRIP)
    text = text.strip("'")
    return text


@lru_cache(maxsize=4096)
def metaphone(word: str) -> tuple[str, str]:
    """Double Metaphone codes for a word.

    Cached because the same lyric words appear many thousands of times
    across a setlist and the encoder is hot in the predict/update loop.
    Returns `("", "")` for empty input.
    """
    text = normalize(word)
    if not text:
        return ("", "")
    primary, secondary = doublemetaphone(text)
    return (primary or "", secondary or "")


@lru_cache(maxsize=65536)
def metaphone_match_score(observed: str, expected: str) -> float:
    """Phonetic similarity between two words, in `[0, 1]`.

    Scoring tiers (highest first):

      1.0  — identical normalized text.
      0.9  — primary metaphone codes match (e.g. "hosanna" / "hose-anna").
      0.7  — secondary code match or a primary-vs-secondary cross match
             (common for ambiguous-stress vowels).
      *    — fallback: `1 - Levenshtein(observed, expected) / max(len)`,
             clipped to `[0, 0.6]` so phonetic matches always outrank
             pure string proximity.
      0.0  — either side is empty after normalization.

    The tiered structure keeps the score interpretable: the particle
    filter's emission kernel applies a linear floor (`0.05 + 0.95 * s`)
    on top, so a 0-output here means "this word is no evidence."

    Spelling-based, and kept as the fallback rather than the primary: it never
    consults a pronunciation, so it is right about "knight" by luck of its rule
    set. `index.phonemic` uses a real dictionary where one applies and delegates
    here where it does not — which is the right way round, since for a word no
    dictionary knows, spelling is all the evidence there is.

    Cached on the word pair. This is the hottest function in the system —
    every update scores every particle against a window of slide words, so it
    runs tens of thousands of times per transcript event — and both arguments
    come from a small fixed vocabulary (the setlist's lyrics crossed with what
    the ASR emits), so the cache hits almost every time after the first pass
    through a song. Pure and deterministic, which is what makes memoizing it
    safe.
    """
    a = normalize(observed)
    b = normalize(expected)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    a_primary, a_secondary = metaphone(a)
    b_primary, b_secondary = metaphone(b)

    if a_primary and a_primary == b_primary:
        return 0.9
    cross_match = (a_primary and a_primary == b_secondary) or (
        a_secondary and a_secondary == b_primary
    )
    if cross_match:
        return 0.7
    if a_secondary and a_secondary == b_secondary:
        return 0.7

    # Fallback to letter-level similarity, capped so it never beats a
    # phonetic tier match.
    dist = Levenshtein.distance(a, b)
    longer = max(len(a), len(b))
    if longer == 0:
        return 0.0
    similarity = max(0.0, 1.0 - dist / longer)
    return min(0.6, similarity)


def phonetic_match_score(observed: str, expected: str) -> float:
    """Phonetic similarity in `[0, 1]` — the emission score the tracker uses.

    Delegates to `index.phonemic`, which compares actual pronunciations where
    the dictionary has both words and falls back to `metaphone_match_score`
    where it does not. Imported lazily to keep this module free of the
    dictionary for callers that only want metaphone codes.
    """
    from auto_presenter.index.phonemic import phonemic_match_score

    return phonemic_match_score(observed, expected)
