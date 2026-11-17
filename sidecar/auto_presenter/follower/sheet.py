"""What the lead sheet says is sounding at each cell, and which key the band is in.

Ported from the music-only spike (`reference.py`, `follow.build`,
`follow.estimate_key_shift`). The follower's grid is an eighth note
(`CELL`); each cell carries the sung note (MIDI, or -1 for a rest) and the
chord in force, reduced to what BTC can hear: root plus major or minor (25
classes in BTC's order: C, Cm, C#, C#m, ..., B, Bm, N).

The tables here are in the *sheet's* key. Bands rarely play a lead sheet in
its printed key (3 of 5 recordings in the spike did not), so the key is
estimated from the chords actually heard and applied when evidence is
scored (`shift_chords`). It must be estimated against these *unshifted*
tables — estimating against an already-shifted model was a spike bug that
collapsed one song.
"""

from __future__ import annotations

import re

import numpy as np

from auto_presenter.follower import score as sc

#: Quarter notes per state cell: an eighth note.
CELL = 0.5
#: The note/chord lookup's resolution, quarter notes.
GRID = 1 / 16

_PCS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
#: BTC's no-chord class.
NO_CHORD = 24


def chord_class(name: str) -> int | None:
    """Index into BTC's 25 labels (C, Cm, C#, C#m, ..., N), or None."""
    m = re.match(r"([A-G])([#b]?)(.*)", name)
    if not m:
        return None
    pc = (_PCS[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
    rest = m.group(3).split("/")[0]
    minor = rest.startswith("m") and not rest.startswith("maj")
    return pc * 2 + (1 if minor else 0)


def cell_tables(s: sc.Score) -> tuple[np.ndarray, np.ndarray]:
    """(midi or -1, chord class or -1) at the centre of every cell of the unrolled sheet."""
    n = int(np.ceil(s.length / GRID)) + 1
    midi = np.full(n, -1, np.int16)
    chord = np.full(n, -1, np.int16)
    cur_chord = -1
    for pb in s.played:
        bar = s.bar(pb.bar)
        marks = [
            (pb.start + c["beat"], chord_class(c["name"]))
            for c in sorted(bar["chords"], key=lambda c: c["beat"])
        ]
        bl = sc._bar_len(bar, s.meter)
        i0, i1 = round(pb.start / GRID), round((pb.start + bl) / GRID)
        for i in range(i0, min(i1, n)):
            p = i * GRID
            for t, c in marks:
                if t <= p + 1e-9 and c is not None:
                    cur_chord = c
            chord[i] = cur_chord
        for note in sc.line(bar, pb.row):
            if note["midi"] is None:
                continue
            a = round((pb.start + note["onset"]) / GRID)
            b = round((pb.start + note["onset"] + note["dur"]) / GRID)
            midi[a:b] = note["midi"]
    n_cells = int(np.floor(s.length / CELL))
    pos = (np.arange(n_cells) + 0.5) * CELL
    idx = np.clip(np.round(pos / GRID).astype(int), 0, n - 1)
    return midi[idx].astype(np.int16), chord[idx].astype(np.int16)


def shift_chords(chord: np.ndarray, key_shift: int) -> np.ndarray:
    """Chord classes moved by `key_shift` semitones (roots move, quality stays)."""
    if not key_shift:
        return chord
    return np.where(
        (chord >= 0) & (chord < 24), ((chord // 2 + key_shift) % 12) * 2 + chord % 2, chord
    ).astype(np.int16)


def shift_midi(midi: np.ndarray, key_shift: int) -> np.ndarray:
    return np.where(midi >= 0, midi + key_shift, -1).astype(np.int16) if key_shift else midi


def chord_histogram(chords: np.ndarray) -> np.ndarray:
    """12 x 2 (root x major/minor) time share from frames of chord probabilities."""
    h = chords[:, :24].sum(axis=0)
    return (h / max(h.sum(), 1e-9)).reshape(12, 2)


def sheet_histogram(chord: np.ndarray) -> np.ndarray:
    h = np.zeros(24)
    for c in chord:
        if 0 <= c < 24:
            h[c] += 1
    return (h / max(h.sum(), 1)).reshape(12, 2)


def estimate_key_shift(heard: np.ndarray, sheet_chord: np.ndarray) -> tuple[int, np.ndarray]:
    """Semitones to move the sheet into the band's key: the shift whose
    chord-quality histogram best correlates with what BTC hears. Returns the
    shift and the correlation for every shift. `sheet_chord` must be the
    unshifted table."""
    a = chord_histogram(heard)
    b = sheet_histogram(sheet_chord)
    corr = np.array(
        [np.corrcoef(a.ravel(), np.roll(b, k, axis=0).ravel())[0, 1] for k in range(12)]
    )
    corr = np.nan_to_num(corr, nan=-1.0)
    return int(np.argmax(corr)), corr
