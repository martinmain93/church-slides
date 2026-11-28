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


def test_sustained_needs_the_band_held_over_the_longer_window():
    # a meeting: room tone reads as chords for a few seconds after a minute of talk
    b = BandPresence(window_s=6.0, min_harmony=0.5, sustain_s=45.0)
    for t in range(0, 60):
        b.observe(float(t), _probs(0.95))
    for t in range(60, 68):
        b.observe(float(t), _probs(0.1))
    assert b.playing and not b.sustained
    # a song: the band has played all along
    for t in range(68, 120):
        b.observe(float(t), _probs(0.1))
    assert b.playing and b.sustained
    # talk again: neither, as soon as the quick reading drops
    for t in range(120, 128):
        b.observe(float(t), _probs(0.95))
    assert not b.playing and not b.sustained
