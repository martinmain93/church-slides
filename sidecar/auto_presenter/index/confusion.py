"""Which slides start the same, and what word tells them apart.

Worship songs do this constantly and by design. Measured across the 311-song
library: **142 songs (46%) contain slides that share their first three words**,
and **530 of 2828 slides (19%) sit in such a group**. Groups are usually pairs
but reach eight members. They diverge at word 4 on median, with a tail out to
word 50.

"All to Jesus I surrender" opens five different verses of one hymn. Until the
sixth word arrives, no amount of matching can tell those verses apart, because
the audio genuinely does not contain the information. Every part of the tracker
that treats this as uncertainty is mis-modelling it: the words are not
ambiguous evidence, they are *shared* evidence, and the only thing that
resolves them is a word the group does not share.

So the resolution is precomputed rather than inferred. For each group we store
the **signatures**: phonetic codes that occur in exactly one member's text and
lie past the point where the members diverge. Hearing a signature is not weak evidence to be
accumulated — it is a proof of identity, and the tracker should act on it
immediately rather than waiting for a particle cloud to drift to the same
conclusion several seconds later.

Two deliberate choices:

- **Phonetic, not literal.** The discriminating word is the one most likely to
  be misheard, because it is the one carrying the information. Double Metaphone
  codes absorb the near-misses that would otherwise defeat the whole mechanism.
- **A set of signatures per member, not just the word at the divergence
  point.** Live ASR drops words. Keying on a single position would mean one
  missed word costs the entire branch, so any uniquely-owned word past the
  divergence is allowed to fire.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from auto_presenter.index.phonetic import metaphone, normalize

#: Slides must agree on this many leading codes to count as confusable. Two
#: shared words is a coincidence of grammar ("and the", "I will"); three is a
#: shared line, which is what actually strands a matcher.
PREFIX_LEN = 3

#: A word must be at least this long (after normalisation) to serve as a
#: signature. Short function words are exactly the ones ASR swaps around, and
#: a false branch is more expensive than a missed one.
MIN_SIGNATURE_LEN = 3


def code(word: str) -> str:
    """Phonetic code for one word, falling back to the normalised text."""
    primary, _secondary = metaphone(normalize(word))
    return primary or normalize(word)


@dataclass(frozen=True)
class ConfusionGroup:
    """Slides in one song that open with the same words.

    `signatures` maps a phonetic code to the slide index it uniquely
    identifies. A code appearing in two members is not a signature and is
    absent from the mapping — silence is the correct answer for a word that
    still cannot tell them apart.
    """

    item_idx: int
    #: Slide indices in this group, ascending.
    slide_idxs: tuple[int, ...]
    #: How many leading codes every member shares.
    prefix_len: int
    #: Index of the first word position where members disagree.
    divergence_idx: int
    signatures: dict[str, int] = field(default_factory=dict)

    def identify(self, word: str) -> int | None:
        """Which member does this word prove we are on, if any?"""
        return self.signatures.get(code(word))


def _signatures(
    members: dict[int, tuple[str, ...]], divergence_idx: int
) -> dict[str, int]:
    """Codes that occur in exactly one member's text, anywhere in it.

    Ownership is judged over each member's *whole* word list, not just the
    part past the divergence point, and getting that wrong is not a subtle
    error. "All to Jesus I surrender" opens five verses of one hymn; verse 4
    happens to say "Jesus" a second time further in. Scanning only the
    continuations made `JSS` look like verse 4's private property, so every
    time the band sang the shared opening line the resolver branched
    confidently to verse 4 — turning the mechanism meant to fix this song into
    the thing that broke it (wrong-slide 0.156 -> 0.394).

    A word that appears in the shared prefix cannot distinguish anything, and
    ownership over the full text is what encodes that.
    """
    owners: dict[str, set[int]] = defaultdict(set)
    for slide_idx, codes in members.items():
        for c in set(codes):
            if len(c) >= MIN_SIGNATURE_LEN:
                owners[c].add(slide_idx)
    # A signature must also lie past the point where the members diverge:
    # anything earlier is shared by construction.
    late: dict[int, set[str]] = {
        i: {c for c in codes[divergence_idx:]} for i, codes in members.items()
    }
    return {
        c: next(iter(o))
        for c, o in owners.items()
        if len(o) == 1 and c in late[next(iter(o))]
    }


def find_confusion_groups(
    slides: list[tuple[int, tuple[str, ...]]], item_idx: int
) -> list[ConfusionGroup]:
    """Group the slides of one song by shared opening words.

    `slides` is `(slide_idx, words)` pairs. Slides shorter than `PREFIX_LEN`
    are skipped — there is nothing to share. Groups whose members are textually
    identical are skipped too: those are genuine repeats of the same content,
    where picking the "wrong" one shows the audience exactly the right words.
    """
    coded = {
        slide_idx: tuple(code(w) for w in words)
        for slide_idx, words in slides
        if len(words) >= PREFIX_LEN
    }
    by_prefix: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for slide_idx, codes in coded.items():
        by_prefix[codes[:PREFIX_LEN]].append(slide_idx)

    groups: list[ConfusionGroup] = []
    for _prefix, idxs in sorted(by_prefix.items()):
        if len(idxs) < 2:
            continue
        members = {i: coded[i] for i in sorted(idxs)}
        if len({m for m in members.values()}) < 2:
            continue  # identical slides: not a confusion, just a repeat

        shortest = min(len(m) for m in members.values())
        divergence = shortest
        for k in range(shortest):
            if len({m[k] for m in members.values()}) > 1:
                divergence = k
                break

        sigs = _signatures(members, divergence)
        groups.append(
            ConfusionGroup(
                item_idx=item_idx,
                slide_idxs=tuple(sorted(idxs)),
                prefix_len=PREFIX_LEN,
                divergence_idx=divergence,
                signatures=sigs,
            )
        )
    return groups
