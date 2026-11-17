"""Who moved the slides, cleaned, and how much a performance counts.

An operator's clicks are almost, not quite, ground truth. Measured in the
recordings store (2026-10-07): 91% of recorded moves stayed up under 1.5 s —
stepping through slides to reach one, not the band moving. And an AI move
the operator undid within a few seconds was a mistake, not a place the band
went.
"""

from __future__ import annotations

from difflib import SequenceMatcher

#: A slide up for less than this was passed through on the way somewhere.
SCRUB_S = 1.5
#: An AI move the operator replaced within this long was a mistake.
CORRECTION_S = 4.0
#: What the look-back's confidence is worth, calibrated against the goldens
#: (research/eval/calibrate_lookback.py, 2026-10-08). Held out by recording, its raw
#: posterior was better calibrated than any map fitted to it (Brier 0.137 on
#: live words vs 0.159-0.172 for Platt and binned fits; on whole-file Whisper
#: all within 0.104-0.110), so it is used as P(right) directly.
#:
#: A pass counts at all only at this confidence (live words: keeps 70% of
#: passes, 88.6% of them right, against 76% right unfiltered).
CONFIDENT = 0.7
#: A line change A -> B counts only if P(both right) ~ conf(A) x conf(B)
#: reaches this, and then with that product as its weight. Live words: 64% of
#: changes kept, 84.1% right by weight, against 82.9% for the old rule (both
#: passes >= 0.8, equal weight); whole-file Whisper 91.2% vs 91.0%.
MIN_CHANGE_CONFIDENCE = 0.7
OPERATOR_SOURCES = ("manual", "override", "voice")
#: The slide a session opened on: the operator's set-up, so it counts as what
#: the operator showed, but nobody moved to it.
START = "start"

#: Performance weights (docs/done/LEARNED_REFERENCES.md, "Label sources and trust").
W_OPERATOR = 1.0
W_OPERATOR_DISAGREES = 0.3
W_MIXED = 0.6
W_AI = 0.3
#: Below this agreement an operator-led performance counts less: the audio
#: and the clicks tell different stories.
AGREES = 0.6
#: An AI-led performance counts only if the look-back is this sure on average.
AI_MIN_CONFIDENCE = 0.7


def clean_moves(moves: list[tuple[float, int, str]]) -> list[tuple[float, int, str]]:
    """(t, slide, source) moves within one song, scrubs and corrected AI
    mistakes removed."""
    out: list[tuple[float, int, str]] = []
    for i, (t, slide, source) in enumerate(moves):
        nxt = moves[i + 1] if i + 1 < len(moves) else None
        if nxt is not None and nxt[0] - t < SCRUB_S:
            continue  # passed through
        if (
            source == "ai"
            and nxt is not None
            and nxt[2] in OPERATOR_SOURCES
            and nxt[0] - t < CORRECTION_S
        ):
            continue  # corrected
        if out and out[-1][1] == slide:
            continue
        out.append((t, slide, source))
    return out


def led_by(moves: list[tuple[float, int, str]]) -> str:
    moves = [m for m in moves if m[2] != START]
    if not moves:
        return "none"
    op = sum(1 for _t, _s, src in moves if src in OPERATOR_SOURCES)
    share = op / len(moves)
    return "operator" if share >= 0.7 else "ai" if share <= 0.3 else "mixed"


def confident_sequence(passes, canon=lambda s: s) -> list[int]:
    """Slides of the confident passes, repeats of one line collapsed (an
    operator cannot show a line twice in a row)."""
    out: list[int] = []
    for p in passes:
        if p.confidence < CONFIDENT:
            continue
        s = canon(p.slide)
        if not out or out[-1] != s:
            out.append(s)
    return out


def agreement(passes, moves: list[tuple[float, int, str]], canon=lambda s: s) -> float | None:
    """Share of the look-back's confident line changes the operator made too."""
    if not any(src in OPERATOR_SOURCES for _t, _s, src in moves):
        return None  # nobody moved a slide: no evidence either way
    ops = [canon(s) for _t, s, src in moves if src in (*OPERATOR_SOURCES, START)]
    lb = confident_sequence(passes, canon)
    if not lb:
        return 0.0
    sm = SequenceMatcher(None, lb, ops, autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks()) / len(lb)


def performance_weight(provenance: str, led: str, agree: float | None, mean_conf: float) -> float:
    if provenance != "live":
        return 0.0
    if led == "operator":
        return W_OPERATOR if (agree is not None and agree >= AGREES) else W_OPERATOR_DISAGREES
    if led == "mixed":
        return W_MIXED
    return W_AI if mean_conf >= AI_MIN_CONFIDENCE else 0.0
