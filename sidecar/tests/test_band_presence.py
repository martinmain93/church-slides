import numpy as np

from auto_presenter.music.band import BandPresence


def _probs(no_chord: float) -> np.ndarray:
    p = np.zeros(25)
    p[-1], p[0] = no_chord, 1.0 - no_chord
    return p


def test_unknown_until_evidence_then_follows_a_trailing_window():
    b = BandPresence(window_s=6.0, min_harmony=0.5)
    assert not b.playing and b.harmony is None
    b.observe(1.0, None)  # no learned recogniser: not evidence
    assert b.harmony is None
    for t in range(1, 7):
        b.observe(float(t), _probs(0.1))
    assert b.playing
    for t in range(7, 14):
        b.observe(float(t), _probs(0.95))  # talk: the old chords age out
    assert not b.playing
    b.reset()
    assert b.harmony is None
