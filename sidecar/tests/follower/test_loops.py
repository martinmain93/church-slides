"""Counted loops (`reference.LOOPS`): a section the band repeats is unrolled
into passes, so the follower knows how many times it has been sung."""

from __future__ import annotations

import numpy as np
import pytest

from auto_presenter.follower import reference as ref_mod
from auto_presenter.follower.online import OnlineFollower
from auto_presenter.follower.reference import SegmentReference, loop_leave
from auto_presenter.follower.spec import library_spec

# A common chorus shape: a line written four times, then the next section
SLIDES = [
    [],
    ["lift", "your", "eyes", "to", "the", "hills"],
    ["you", "will", "not", "let", "go"],
    ["you", "will", "not", "let", "go"],
    ["you", "will", "not", "let", "go"],
    ["you", "will", "not", "let", "go"],
    ["when", "the", "storm", "is", "raging", "on"],
]
SECTIONS = ["other:0", "verse:1", "chorus:1", "chorus:2", "chorus:3", "chorus:4", "bridge:1"]


@pytest.fixture
def loops_on(monkeypatch):
    monkeypatch.setattr(ref_mod, "LOOPS", True)


def test_leaving_follows_the_count():
    assert loop_leave(1) < loop_leave(2) < loop_leave(4) < loop_leave(8)
    assert loop_leave(5) < 0.1 and loop_leave(7) < 0.1


def test_a_written_run_is_unrolled_into_counted_passes(loops_on):
    ref = SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS)
    assert len(ref.jump_targets) == 1 + 6 + (ref_mod.LOOP_MAX - 4)
    # every further pass shows the chorus line and repeats its cells
    extra = ref.seg_slides[7:]
    assert set(extra) <= {2, 3, 4, 5}
    seg4 = 5  # the fourth written pass
    nxt = 7  # the fifth pass
    # the written run sung as written: mostly on to the next line
    leave = max(ref_mod.LOOP_WRITTEN_END_LEAVE, loop_leave(4))
    assert ref.succ[seg4, nxt] == pytest.approx(1 - leave)
    assert ref.succ[seg4, 6] == pytest.approx(leave)
    # the last pass may still be the line
    last = len(ref.jump_targets) - 1
    assert ref.succ[last, last] == pytest.approx(ref_mod.LOOP_LAST_STAY)
    # inside the written run the band mostly follows the writing
    assert ref.succ[2, 3] == pytest.approx(1 - ref_mod.LOOP_WRITTEN_LEAVE)
    # a fifth pass is almost sure to be followed by a sixth
    assert ref.succ[7, 8] == pytest.approx(1 - loop_leave(5))
    prior = ref_mod.LOOPS
    ref_mod.LOOPS = False
    try:
        assert SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS).P < ref.P
    finally:
        ref_mod.LOOPS = prior
    assert ref.sung[ref.copy_of].tolist() == ref.sung.tolist()


def test_a_sixth_chorus_stays_on_the_chorus(loops_on):
    ref = SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS)
    f = OnlineFollower(ref, lookahead_s=0.0)
    f.anchor(1, 0.0)
    t = 1.0
    for w in SLIDES[1]:
        f.push_word(t, t + 0.5, w)
        t += 0.6
    for _ in range(6):
        for w in SLIDES[2]:
            f.push_word(t, t + 0.5, w)
            t += 0.6
        t += 0.6
    assert f.readout(t).slide in {2, 3, 4, 5}


def test_on_by_default_and_switchable(monkeypatch):
    assert ref_mod.LOOPS is True or ref_mod.LOOPS_ENV == "0"
    monkeypatch.setattr(ref_mod, "LOOPS", False)
    ref = SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS)
    assert len(ref.jump_targets) == 7
    assert np.array_equal(ref.copy_of, np.arange(ref.P))


def test_a_slide_marked_looped_gets_counted_passes(loops_on):
    spec = library_spec(SLIDES)
    spec.loops = [1]
    ref = SegmentReference(spec, SLIDES, SECTIONS)
    passes = [g for g, sl in enumerate(ref.seg_slides) if sl == 1]
    assert len(passes) == ref_mod.LOOP_MAX
    # after its first pass: sung again or on to the chorus
    assert ref.succ[passes[0], passes[1]] == pytest.approx(ref_mod.LOOP_MARKED_AGAIN)


def test_scroll_mode_reads_a_further_pass_as_the_words_it_repeats(loops_on):
    from auto_presenter.follower.live import _cell_words

    ref = SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS)
    slide, word = _cell_words(ref)
    extra = ref.jump_targets[7]  # the fifth chorus pass
    for c in range(extra, ref.end_cells[7] + 1):
        src = ref.copy_of[c]
        assert slide[c] == slide[src] and word[c] == word[src]


def test_a_pair_sung_as_written_moves_on_in_time(loops_on):
    """Two identical lines then a new one, sung as written: the new line is
    shown about as soon as without loops (the rhythm took 3 s longer)."""
    slides = [[], ["holy", "holy", "holy"], ["holy", "holy", "holy"], ["lord", "god", "almighty"]]
    sections = ["other:0", "verse:1", "verse:2", "verse:3"]

    def first_shown_at(loops: bool) -> float:
        prior = ref_mod.LOOPS
        ref_mod.LOOPS = loops
        try:
            f = OnlineFollower(SegmentReference(library_spec(slides), slides, sections))
        finally:
            ref_mod.LOOPS = prior
        f.anchor(1, 0.0)
        f.shown = 1
        t, words = 0.5, [*slides[1], *slides[2], *slides[3]]
        for w in words:
            f.push_word(t, t + 1.5, w)
            t += 0.6
        for k in range(int(t * 4) + 40):
            now = k * 0.25
            r = f.readout(now)
            if r.slide is not None and r.slide != f.shown:
                f.shown = r.slide
            if f.shown == 3:
                return now
        return 1e9

    assert first_shown_at(True) <= first_shown_at(False) + 1.0


def test_a_rebuild_keeps_the_pass_count(loops_on):
    from auto_presenter.follower.live import carry

    ref = SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS)
    f = OnlineFollower(ref)
    f.alpha[:] = 0.0
    fifth = ref.jump_targets[7]  # a cell on the fifth pass
    f.alpha[:, fifth + 2] = 1.0
    a = carry(f, SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS))
    assert ref.seg_of[int(np.argmax(a.sum(axis=0)))] == 7
