"""A step on to the next slide waits for the shown slide's last words
(`online.LEAVE_NEEDS_LAST_LINE`)."""

from __future__ import annotations

import pytest

from auto_presenter.follower import online
from auto_presenter.follower.online import OnlineFollower
from tests.follower.test_follower import reference


@pytest.fixture
def follower(monkeypatch):
    monkeypatch.setattr(online, "LEAVE_NEEDS_LAST_LINE", True)
    monkeypatch.setattr(online, "SCORES_FRESH_S", 1e9)
    f = OnlineFollower(reference())
    words = f.ref.slide_words
    assert words[1] and words[2] and words[1] != words[2]
    f.shown = 1
    f.note_span_scores(9.9, {})  # the line scorer is running
    return f


def _last(f, slide):
    return len(f.ref.slide_words[slide]) - 1


def test_on_by_default():
    assert online.LEAVE_NEEDS_LAST_LINE


def test_switched_off_changes_nothing(monkeypatch):
    monkeypatch.setattr(online, "LEAVE_NEEDS_LAST_LINE", False)
    f = OnlineFollower(reference())
    f.shown = 1
    assert f._wait_for_last_line(2, 10.0) == 2


def test_a_step_on_waits_until_the_last_words_are_heard(follower):
    f = follower
    assert f._wait_for_last_line(2, 10.0) == 1
    assert f.leave_holds == 1
    f.note_span_scores(10.4, {(1, _last(f, 1)): -2.0})
    assert f._wait_for_last_line(2, 10.5) == 2


def test_a_weak_score_on_the_last_words_is_not_heard(follower):
    f = follower
    f.note_span_scores(10.0, {(1, _last(f, 1)): online.TAIL_LEVEL - 0.5})
    assert f._wait_for_last_line(2, 10.5) == 1


def test_the_next_slides_opening_releases_it(follower):
    f = follower
    assert f._wait_for_last_line(2, 10.0) == 1
    f.note_span_scores(10.4, {(2, 2): -1.5})
    assert f._wait_for_last_line(2, 10.5) == 2


def test_the_wait_is_bounded(follower):
    f = follower
    t = 10.0
    while t < 10.0 + online.LEAVE_WAIT_MAX_S - 0.25:
        assert f._wait_for_last_line(2, t) == 1
        t += 0.5
    assert f._wait_for_last_line(2, t + 0.5) == 2


def test_a_wavering_belief_does_not_restart_the_wait(follower):
    f = follower
    t, wanted = 10.0, 0.0
    while wanted < online.LEAVE_WAIT_MAX_S:
        assert f._wait_for_last_line(2, t) == 1  # wants the next slide
        assert f._wait_for_last_line(1, t + 0.5) == 1  # back to the shown one
        t += 1.0
        wanted += 1.0
    assert f._wait_for_last_line(2, t) == 2


def test_last_words_heard_long_ago_do_not_count(follower):
    f = follower
    f.note_span_scores(1.0, {(1, _last(f, 1)): -1.0})
    assert f._wait_for_last_line(2, 1.0 + online.TAIL_HEARD_S + 1.0) == 1


def test_jumps_and_steps_back_are_not_held(follower):
    f = follower
    f.shown = 2
    assert f._wait_for_last_line(1, 10.0) == 1  # back one
    n = len(f.ref.slide_words)
    if n > 4:
        assert f._wait_for_last_line(4, 10.0) == 4  # a jump


def test_the_wait_restarts_on_a_new_slide(follower):
    f = follower
    for k in range(8):
        f._wait_for_last_line(2, 10.0 + k)  # the wait on slide 1 runs out
    f.shown = 2
    f._wait_for_last_line(2, 20.0)
    f.shown = 1
    assert f._wait_for_last_line(2, 30.0) == 1  # a fresh wait



def test_without_a_line_scorer_the_rule_stands_aside(monkeypatch):
    monkeypatch.setattr(online, "LEAVE_NEEDS_LAST_LINE", True)
    f = OnlineFollower(reference())
    f.shown = 1
    assert f._wait_for_last_line(2, 10.0) == 2
    f.note_span_scores(10.0, {})
    assert f._wait_for_last_line(2, 10.5) == 1
    assert f._wait_for_last_line(2, 10.5 + online.SCORES_FRESH_S) == 2
