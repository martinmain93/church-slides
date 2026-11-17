"""The next-line check: what the follower asks near a line's end, and what it
does with the answer (`OnlineFollower.next_line_query` / `push_next_line`)."""

from __future__ import annotations

import numpy as np
import pytest

from auto_presenter.follower import online
from auto_presenter.follower.online import OnlineFollower
from auto_presenter.follower.reference import SegmentReference
from auto_presenter.follower.spec import library_spec

SLIDES = [
    [],
    "early in the morning".split(),
    "walking down the road".split(),
    "oh how bright the day".split(),
    "you will always walk beside me".split(),
    "this river running to the open sea".split(),
    "this song outlasting all my fear".split(),
]
SECTIONS = ["other:0", "verse:1", "verse:1", "chorus:1", "chorus:1", "verse:2", "verse:2"]


@pytest.fixture
def check_on(monkeypatch):
    monkeypatch.setattr(online, "NEXT_LINE_WEIGHT", 3.0)
    monkeypatch.setattr(online, "NEXT_LINE_JUMP", 0.5)


def _near_end_of_chorus() -> tuple[OnlineFollower, float, online.NextLineQuery]:
    """A follower on the chorus's last line, late in it, with words heard."""
    f = OnlineFollower(SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS), lookahead_s=0.0)
    f.anchor(4, 0.0)
    for k in range(1, 80):
        t = k * 0.1
        f.push_heard(t)
        q = f.next_line_query(f.readout(t).marginal_now)
        if q is not None:
            return f, t, q
    raise AssertionError("never asked")


def test_off_by_default():
    f = OnlineFollower(SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS), lookahead_s=0.0)
    f.anchor(4, 0.0)
    f.push_heard(1.0)
    assert f.next_line_query(f.readout(5.0).marginal_now) is None


def test_asks_written_next_and_the_section_again_near_a_line_end(check_on):
    _f, _t, q = _near_end_of_chorus()
    assert q.anchor == 4
    # "still on this line": runs of its own words, from anywhere in it
    stays = [sp for sp, sl in zip(q.spans, q.slides, strict=True) if not sl]
    assert "you will always walk beside me" in stays and "you will" in stays
    opened = {next(iter(sl)) for sl in q.slides if sl}
    assert {5, 3} <= opened  # verse 2 as written; the chorus again
    assert all(sp.startswith("always walk beside me ") for sp, sl in zip(q.spans, q.slides) if sl)
    assert q.slides[q.expected] == {5}


def test_no_question_in_an_instrumental(check_on):
    f = OnlineFollower(SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS), lookahead_s=0.0)
    f.anchor(4, 0.0)
    f.push_heard(0.5)
    for k in range(1, 120):
        r = f.readout(k * 0.1)
    assert f.next_line_query(r.marginal_now) is None  # nothing heard for 11 s


def _slide_after(answer_for: int) -> int:
    f, t, q = _near_end_of_chorus()
    scores = np.full(len(q.spans), -3.0)
    win = next(i for i, s in enumerate(q.slides) if answer_for in s)
    scores[win] = -1.0
    f.push_next_line(t - 0.2, t, q, scores)
    m = f.readout(t + 0.5).marginal_now
    idx = f._slide_idx[: f.ref.P]
    mass = np.bincount(idx[idx >= 0], weights=m[idx >= 0], minlength=f._n_slides)
    return int(np.argmax(mass[1:]) + 1)


def test_a_clear_answer_moves_the_belief_to_that_opening(check_on):
    assert _slide_after(3) == 3  # the band went back to the chorus
    assert _slide_after(5) == 5  # on to verse 2, as written


def test_still_on_this_line_says_nothing(check_on):
    f, t, q = _near_end_of_chorus()
    scores = np.full(len(q.spans), -3.0)
    scores[1] = -1.0  # a run from the middle of this line
    f.push_next_line(t - 0.2, t, q, scores)
    frame = int((t - 0.2) * f.fps)
    assert frame not in f._events and frame not in f._jumps
