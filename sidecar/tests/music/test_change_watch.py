"""Key and tempo changes as a song-change hint (`music.change_watch`)."""

from __future__ import annotations

import numpy as np

from auto_presenter.music.btc import MAJMIN_LABELS
from auto_presenter.music.change_watch import KeyTempoWatch, same_tempo


def chord(name: str) -> np.ndarray:
    p = np.full(len(MAJMIN_LABELS), 0.01, np.float32)
    p[MAJMIN_LABELS.index(name)] = 1.0
    return p


def play(watch: KeyTempoWatch, progression: list[str], seconds: float, t0: float, bpm: float):
    """Ten analyser ticks a second, a chord every 2 s."""
    changes = []
    for i in range(int(seconds * 10)):
        t = t0 + i / 10
        changes += watch.update(t, chord(progression[int(t / 2) % len(progression)]), bpm)
    return changes


def test_a_new_key_that_holds_is_a_change():
    w = KeyTempoWatch()
    assert play(w, ["G", "C", "D", "Em"], 90, 0.0, 72) == []
    changes = play(w, ["A", "D", "E", "F#m"], 90, 90.0, 72)
    assert [(c.kind, c.before, c.after) for c in changes] == [("key", "G", "A")]


def test_the_same_key_and_its_relative_minor_are_not_changes():
    w = KeyTempoWatch()
    play(w, ["G", "C", "D", "Em"], 90, 0.0, 72)
    assert play(w, ["Em", "C", "G", "D"], 90, 90.0, 72) == []  # E minor = G major's chords


def test_tempo_changes_count_but_octave_misreadings_do_not():
    w = KeyTempoWatch()
    play(w, ["G", "C", "D"], 40, 0.0, 120)
    assert play(w, ["G", "C", "D"], 40, 40.0, 60) == []  # half the pulse: same tempo
    changes = play(w, ["G", "C", "D"], 40, 80.0, 84)
    assert [c.kind for c in changes] == ["tempo"]
    assert same_tempo(120, 61) and same_tempo(70, 140) and not same_tempo(120, 84)


def test_a_brief_detour_does_not_count():
    w = KeyTempoWatch()
    play(w, ["G", "C", "D", "Em"], 90, 0.0, 72)
    assert play(w, ["F", "A#", "C"], 8, 90.0, 72) == []  # a passing modulation
    assert play(w, ["G", "C", "D", "Em"], 60, 98.0, 72) == []
