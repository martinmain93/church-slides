"""Followers rebuilt mid-service (a setlist edit) carry on from the session's
clock, and a read-out long after its checkpoint doesn't step through every
frame in between."""

from __future__ import annotations

import time

from auto_presenter.follower.live import build_live_followers
from auto_presenter.follower.online import OnlineFollower
from auto_presenter.follower.reference import SegmentReference
from auto_presenter.follower.spec import library_spec

SLIDES = [
    [],
    ["amazing", "grace", "how", "sweet", "the", "sound"],
    ["that", "saved", "a", "wretch", "like", "me"],
    ["i", "once", "was", "lost", "but", "now", "am", "found"],
]
SECTIONS = ["other:0", "verse:1", "verse:2", "verse:3"]


def test_new_followers_take_over_the_session_clock():
    old = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    old.audio_now, old._last_scored_t, old.load_level = 3600.0, 3598.0, 1
    new = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    new.take_over(old)
    assert (new.audio_now, new._last_scored_t, new.load_level) == (3600.0, 3598.0, 1)


def test_a_read_out_an_hour_after_its_anchor_is_quick_and_keeps_the_slide():
    f = OnlineFollower(SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS))
    f.anchor(2, 0.0)
    f.shown = 2
    started = time.monotonic()
    r = f.readout(3600.0)
    assert time.monotonic() - started < 1.0
    assert r.slide == 2
