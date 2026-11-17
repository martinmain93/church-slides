"""A slide and its longer twin are one place (`online.TWINS_IN_ORDER`)."""

from __future__ import annotations

from auto_presenter.follower import online
from auto_presenter.follower.online import OnlineFollower
from auto_presenter.follower.reference import SegmentReference
from auto_presenter.follower.spec import library_spec

SLIDES = [
    [],
    "morning light upon the hills we wake and sing".split(),
    "every field and every tree we rest in you".split(),
    "jesus the name above every other name".split(),
    "every field and every tree we rest in you we rest in you".split(),
    "faithful through the longest night".split(),
]


def follower(slides=SLIDES) -> OnlineFollower:
    sections = ["other:0"] + [f"verse:{i}" for i in range(1, len(slides))]
    return OnlineFollower(SegmentReference(library_spec(slides), slides, sections))


def test_on_by_default():
    assert online.TWINS_IN_ORDER


def test_twins_are_found_both_ways():
    assert follower()._twins() == {2: {4}, 4: {2}}


def test_the_twin_out_of_order_shows_the_copy_in_order():
    f = follower()
    f.shown = 1
    assert f._twin_in_order(4) == 2  # the band sang the longer one at slide 2's place
    f.shown = 2
    assert f._twin_in_order(4) == 2


def test_a_step_on_from_the_twin_is_a_step_on_from_the_copy_shown():
    f = follower()
    f.shown = 2
    assert f._twin_in_order(5) == 3
    f.shown = 3  # already stepped on: stay
    assert f._twin_in_order(5) == 3


def test_reached_in_order_the_longer_twin_is_shown():
    f = follower()
    f.shown = 3
    assert f._twin_in_order(4) == 4
    f.shown = 4
    assert f._twin_in_order(5) == 5


def test_other_moves_are_untouched():
    f = follower()
    f.shown = 5
    assert f._twin_in_order(1) == 1
    assert f._twin_in_order(None) is None
    f.shown = None
    assert f._twin_in_order(4) == 4
    f.shown = 4
    assert f._twin_in_order(2) == 4  # on its twin: the same place, stay
    assert f._twin_in_order(1) == 1


def test_never_past_the_last_slide():
    slides = [[], "a b c d".split(), "x y z".split(), "a b c".split()]
    f = follower(slides)
    f.shown = 3
    assert f._twin_in_order(2) == 2


def test_switched_off_changes_nothing(monkeypatch):
    monkeypatch.setattr(online, "TWINS_IN_ORDER", False)
    f = follower()
    f.shown = 1
    assert f._twin_in_order(4) == 4


def test_identical_slides_are_not_twins():
    slides = [[], "a b c".split(), "d e f".split(), "a b c".split()]
    assert follower(slides)._twins() == {}
