"""A non-finite score never reaches the follower's belief (engine review
2026-11-10, finding 7): one NaN used to spread through it, read out slide 0
mid-song, and stop the follower until someone clicked."""

from __future__ import annotations

import numpy as np

from auto_presenter.follower.live import build_live_followers

SLIDES = [
    [],
    "amazing grace how sweet the sound".split(),
    "that saved a wretch like me".split(),
]
SECTIONS = ["other:0", "verse:1", "verse:1"]


def _song():
    followers = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    return followers, followers.songs[0]


def test_a_scorer_pass_with_a_nan_is_dropped(monkeypatch):
    followers, sf = _song()
    pushed = []
    monkeypatch.setattr(sf.follower, "push_scored", lambda *a, **k: pushed.append(a))
    monkeypatch.setattr(sf.follower, "push_heard", lambda *a, **k: pushed.append(a))
    span = (0, 2)  # any span key: the pass is dropped before it is read
    followers._push_scored(sf, 3.0, 3.0, {span: float("nan")})
    assert pushed == []


def test_bad_evidence_keeps_the_belief(monkeypatch):
    _followers, sf = _song()
    f = sf.follower
    before = f.alpha.copy()
    monkeypatch.setattr(f, "_evidence", lambda frame, wall: np.full(f.N, np.nan, np.float32))
    after = f._update(before.copy(), 0, 0.0)
    assert np.isfinite(after).all()
    assert np.allclose(after, before / before.sum())
