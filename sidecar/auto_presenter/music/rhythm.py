"""Where each band's onsets sit on the beat grid.

The inspector draws this. A later fingerprint can read the same bars: one
pattern per band per bar, plus how far each onset sat from its slot. Nothing
here is a second tempo estimate. The beats and downbeats are taken as given.

An onset is close to a slot when it is within a fifth of that slot's spacing.
Quarters and eighths are worth showing from a single hit. Sixteenths and
triplets are shown only when the same pattern holds for two bars in a row.
"""

from __future__ import annotations

from dataclasses import dataclass

# Fraction of the distance between neighbouring slots. A fifth of the way
# toward the next slot is as far as an onset can wander and still belong.
SLOT_TOLERANCE = 0.2

# Coarsest first. A bar of plain beats is quarters, even though those same
# hits also land on the eighth and sixteenth grids.
PATTERNS: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("quarter", (0.0,)),
    ("eighth", (0.0, 0.5)),
    ("sixteenth", (0.0, 0.25, 0.5, 0.75)),
    ("triplet", (0.0, 1.0 / 3.0, 2.0 / 3.0)),
)
FINE = {"sixteenth", "triplet"}
SPACING = {
    "quarter": 1.0,
    "eighth": 0.5,
    "sixteenth": 0.25,
    "triplet": 1.0 / 3.0,
}


@dataclass(frozen=True)
class RhythmEvent:
    band: str
    t: float
    phase: float
    kind: str
    error_ms: float
    bar_index: int
    show: bool


@dataclass(frozen=True)
class BarPattern:
    band: str
    bar_index: int
    t_start: float
    t_end: float
    pattern: str
    stable: bool
    onset_count: int
    on_grid: int


def _phase_error(phase: float, slots: tuple[float, ...]) -> float:
    best = min(abs(phase - s) for s in slots)
    # The next beat is slot 0 of the next interval, at phase 1.
    if any(s == 0.0 for s in slots):
        best = min(best, abs(phase - 1.0))
    return best


def _hits(phase: float, name: str, slots: tuple[float, ...]) -> bool:
    return _phase_error(phase, slots) <= SLOT_TOLERANCE * SPACING[name]


def _bar_edges(beats: list[float], downbeats: list[float]) -> list[float]:
    if len(downbeats) >= 2:
        edges = [t for t in downbeats]
        if beats and beats[0] < edges[0] - 1e-3:
            edges.insert(0, beats[0])
        return edges
    if len(beats) < 5:
        return [beats[0]] if beats else []
    return [beats[i] for i in range(0, len(beats), 4)]


def _interval(beats: list[float], t: float) -> tuple[int, float, float] | None:
    for i in range(len(beats) - 1):
        if beats[i] - 1e-4 <= t < beats[i + 1]:
            span = beats[i + 1] - beats[i]
            if span <= 1e-4:
                return None
            return i, (t - beats[i]) / span, span
    return None


def _classify(phases: list[float]) -> tuple[str, int]:
    if len(phases) < 2:
        return "sparse", 0
    locked = [
        p for p in phases if any(_hits(p, name, slots) for name, slots in PATTERNS)
    ]
    if len(locked) < 2:
        return "sparse", len(locked)
    covering = [
        name
        for name, slots in PATTERNS
        if all(_hits(p, name, slots) for p in locked)
    ]
    if covering:
        return covering[0], len(locked)
    best_name = "mixed"
    best_count = 0
    for name, slots in PATTERNS:
        count = sum(_hits(p, name, slots) for p in phases)
        if count > best_count:
            best_name = name
            best_count = count
    if best_count >= 2 and best_count / len(phases) >= 0.6:
        return best_name, best_count
    return "mixed", best_count


def describe_rhythm(
    beats: list[float],
    downbeats: list[float],
    onsets: dict[str, list[float]],
) -> tuple[list[RhythmEvent], list[BarPattern]]:
    """Patterns per band, and one event per onset that fell inside a beat."""
    edges = _bar_edges(beats, downbeats)
    events: list[RhythmEvent] = []
    bars: list[BarPattern] = []
    if len(beats) < 2 or not edges:
        return events, bars

    slots_of = dict(PATTERNS)
    for band, times in onsets.items():
        placed: list[tuple[float, float, float, int]] = []
        for t in times:
            found = _interval(beats, t)
            if found is None:
                continue
            _beat_i, phase, span = found
            bar_i = 0
            for i, edge in enumerate(edges):
                if edge <= t + 1e-6:
                    bar_i = i
                else:
                    break
            placed.append((t, phase, span, bar_i))

        by_bar: dict[int, list[tuple[float, float, float]]] = {}
        for t, phase, span, bar_i in placed:
            by_bar.setdefault(bar_i, []).append((t, phase, span))

        decided: dict[int, tuple[str, int, int]] = {}
        for bar_i, group in by_bar.items():
            pattern, on_grid = _classify([p for _t, p, _s in group])
            decided[bar_i] = (pattern, len(group), on_grid)

        stable = {
            bar_i
            for bar_i, (pattern, _n, _g) in decided.items()
            if pattern in FINE
            and (
                decided.get(bar_i - 1, ("", 0, 0))[0] == pattern
                or decided.get(bar_i + 1, ("", 0, 0))[0] == pattern
            )
        }
        for bar_i, (pattern, count, on_grid) in sorted(decided.items()):
            t_start = edges[bar_i] if bar_i < len(edges) else beats[0]
            t_end = edges[bar_i + 1] if bar_i + 1 < len(edges) else beats[-1]
            bars.append(
                BarPattern(
                    band=band,
                    bar_index=bar_i,
                    t_start=round(float(t_start), 3),
                    t_end=round(float(t_end), 3),
                    pattern=pattern,
                    stable=pattern not in FINE or bar_i in stable,
                    onset_count=count,
                    on_grid=on_grid,
                )
            )

        for t, phase, span, bar_i in placed:
            coarsest = "off"
            err = 1.0
            for name, slots in PATTERNS:
                e = _phase_error(phase, slots)
                if e <= SLOT_TOLERANCE * SPACING[name]:
                    coarsest = name
                    err = e
                    break
            pattern = decided.get(bar_i, ("sparse", 0, 0))[0]
            if (
                pattern in FINE
                and bar_i in stable
                and _hits(phase, pattern, slots_of[pattern])
            ):
                kind = pattern
                err = _phase_error(phase, slots_of[pattern])
                show = True
            elif coarsest in ("quarter", "eighth"):
                kind = coarsest
                show = True
            else:
                kind = coarsest
                show = False
            events.append(
                RhythmEvent(
                    band=band,
                    t=round(t, 3),
                    phase=round(phase, 3),
                    kind=kind,
                    error_ms=round(err * span * 1000.0, 1),
                    bar_index=bar_i,
                    show=show,
                )
            )
    return events, bars
