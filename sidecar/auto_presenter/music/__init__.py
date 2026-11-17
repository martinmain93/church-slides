"""Chord and beat analysis as a live side channel.

Diagnostic first: nothing in the decision path consumes this yet. See
`Plan/CHORD_DOWNBEAT.md` for what it is measured to be worth.
"""

from auto_presenter.music.analyser import (
    ChordCandidate,
    MusicAnalyser,
    MusicUpdate,
)

__all__ = ["ChordCandidate", "MusicAnalyser", "MusicUpdate"]
