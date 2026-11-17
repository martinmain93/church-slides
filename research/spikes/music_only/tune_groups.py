"""Which slides of a song are sung to the same tune? Two oracles from its lead sheet.

* `bars`: slides bound to the same printed bars (a hymn's verses; copies of a chorus).
* `melody`: slides whose printed melodies agree: each slide's sung notes resampled to
  32 points, mean |difference| <= MAX_SEMITONES and lengths within LEN_RATIO
  (a modern song's verse 2 printed under its own notes, the same tune as verse 1).

These stand in for what an in-service look-back could learn: the ceiling of
"hear one slide of a tune, know all its slides" (tune_share_replay.py).
"""
from __future__ import annotations

import collections

import numpy as np

from auto_presenter.follower.reference import sheet_spec

MAX_SEMITONES = 1.0
LEN_RATIO = 1.35
POINTS = 32


def _contour(seg) -> np.ndarray | None:
    m = np.asarray(seg.midi)
    m = m[m >= 0]
    if len(m) < 4:
        return None
    return np.interp(np.linspace(0, len(m) - 1, POINTS), np.arange(len(m)), m.astype(float))


def tune_groups(score, slides, sections, how: str = "melody") -> list[set[int]]:
    spec = sheet_spec(score, slides, sections)
    if how == "bars":
        groups = collections.defaultdict(set)
        for si, sps in (spec.spans or {}).items():
            key = tuple((round(p.start, 1), round(p.end, 1)) for p in sps) if sps else ("solo", si)
            groups[key].add(si)
        return [g for g in groups.values()]
    segs = {g.slide: g for g in spec.segments}
    cont = {s: _contour(g) for s, g in segs.items()}
    parent = {s: s for s in segs}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    ss = sorted(segs)
    for i, a in enumerate(ss):
        for b in ss[i + 1:]:
            ca, cb = cont[a], cont[b]
            if ca is None or cb is None:
                continue
            ra = segs[a].cells / max(1, segs[b].cells)
            if 1 / LEN_RATIO <= ra <= LEN_RATIO and np.mean(np.abs(ca - cb)) <= MAX_SEMITONES:
                parent[find(a)] = find(b)
    groups = collections.defaultdict(set)
    for s in ss:
        groups[find(s)].add(s)
    return list(groups.values())


def syllables(word: str) -> int:
    """A rough English syllable count: vowel groups, a silent final e dropped."""
    import re

    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def metre_groups(slides, syl_tol: int = 1, word_ratio: float = 1.25, chain: bool = True) -> list[set[int]]:
    """Slides likely sung to one tune, from their text alone: the same number of
    syllables (within `syl_tol`) and of words (within `word_ratio`). What a live
    session can know before a slide is sung; hymn verses share a metre."""
    from auto_presenter.follower import score as sc

    info = {}
    for si, sw in enumerate(slides):
        ws = [w for w in (sc.norm(x) for x in sw) if w]
        if ws:
            info[si] = (sum(syllables(w) for w in ws), len(ws), " ".join(ws))
    parent = {s: s for s in info}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    ss = sorted(info)

    def match(a, b):
        (sa, wa, ta), (sb, wb, tb) = info[a], info[b]
        return ta == tb or (abs(sa - sb) <= syl_tol and max(wa, wb) / max(1, min(wa, wb)) <= word_ratio)

    if not chain:
        # each slide with the slides that match it directly (no chains)
        return [{a, *(b for b in ss if b != a and match(a, b))} for a in ss]
    for i, a in enumerate(ss):
        for b in ss[i + 1:]:
            if match(a, b):
                parent[find(a)] = find(b)
    out = collections.defaultdict(set)
    for s_ in ss:
        out[find(s_)].add(s_)
    return list(out.values())
