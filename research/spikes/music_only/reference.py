"""What the sheet says should be sounding at a played position.

A dense lookup on a fine grid of the unrolled performance: the sung note (or
a rest) and the chord in force. Chords are reduced to what BTC can emit —
root plus major or minor — because the chart's sevenths, sus chords and slash
basses are not in its vocabulary (the chord audit found 20% of chart chords
could never match until this was done).
"""

from __future__ import annotations

import re

import numpy as np

import score as sc

PCS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
GRID = 1 / 16  # quarter notes


def chord_class(name: str) -> int | None:
    """Index into BTC's 25 labels (C, Cm, C#, C#m, ..., N), or None."""
    m = re.match(r"([A-G])([#b]?)(.*)", name)
    if not m:
        return None
    pc = (PCS[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
    rest = m.group(3).split("/")[0]
    minor = rest.startswith("m") and not rest.startswith("maj")
    return pc * 2 + (1 if minor else 0)


class Reference:
    def __init__(self, s: sc.Score):
        self.s = s
        n = int(np.ceil(s.length / GRID)) + 1
        self.midi = np.full(n, -1, np.int16)  # -1: rest
        self.onset = np.zeros(n, bool)  # a sung note starts in this cell
        self.chord = np.full(n, -1, np.int16)
        cur_chord = -1
        for pb in s.played:
            bar = s.bar(pb.bar)
            chords = sorted(bar["chords"], key=lambda c: c["beat"])
            # chord in force through this bar, carried over from earlier bars
            marks = [(pb.start + c["beat"], chord_class(c["name"])) for c in chords]
            bl = sc._bar_len(bar, s.meter)
            i0, i1 = int(round(pb.start / GRID)), int(round((pb.start + bl) / GRID))
            for i in range(i0, min(i1, n)):
                p = i * GRID
                for t, c in marks:
                    if t <= p + 1e-9 and c is not None:
                        cur_chord = c
                self.chord[i] = cur_chord
            for note in sc.line(bar, pb.row):
                if note["midi"] is None:
                    continue
                a = int(round((pb.start + note["onset"]) / GRID))
                b = int(round((pb.start + note["onset"] + note["dur"]) / GRID))
                self.midi[a:b] = note["midi"]
                if not note["tied_from_prev"]:
                    self.onset[a] = True

    def at(self, pos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(midi or -1, chord class or -1) at each played position; NaN -> -1."""
        idx = np.round(np.nan_to_num(pos, nan=-1) / GRID).astype(int)
        ok = (idx >= 0) & (idx < len(self.midi))
        midi = np.where(ok, self.midi[np.clip(idx, 0, len(self.midi) - 1)], -1)
        chord = np.where(ok, self.chord[np.clip(idx, 0, len(self.chord) - 1)], -1)
        return midi, chord


def pc_salience(contour: np.ndarray) -> np.ndarray:
    """Basic Pitch contour (frames x 264) -> (frames x 12) pitch-class salience.

    Each semitone takes the max of its three bins; each pitch class the max
    over octaves. Octave-folded because the singer here is an octave below
    the written melody, and a matcher should not care which octave it is."""
    c = contour.astype(np.float32)
    T = len(c)
    n_semi = (c.shape[1] - 1) // 3
    semi = np.zeros((T, n_semi), np.float32)
    for k in range(n_semi):
        b = 1 + 3 * k
        semi[:, k] = c[:, max(0, b - 1) : b + 2].max(axis=1)
    out = np.zeros((T, 12), np.float32)
    for k in range(n_semi):
        pc = (21 + k) % 12
        np.maximum(out[:, pc], semi[:, k], out=out[:, pc])
    return out


def semitone_salience(contour: np.ndarray) -> np.ndarray:
    """(frames x 88) per semitone from MIDI 21, unfolded."""
    c = contour.astype(np.float32)
    n_semi = (c.shape[1] - 1) // 3
    semi = np.zeros((len(c), n_semi), np.float32)
    for k in range(n_semi):
        b = 1 + 3 * k
        semi[:, k] = c[:, max(0, b - 1) : b + 2].max(axis=1)
    return semi
