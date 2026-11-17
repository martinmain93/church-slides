"""Phoneme-sequence matching: a pronunciation dictionary plus a graded distance.

Spike, 2026-09-01. Not wired in — `cmudict` is not yet a sidecar dependency.
Measured on the six approved goldens, swapping this in for the scorer the
forward filter calls:

    stream          scorer      wrong-slide   anticipation
    whisper only    metaphone       0.484        +588 ms (late)
    whisper only    phonemic        0.472        +615 ms
    both streams    metaphone       0.571        +176 ms
    both streams    phonemic        0.529        -220 ms (early)

It helps Whisper alone slightly and the combined stream considerably, which is
the expected shape: the fast channel's errors are near-miss *real words*
("says" for "chases", "and" for "found"), and those are exactly what a graded
phoneme distance recovers and an exact code match throws away.

Two problems with the shipped `phonetic_match_score`, both measured:

  It reads spelling, not sound. Double Metaphone applies English spelling rules
  and never consults a pronunciation. It gets "knight" right by luck of its rule
  set, not because it knows the k is silent.

  It is all-or-nothing. Codes either match exactly or the score collapses to
  letter-level Levenshtein capped at 0.6. Every inserted sound fell through that
  way, and insertions are the dominant ASR error here.

This replaces both: CMUdict gives the actual phoneme sequence, and an edit
distance over phonemes handles insertions and deletions by construction. The
substitution cost is graded by articulation, so a sound confused for a similar
one costs little and a sound confused for an unrelated one costs full price.
"""
import functools
import re

import sys
from pathlib import Path

import cmudict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sidecar"))

from auto_presenter.index.phonetic import phonetic_match_score  # noqa: E402

_DICT = cmudict.dict()

# Articulatory classes. Two phonemes differing only in voicing are the classic
# ASR confusion (p/b, t/d, k/g, f/v, s/z, ch/jh, th/dh) and should be cheap.
_VOICING = {
    ("P", "B"), ("T", "D"), ("K", "G"), ("F", "V"), ("S", "Z"),
    ("CH", "JH"), ("TH", "DH"), ("SH", "ZH"),
}
_MANNER = {
    **{p: "stop" for p in ("P", "B", "T", "D", "K", "G")},
    **{p: "fric" for p in ("F", "V", "S", "Z", "TH", "DH", "SH", "ZH", "HH")},
    **{p: "affr" for p in ("CH", "JH")},
    **{p: "nasal" for p in ("M", "N", "NG")},
    **{p: "liquid" for p in ("L", "R")},
    **{p: "glide" for p in ("W", "Y")},
}
_PLACE = {
    **{p: "labial" for p in ("P", "B", "M", "F", "V", "W")},
    **{p: "alveolar" for p in ("T", "D", "N", "S", "Z", "L", "R")},
    **{p: "postalv" for p in ("SH", "ZH", "CH", "JH", "Y")},
    **{p: "velar" for p in ("K", "G", "NG")},
    **{p: "dental" for p in ("TH", "DH")},
    **{p: "glottal" for p in ("HH",)},
}


def _is_vowel(p: str) -> bool:
    return p[0] in "AEIOU"


@functools.lru_cache(maxsize=100_000)
def substitution_cost(a: str, b: str) -> float:
    """0 = same sound, 1 = unrelated. Graded by how the sounds are made."""
    if a == b:
        return 0.0
    av, bv = _is_vowel(a), _is_vowel(b)
    if av and bv:
        # Vowels are the least reliable part of sung ASR — the 2026-08-21 spike
        # found them "wrong throughout" — so confusing one for another is cheap.
        return 0.3
    if av != bv:
        return 1.0
    if (a, b) in _VOICING or (b, a) in _VOICING:
        return 0.2
    same_manner = _MANNER.get(a) == _MANNER.get(b) and a in _MANNER
    same_place = _PLACE.get(a) == _PLACE.get(b) and a in _PLACE
    if same_manner and same_place:
        return 0.3
    if same_manner or same_place:
        return 0.6
    return 1.0


def _indel_cost(p: str) -> float:
    """Dropping or adding a vowel is common and cheap; a consonant is not."""
    return 0.4 if _is_vowel(p) else 0.8


@functools.lru_cache(maxsize=200_000)
def in_dictionary(word: str) -> bool:
    w = re.sub(r"[^a-z']", "", word.lower())
    if not w:
        return False
    if w in _DICT:
        return True
    return any(w.endswith(sfx) and w[: -len(sfx)] in _DICT
               for sfx in ("'s", "s", "'"))


@functools.lru_cache(maxsize=200_000)
def phonemes(word: str) -> tuple[str, ...]:
    """Phoneme sequence, stress markers stripped. Falls back for OOV words."""
    w = re.sub(r"[^a-z']", "", word.lower())
    if not w:
        return ()
    entry = _DICT.get(w)
    if entry:
        return tuple(re.sub(r"\d", "", p) for p in entry[0])
    # Out of vocabulary — ASR fragments, contractions, British spellings. Strip
    # a plural or possessive and retry before giving up on the dictionary.
    for suffix in ("'s", "s", "'"):
        if w.endswith(suffix) and len(w) > len(suffix) + 1:
            stem = _DICT.get(w[: -len(suffix)])
            if stem:
                return tuple(re.sub(r"\d", "", p) for p in stem[0])
    return _spell_out(w)


def _spell_out(w: str) -> tuple[str, ...]:
    """Crude grapheme-to-phoneme for words the dictionary does not have.

    Deliberately simple: OOV words are 1% of lyrics and 4% of fast-channel
    output, and most are ASR nonsense that should not match anything strongly.
    Being roughly right is enough; being wrong here costs little.
    """
    pairs = {"ch": "CH", "sh": "SH", "th": "TH", "ph": "F", "ck": "K",
             "ng": "NG", "qu": "K", "wh": "W"}
    out, i = [], 0
    single = {"a": "AE", "e": "EH", "i": "IH", "o": "AO", "u": "AH",
              "y": "IY", "c": "K", "j": "JH", "x": "K", "'": None}
    while i < len(w):
        if i + 1 < len(w) and w[i:i + 2] in pairs:
            out.append(pairs[w[i:i + 2]])
            i += 2
            continue
        ch = w[i]
        mapped = single.get(ch, ch.upper())
        if mapped:
            out.append(mapped)
        i += 1
    return tuple(out)


@functools.lru_cache(maxsize=200_000)
def phonemic_match_score(observed: str, expected: str) -> float:
    """Similarity in `[0, 1]`, robust to inserted and dropped sounds.

    A weighted edit distance over phoneme sequences, normalised by the cost of
    deleting the expected word outright — so the score is "how much of the
    expected pronunciation survived", and an insertion costs only what the
    inserted sound is worth rather than voiding the match.
    """
    # Only trust phonemes when we actually have them. For a word the dictionary
    # does not know — a misspelling or an ASR fragment — all we have IS the
    # spelling, and Double Metaphone is built for exactly that. Measured:
    # grapheme-guessing "graace" scored 0.25 against "grace" where metaphone
    # scored 0.90, because the guess produces a nonsense pronunciation.
    if not (in_dictionary(observed) and in_dictionary(expected)):
        return phonetic_match_score(observed, expected)

    a, b = phonemes(observed), phonemes(expected)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    prev = [0.0]
    for j, pb in enumerate(b):
        prev.append(prev[j] + _indel_cost(pb))
    for pa in a:
        cur = [prev[0] + _indel_cost(pa)]
        for j, pb in enumerate(b):
            cur.append(min(
                prev[j] + substitution_cost(pa, pb),
                prev[j + 1] + _indel_cost(pa),
                cur[j] + _indel_cost(pb),
            ))
        prev = cur
    worst = sum(_indel_cost(p) for p in b)
    return max(0.0, 1.0 - prev[-1] / worst) if worst else 0.0
