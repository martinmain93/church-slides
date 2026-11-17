"""Phoneme-sequence matching: a pronunciation dictionary plus a graded distance.

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
import gzip
import re
import threading
from collections.abc import Iterable
from pathlib import Path

from auto_presenter.index.phonetic import metaphone, metaphone_match_score

_loaded: dict | None = None
_load_lock = threading.Lock()


def _dictionary() -> dict:
    """Loaded on first use, not at import: plenty of processes import this
    module without ever scoring a word — tests, the codegen, CLI tools — and
    they should not pay for it.

    A session loads it when it starts (`warm`), not on the first sung word:
    first use used to be the matcher's first update, which took 1.9 s on a
    Mac in Low Power Mode (about 0.3 s otherwise) and held up the service's
    first line. One load, behind a lock, so a caller arriving mid-load waits
    for it rather than starting a second.
    """
    global _loaded
    if _loaded is None:
        with _load_lock:
            if _loaded is None:
                _loaded = load_cmudict()
    return _loaded


def warm(words: Iterable[str]) -> None:
    """Load the dictionary and look up each of `words` now, off the sung-word
    path. Call with a setlist's lyrics when a session starts."""
    _dictionary()
    for w in set(words):
        in_dictionary(w)
        phonemes(w)
        metaphone(w)


#: The CMU Pronouncing Dictionary's data (BSD-style licence beside it). Only
#: the data is bundled: the `cmudict` package that wraps it is GPL-3.
CMUDICT = Path(__file__).resolve().parent / "data" / "cmudict.dict.gz"


def load_cmudict(path: Path = CMUDICT) -> dict[str, list[list[str]]]:
    """word -> its pronunciations (lists of ARPAbet phonemes). One entry per
    line, `word PH1 PH2 ...`; variants are `word(2)`; `#` starts a comment."""
    out: dict[str, list[list[str]]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            parts = line.split("#", 1)[0].split()
            if len(parts) < 2:
                continue
            word = re.sub(r"\(\d+\)$", "", parts[0])
            out.setdefault(word, []).append(parts[1:])
    return out


# Articulatory classes. Two phonemes differing only in voicing are the classic
# ASR confusion (p/b, t/d, k/g, f/v, s/z, ch/jh, th/dh) and should be cheap.
_VOICING = {
    ("P", "B"),
    ("T", "D"),
    ("K", "G"),
    ("F", "V"),
    ("S", "Z"),
    ("CH", "JH"),
    ("TH", "DH"),
    ("SH", "ZH"),
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


#: Below this, a pair is treated as unrelated and scores zero. Without it,
#: "grace" earns 0.14 against "intro" on the strength of a shared R, and every
#: slide looks faintly plausible, which flattens a posterior the tracker needs
#: peaked.
#:
#: Swept against both the production forward filter and the legacy particle
#: filter's integration tests:
#:
#:     0.10   wrong-slide 0.439   a lost episode ends on the wrong slide
#:     0.15   wrong-slide 0.443   all tests pass
#:     0.20   wrong-slide 0.447   a lost episode ends on the wrong slide
#:     0.25   wrong-slide 0.454   a lost episode ends on the wrong slide
#:
#: Cutting too aggressively removes the weak evidence that separates slides
#: sharing an opening line — "all to thee my blessed saviour" against "all to
#: jesus i surrender now" — which is exactly where a hair of signal decides it.
UNRELATED_BELOW = 0.15


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
    d = _dictionary()
    if w in d:
        return True
    return any(w.endswith(sfx) and w[: -len(sfx)] in d for sfx in ("'s", "s", "'"))


@functools.lru_cache(maxsize=200_000)
def phonemes(word: str) -> tuple[str, ...]:
    """Phoneme sequence, stress markers stripped. Falls back for OOV words."""
    w = re.sub(r"[^a-z']", "", word.lower())
    if not w:
        return ()
    entry = _dictionary().get(w)
    if entry:
        return tuple(re.sub(r"\d", "", p) for p in entry[0])
    # Out of vocabulary — ASR fragments, contractions, British spellings. Strip
    # a plural or possessive and retry before giving up on the dictionary.
    for suffix in ("'s", "s", "'"):
        if w.endswith(suffix) and len(w) > len(suffix) + 1:
            stem = _dictionary().get(w[: -len(suffix)])
            if stem:
                return tuple(re.sub(r"\d", "", p) for p in stem[0])
    return _spell_out(w)


def _spell_out(w: str) -> tuple[str, ...]:
    """Crude grapheme-to-phoneme for words the dictionary does not have.

    Deliberately simple: OOV words are 1% of lyrics and 4% of fast-channel
    output, and most are ASR nonsense that should not match anything strongly.
    Being roughly right is enough; being wrong here costs little.
    """
    pairs = {
        "ch": "CH",
        "sh": "SH",
        "th": "TH",
        "ph": "F",
        "ck": "K",
        "ng": "NG",
        "qu": "K",
        "wh": "W",
    }
    out, i = [], 0
    single = {
        "a": "AE",
        "e": "EH",
        "i": "IH",
        "o": "AO",
        "u": "AH",
        "y": "IY",
        "c": "K",
        "j": "JH",
        "x": "K",
        "'": None,
    }
    while i < len(w):
        if i + 1 < len(w) and w[i : i + 2] in pairs:
            out.append(pairs[w[i : i + 2]])
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
    deleting the *longer* pronunciation outright. Normalising by the expected
    word alone was tried first and was wrong twice over: it made the score
    asymmetric, breaking a contract the emission kernel relies on, and it handed
    partial credit to unrelated words — "grace" against "amazing" scored 0.06
    one way and 0.00 the other. Against the longer side, an unrelated word costs
    more to transform than to delete outright and the score floors at zero,
    which is what "this word is no evidence" has to mean.
    """
    # Only trust phonemes when we actually have them. For a word the dictionary
    # does not know — a misspelling or an ASR fragment — all we have IS the
    # spelling, and Double Metaphone is built for exactly that. Measured:
    # grapheme-guessing "graace" scored 0.25 against "grace" where metaphone
    # scored 0.90, because the guess produces a nonsense pronunciation.
    if not (in_dictionary(observed) and in_dictionary(expected)):
        return metaphone_match_score(observed, expected)

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
            cur.append(
                min(
                    prev[j] + substitution_cost(pa, pb),
                    prev[j + 1] + _indel_cost(pa),
                    cur[j] + _indel_cost(pb),
                )
            )
        prev = cur
    worst = max(sum(_indel_cost(p) for p in a), sum(_indel_cost(p) for p in b))
    if not worst:
        return 0.0
    score = max(0.0, 1.0 - prev[-1] / worst)
    # Words sharing an incidental sound or two should be *no evidence*, not
    # faint evidence. Without this, "grace" scores 0.14 against "intro" on the
    # strength of a shared R, and every slide looks slightly plausible — which
    # flattens the posterior the tracker relies on being peaked.
    return score if score >= UNRELATED_BELOW else 0.0
