"""Lead-time scheduler tests."""

from __future__ import annotations

import dataclasses

import pytest

from auto_presenter.decision.scheduler import (
    LeadTimeScheduler,
    LeadTimeSchedulerConfig,
    TrackedPosition,
)
from auto_presenter.index.library import build_index
from auto_presenter.protocol import SetlistContent, SetlistItemContent
from auto_presenter.tracker.types import BeliefState, Particle


def _setlist(slides_per_item: list[list[list[str]]]):
    content = SetlistContent(
        items=[
            SetlistItemContent(item_idx=i, song_id=f"s{i}", slides=slides)
            for i, slides in enumerate(slides_per_item)
        ]
    )
    return build_index(content)


def _belief(
    *,
    item_idx: int,
    slide_idx: int,
    position: float,
    rate: float,
    n_particles: int = 100,
    t_seconds: float = 0.0,
):
    """Synthesize a clean belief: all particles concentrated on one slide."""
    particles = [
        Particle(
            item_idx=item_idx,
            slide_idx=slide_idx,
            position=position,
            rate=rate,
            weight=1.0 / n_particles,
        )
        for _ in range(n_particles)
    ]
    return BeliefState(particles=particles, t_seconds=t_seconds)


# --- happy path -------------------------------------------------------------


def test_commits_when_within_lead_time():
    setlist = _setlist(
        [
            [
                ["amazing", "grace", "how", "sweet"],  # 4 words on slide 0
                ["that", "saved", "a", "wretch"],
            ]
        ]
    )
    scheduler = LeadTimeScheduler(setlist=setlist)
    # Position 0.9 of 4 words at 2 wps → 0.4 words remaining → 0.2 s remaining.
    # Less than the 500 ms lead time → should commit.
    decision = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.9, rate=2.0, t_seconds=1.5)
    )
    assert decision is not None
    assert decision.item_idx == 0
    assert decision.slide_idx == 1


def test_no_commit_when_well_inside_current_slide():
    """"Well inside" is relative to the lead, and the lead is now derived from
    the delivery lag — about 2.8 s before any margin. The old fixture left
    1.8 s on the slide, which a scheduler acting on 2.8-second-old evidence
    should absolutely commit; it takes a genuinely long slide to sit inside."""
    setlist = _setlist([[["a"] * 40, ["e", "f"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    decision = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.1, rate=2.0)
    )
    assert decision is None


def test_doesnt_refire_for_same_target():
    setlist = _setlist([[["a", "b", "c", "d"], ["e", "f"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    first = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.95, rate=2.0)
    )
    second = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.97, rate=2.0)
    )
    assert first is not None
    assert second is None  # duplicate


def test_fires_again_after_belief_moves_to_target_slide():
    """Once the cloud actually enters the new slide, the scheduler is
    free to commit the slide after it."""
    setlist = _setlist([[["a", "b"], ["c", "d"], ["e", "f"]]])
    scheduler = LeadTimeScheduler(
        setlist=setlist, config=LeadTimeSchedulerConfig(fixed_lead_time_ms=300)
    )
    first = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.95, rate=2.0)
    )
    assert first is not None and first.slide_idx == 1
    # Cloud crosses to slide 1, advances; scheduler now commits slide 2.
    second = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=1, position=0.95, rate=2.0)
    )
    assert second is not None and second.slide_idx == 2


# --- edge cases -------------------------------------------------------------


def test_no_commit_on_last_slide_of_song():
    setlist = _setlist([[["a", "b"], ["c", "d"]]])  # slide 1 is the last
    scheduler = LeadTimeScheduler(setlist=setlist)
    # Display already on the last slide; the band is there too → nowhere to go.
    scheduler.note_external_commit(0, 1)
    decision = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=1, position=0.95, rate=2.0)
    )
    assert decision is None


def test_no_commit_with_empty_slide():
    """An instrumental / blank slide has no n_words to drive ETA."""
    setlist = _setlist([[[], ["a", "b"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    decision = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.99, rate=2.0)
    )
    assert decision is None


def test_no_commit_when_marginal_below_threshold():
    """Diffuse cloud → scheduler holds its fire."""
    setlist = _setlist([[["a", "b"], ["c", "d"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    # Build a belief with two competing modes so the top marginal is ~0.5.
    particles = [
        Particle(item_idx=0, slide_idx=0, position=0.95, rate=2.0, weight=0.5),
        Particle(item_idx=0, slide_idx=1, position=0.1, rate=2.0, weight=0.5),
    ]
    belief = BeliefState(particles=particles)
    # Default threshold is 0.4 → top marginal of 0.5 still passes.
    # Push threshold higher to test the gate.
    scheduler.config = LeadTimeSchedulerConfig(marginal_threshold=0.8)
    assert scheduler.on_belief(belief) is None


def test_min_rate_floor_prevents_degenerate_eta():
    """A near-zero rate would otherwise compute an enormous ETA and
    suppress commits forever."""
    setlist = _setlist([[["a", "b", "c", "d"], ["e"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    # Real rate = 0.01 wps → without the floor, ETA on a 4-word slide
    # from position 0.5 would be 200 s. With the 0.5 wps floor it's 4 s,
    # still well above lead time → no commit. Use a high position so the
    # floor's effect is visible: ETA = (1-0.99)*4/0.5 = 0.08 s → commit.
    decision = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.99, rate=0.01)
    )
    assert decision is not None


def test_note_external_commit_suppresses_redundant_fire():
    """Operator advances a slide → scheduler shouldn't immediately
    re-fire that same target as if it were anticipating."""
    setlist = _setlist([[["a", "b"], ["c", "d"], ["e", "f"]]])
    scheduler = LeadTimeScheduler(
        setlist=setlist, config=LeadTimeSchedulerConfig(fixed_lead_time_ms=300)
    )
    scheduler.note_external_commit(0, 1)
    # Now the cloud is at slide 1 near the end — would normally fire (0, 2).
    decision = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.99, rate=2.0)
    )
    # Even though slide_idx in the belief is 0 (filter not caught up),
    # the scheduler thinks we already committed to slide 1, so it skips.
    assert decision is None


def test_reset_clears_last_commit():
    setlist = _setlist([[["a", "b"], ["c", "d"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.99, rate=2.0)
    )
    scheduler.reset()
    # Should fire again after reset.
    again = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=0, position=0.99, rate=2.0)
    )
    assert again is not None


def test_empty_belief_returns_none():
    setlist = _setlist([[["a", "b"], ["c", "d"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    assert scheduler.on_belief(BeliefState(particles=[])) is None


def test_top_hypothesis_on_unknown_item_returns_none():
    setlist = _setlist([[["a", "b"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    bogus = _belief(item_idx=99, slide_idx=0, position=0.9, rate=2.0)
    assert scheduler.on_belief(bogus) is None


# --- regression: blank leading slide ----------------------------------------


def test_blank_slide_holds_until_band_starts():
    """The synthetic leading blank slide (no words) must not auto-advance
    on its own — but once the band starts singing slide 1, the scheduler
    catches the display up off the blank."""
    setlist = _setlist([[[], ["amazing", "grace", "how", "sweet"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    # Belief sitting on the blank slide 0 — no words to anticipate → hold.
    assert (
        scheduler.on_belief(_belief(item_idx=0, slide_idx=0, position=0.5, rate=2.0))
        is None
    )
    # Band starts singing → belief mode moves to slide 1, ahead of the
    # displayed blank slide 0. Catch up to it immediately.
    decision = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=1, position=0.1, rate=2.0)
    )
    assert decision is not None
    assert decision.slide_idx == 1


def test_catch_up_when_belief_jumps_ahead_of_display():
    """Regression: the cloud reaches the last slide while the display lags
    behind. The scheduler must commit to where the band is, not freeze."""
    setlist = _setlist([[["a", "b"], ["c", "d"], ["e", "f"]]])  # 3 slides
    scheduler = LeadTimeScheduler(setlist=setlist)
    # Display is on slide 1 (operator/AI put it there); the belief has
    # already advanced to slide 2 (the last slide) at high confidence.
    scheduler.note_external_commit(0, 1)
    decision = scheduler.on_belief(
        _belief(item_idx=0, slide_idx=2, position=0.2, rate=2.0)
    )
    assert decision is not None
    assert decision.slide_idx == 2  # caught up to the band, last slide and all


def test_catch_up_does_not_refire_once_synced():
    setlist = _setlist([[["a", "b"], ["c", "d"], ["e", "f"]]])
    scheduler = LeadTimeScheduler(
        setlist=setlist, config=LeadTimeSchedulerConfig(fixed_lead_time_ms=300)
    )
    scheduler.note_external_commit(0, 1)
    first = scheduler.on_belief(_belief(item_idx=0, slide_idx=2, position=0.2, rate=2.0))
    assert first is not None and first.slide_idx == 2
    # Belief still on slide 2, display now synced to 2 → no duplicate fire
    # (and slide 2 is the last slide, so no anticipation either).
    second = scheduler.on_belief(_belief(item_idx=0, slide_idx=2, position=0.3, rate=2.0))
    assert second is None


# --- backward commits (Phase 7) ---------------------------------------------


def _song(n_slides: int = 8, words_per_slide: int = 20):
    """Slides long enough that the lead-time projection is not always firing.

    Three-word slides were fine while `fixed_lead_time_ms` was 300: at 2 words/s a
    slide has 1.5 s of runway, so the projection stayed quiet. At the shipped
    1500 ms it fires from the very first word, which turned every test here
    into a test of anticipation rather than of the branch it meant to exercise.
    Real slides are 8-26 words.
    """
    return _setlist(
        [[["word", f"w{i}", *[f"x{j}" for j in range(words_per_slide - 2)]]
          for i in range(n_slides)]]
    )


def test_follows_the_band_back_to_a_repeated_section():
    """The band returns to the bridge. Refusing this made repeats structurally
    uncommittable: the tracker re-localized and the decision was discarded."""
    setlist = _song()
    s = LeadTimeScheduler(setlist=setlist)
    s.note_external_commit(0, 6)
    decision = s.on_belief(
        _belief(item_idx=0, slide_idx=3, position=0.1, rate=2.0, t_seconds=10.0)
    )
    assert decision is not None
    assert decision.slide_idx == 3


def test_one_slide_behind_late_in_the_slide_is_lag_and_is_left_alone():
    """Being one slide behind is the *normal* state just after anticipating,
    and moving back on it would flap on every commit.

    What makes it lag is *where in the slide* the belief sits: we commit N+1
    early precisely because the band is finishing N, so the belief is late in
    N (position near 1) and about to leave on its own. That is the signature
    `backward_one_max_position` tests for — see the companion below, which is
    the same one-slide disagreement with the opposite signature.
    """
    setlist = _song()
    s = LeadTimeScheduler(setlist=setlist)
    s.note_external_commit(0, 4)
    assert s.on_belief(
        _belief(item_idx=0, slide_idx=3, position=0.92, rate=2.0, t_seconds=10.0)
    ) is None


def test_one_slide_behind_at_the_start_of_the_slide_is_a_real_repeat():
    """The same one-slide disagreement, and the opposite conclusion.

    A belief sitting at the *start* of the previous slide cannot be
    anticipation lag: anticipation only fires when the current slide is nearly
    over, so it never leaves the belief near position 0. It is the band having
    gone back — a repeat or a reprise — and the screen is simply wrong.

    Taken from a real trace ("The Blood" medley, t=75-86 s): the belief left
    slide 4 at position 0.88, re-entered slide 3 at position 0.09 and climbed
    to 0.89 over eleven seconds at up to 0.94 confidence, while the screen
    stayed on 4 the whole time.
    """
    setlist = _song()
    s = LeadTimeScheduler(setlist=setlist)
    # A scheduler commit, so the screen state is ours rather than a human's.
    assert s.on_position(
        TrackedPosition(item_idx=0, slide_idx=4, confidence=0.9,
                        position=0.05, rate=1.0, t_seconds=9.0)
    ) is not None
    decision = s.on_belief(
        _belief(item_idx=0, slide_idx=3, position=0.09, rate=2.0, t_seconds=10.0)
    )
    assert decision is not None and decision.slide_idx == 3


def test_backward_commit_demands_more_confidence_than_forward():
    """Yanking the screen backwards is more jarring than being a beat late,
    so a diffuse belief must not be able to do it."""
    setlist = _song()
    s = LeadTimeScheduler(setlist=setlist)
    s.note_external_commit(0, 6)
    # 50% marginal clears the forward threshold (0.4) but not the backward
    # one (0.6).
    particles = [
        Particle(item_idx=0, slide_idx=3, position=0.1, rate=2.0, weight=0.5),
        Particle(item_idx=0, slide_idx=7, position=0.1, rate=2.0, weight=0.5),
    ]
    belief = BeliefState(particles=particles, t_seconds=10.0)
    assert s.on_belief(belief) is None


def test_backward_thresholds_are_configurable():
    setlist = _song()
    s = LeadTimeScheduler(
        setlist=setlist,
        config=LeadTimeSchedulerConfig(backward_min_slides=4),
    )
    s.note_external_commit(0, 6)
    # 3 slides back is no longer enough.
    assert s.on_belief(
        _belief(item_idx=0, slide_idx=3, position=0.1, rate=2.0, t_seconds=10.0)
    ) is None
    assert s.on_belief(
        _belief(item_idx=0, slide_idx=1, position=0.1, rate=2.0, t_seconds=11.0)
    ) is not None


def test_a_scheduler_commit_is_not_recorded_as_a_deviation():
    """A commit must not mark itself a repeat of itself.

    Both engines used to call `note_external_commit` after `on_position` had
    already recorded the commit. `SongProgress.note_commit` sees the same slide
    twice, `slide_idx <= previous` holds, and the slide lands in
    `_jump_origins` — so `order_is_predictive` was False for every slide ever
    displayed and the lead-time anticipation could never fire. That is the
    "41 of 41 commits came from catch-up and none from anticipation"
    observation in this module's own docstring.
    """
    sched = LeadTimeScheduler(
        setlist=_setlist([[["word"] * 12 for _ in range(6)]]),
        config=LeadTimeSchedulerConfig(fixed_lead_time_ms=300, marginal_threshold=0.4),
    )
    for slide_idx in (1, 2, 3):
        decision = sched.on_position(
            TrackedPosition(item_idx=0, slide_idx=slide_idx, confidence=0.9,
                            position=0.1, rate=1.0, t_seconds=float(slide_idx))
        )
        assert decision is not None and decision.slide_idx == slide_idx

    assert sched.progress.deviated is False, (
        "a run of forward commits is not a deviation"
    )
    for slide_idx in (1, 2, 3):
        assert sched.progress.order_is_predictive(slide_idx, at_last_slide=False), (
            f"anticipation disabled at slide {slide_idx} by its own commit"
        )


def test_a_real_backward_move_is_still_a_deviation():
    """The guard above must not swallow the signal it is protecting."""
    sched = LeadTimeScheduler(
        setlist=_setlist([[["word"] * 12 for _ in range(6)]]),
        config=LeadTimeSchedulerConfig(fixed_lead_time_ms=300, marginal_threshold=0.4,
                                       backward_min_slides=2),
    )
    for slide_idx in (1, 2, 3, 4):
        sched.on_position(
            TrackedPosition(item_idx=0, slide_idx=slide_idx, confidence=0.9,
                            position=0.1, rate=1.0, t_seconds=float(slide_idx))
        )
    sched.on_position(
        TrackedPosition(item_idx=0, slide_idx=2, confidence=0.9,
                        position=0.1, rate=1.0, t_seconds=9.0)
    )
    assert sched.progress.deviated is True
    assert sched.progress.order_is_predictive(4, at_last_slide=False) is False


def test_commits_on_pooled_confidence_across_identical_slides():
    """A chorus written as four identical slides splits the mass four ways.

    The per-slide marginal then sits near 0.25 while the pooled mass over the
    copies — every one of which puts the same words on the screen — is well
    above threshold. Gating on the split value refuses to commit to a slide we
    are in fact sure about, and measured as the single largest cause of the
    tracker never reaching a slide at all (39% of blocked moments).
    """
    chorus = ["you", "are", "kind", "kind", "oh"]
    setlist = _setlist([[["verse", "one", "here"], chorus, chorus, chorus, chorus]])
    scheduler = LeadTimeScheduler(
        setlist=setlist, config=LeadTimeSchedulerConfig(marginal_threshold=0.4)
    )
    # Mass spread evenly over the four identical copies: 0.24 each, 0.96 pooled.
    particles = [
        Particle(item_idx=0, slide_idx=i, position=0.1, rate=1.0, weight=0.24)
        for i in (1, 2, 3, 4)
    ]
    belief = BeliefState(
        particles=particles, t_seconds=5.0, display_marginal=0.96
    )
    assert belief.top_hypothesis[2] < 0.4  # would be refused on its own
    decision = scheduler.on_belief(belief)
    assert decision is not None
    assert setlist.item(0).slides[decision.slide_idx].words == tuple(chorus)


def test_a_genuinely_split_belief_still_does_not_commit():
    """The pooling must not become a way to commit on any diffuse cloud — only
    on one whose candidates would display the same words."""
    setlist = _setlist([[["verse", "one", "here"], ["chorus", "words", "differ"],
                         ["bridge", "text", "again"]]])
    scheduler = LeadTimeScheduler(
        setlist=setlist, config=LeadTimeSchedulerConfig(marginal_threshold=0.4)
    )
    particles = [
        Particle(item_idx=0, slide_idx=i, position=0.1, rate=1.0, weight=0.33)
        for i in (0, 1, 2)
    ]
    # Distinct slides pool to nothing: display confidence is the top marginal.
    belief = BeliefState(particles=particles, t_seconds=5.0, display_marginal=0.33)
    assert scheduler.on_belief(belief) is None


def _commit_at(scheduler, *, slide: int, t: float, confidence: float = 0.9):
    """Drive `on_position` at the start of `slide` and return what it committed."""
    d = scheduler.on_position(
        TrackedPosition(item_idx=0, slide_idx=slide, confidence=confidence,
                        position=0.05, rate=1.0, t_seconds=t)
    )
    return None if d is None else d.slide_idx


# --- one-slide backward moves, gated on how long the screen has been up ------


def test_a_one_slide_backward_move_is_refused_right_after_committing():
    """Just after an anticipatory commit, a one-slide disagreement *is* the
    anticipation — the band has not arrived yet. Stepping back here is the
    flip-flop `backward_min_slides` exists to prevent.

    Position is pinned high here because that is what lag actually looks like;
    the duration rule under test cannot tell the difference, which is why
    `backward_one_max_position` now carries this distinction instead.
    """
    setlist = _song(6)
    scheduler = LeadTimeScheduler(
        setlist=setlist,
        config=LeadTimeSchedulerConfig(
            backward_one_after_seconds=4.0, backward_one_max_position=None
        ),
    )
    assert _commit_at(scheduler, slide=3, t=10.0) == 3
    assert _commit_at(scheduler, slide=2, t=11.0) is None


def test_the_same_move_is_allowed_once_the_screen_has_been_up_a_while():
    """Seconds later the disagreement means something else: the band did not go
    where we guessed, and the screen is simply wrong."""
    setlist = _song(6)
    scheduler = LeadTimeScheduler(
        setlist=setlist,
        config=LeadTimeSchedulerConfig(
            backward_one_after_seconds=4.0, backward_one_max_position=None
        ),
    )
    assert _commit_at(scheduler, slide=3, t=10.0) == 3
    assert _commit_at(scheduler, slide=2, t=16.0) == 2


def test_an_operator_override_restarts_the_dwell():
    """`note_external_commit` carries no timestamp, so the time is unknown —
    and unknown must mean *just now*. Stepping the screen back one slide
    immediately after a human moved it is the worst version of this."""
    setlist = _song(6)
    scheduler = LeadTimeScheduler(
        setlist=setlist,
        config=LeadTimeSchedulerConfig(
            backward_one_after_seconds=4.0, backward_one_max_position=None
        ),
    )
    assert _commit_at(scheduler, slide=3, t=10.0) == 3
    scheduler.note_external_commit(0, 3)
    assert _commit_at(scheduler, slide=2, t=20.0) is None


def test_a_larger_backward_jump_still_needs_no_dwell():
    """A two-slide step was never the ambiguous case and is unchanged."""
    setlist = _song(6)
    scheduler = LeadTimeScheduler(
        setlist=setlist,
        config=LeadTimeSchedulerConfig(
            backward_one_after_seconds=4.0, backward_one_max_position=None
        ),
    )
    assert _commit_at(scheduler, slide=4, t=10.0) == 4
    assert _commit_at(scheduler, slide=2, t=11.0) == 2


def test_the_shipped_default_corrects_a_one_slide_error_by_position():
    """What production actually runs — `LeadTimeSchedulerConfig()` with no
    arguments.

    The **duration** exception stays off: it was measured worth 0.008 for 4.5x
    the screen reversals, and re-measured on the displayed metric it costs six
    extra backward commits where position costs two, for identical accuracy.
    The **position** exception is on at 0.25, because a belief at the start of
    the previous slide is not a state anticipation lag can produce.
    """
    cfg = LeadTimeSchedulerConfig()
    assert cfg.backward_one_after_seconds is None
    assert cfg.backward_one_max_position == 0.25

    scheduler = LeadTimeScheduler(setlist=_song(6))
    assert _commit_at(scheduler, slide=3, t=10.0) == 3
    # Start of the previous slide: a real repeat, corrected.
    assert _commit_at(scheduler, slide=2, t=11.0) == 2


def test_the_shipped_default_still_ignores_ordinary_anticipation_lag():
    """The other half of the same default: late in the previous slide is lag,
    and must stay untouched however long it persists."""
    scheduler = LeadTimeScheduler(setlist=_song(6))
    assert _commit_at(scheduler, slide=3, t=10.0) == 3
    late = scheduler.on_position(
        TrackedPosition(item_idx=0, slide_idx=2, confidence=0.9,
                        position=0.90, rate=1.0, t_seconds=30.0)
    )
    assert late is None


def test_the_position_exception_never_undoes_an_operator():
    """A human moving the screen outranks the re-entry signal.

    `note_external_commit` carries no timestamp, and unknown must mean *just
    now*. Stepping the screen back one slide immediately after a person moved
    it is the worst version of this behaviour, so the position rule declines
    it too — not only the duration rule that originally guarded this.
    """
    scheduler = LeadTimeScheduler(setlist=_song(6))
    assert _commit_at(scheduler, slide=3, t=10.0) == 3
    scheduler.note_external_commit(0, 3)
    assert _commit_at(scheduler, slide=2, t=20.0) is None


def test_the_delayed_tail_of_a_slide_does_not_undo_its_own_anticipation():
    """The expected steady state after anticipating, and it must be quiet.

    Evidence arrives late by construction: `SlidingWindowWrapper` only marks a
    word final once it is `stable_margin_seconds` (1.5 s) behind the buffer
    edge, and passes run every `step_seconds` (1 s), on top of the Whisper pass
    itself. So for a second or three *after* committing N+1 we keep receiving
    the tail of slide N, and the belief correctly stays on N. That is not an
    error state and must not be treated as one.

    It is safe because of *where* in N the belief sits. Anticipation fires only
    when the current slide is nearly over, so the tail arrives at high
    position — read as lag and left alone — while a real repeat re-enters near
    position 0. Measured over the seven approved goldens: 84 anticipatory
    commits, 33 following observations still on the previous slide, 30 of them
    above the 0.25 bar, and **zero** anticipations undone.
    """
    scheduler = LeadTimeScheduler(setlist=_song(6))

    # Belief late in slide 3 -> anticipate slide 4. The screen is now one
    # ahead of the belief, which is the intended state, not a fault.
    anticipated = scheduler.on_position(
        TrackedPosition(item_idx=0, slide_idx=3, confidence=0.95,
                        position=0.95, rate=1.0, t_seconds=10.0)
    )
    assert anticipated is not None and anticipated.slide_idx == 4

    # Now the tail of slide 3 keeps arriving. The belief stays on 3, late in
    # it, at high confidence — everything the backward rule inspects — and the
    # screen must never be pulled back behind what we just committed.
    for t, position in ((10.6, 0.86), (11.4, 0.91), (12.1, 0.95), (12.9, 0.98)):
        d = scheduler.on_position(
            TrackedPosition(item_idx=0, slide_idx=3, confidence=0.95,
                            position=position, rate=1.0, t_seconds=t)
        )
        assert d is None or d.slide_idx >= 4, (
            f"delayed tail evidence undid the anticipation at t={t}"
        )


# --- projection, as reported to the debug UI ---------------------------------
#
# `project()` exists so the operator's countdown and the commit rule cannot
# disagree. These tests hold that guarantee: the reported number has to be the
# number that was branched on, over the whole input space, not merely equal on
# a couple of hand-picked cases.


def test_the_reported_countdown_is_the_one_the_rule_acts_on():
    """Sweep position and rate; wherever `project` says the countdown has
    elapsed and order is predictive, a commit must actually fire, and wherever
    it says otherwise, none may. A readout that drifts from the rule misleads
    precisely when it is being trusted."""
    lead = LeadTimeSchedulerConfig(fixed_lead_time_ms=1500)
    checked = 0
    for position in (0.0, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0):
        for rate in (0.3, 1.0, 2.0, 4.0):
            setlist = _setlist([[["a", "b", "c", "d", "e", "f"], ["g", "h"]]])
            scheduler = LeadTimeScheduler(setlist=setlist, config=lead)
            tracked = TrackedPosition(
                item_idx=0,
                slide_idx=0,
                confidence=1.0,
                position=position,
                rate=rate,
                t_seconds=10.0,
            )
            projection = scheduler.project(tracked)
            assert projection is not None
            decision = scheduler.on_position(tracked)

            # Slide 0 is displayed initially only after a commit; from a cold
            # scheduler the displayed slide is the lead-in, so a projection at
            # `mode_slide` still fires as catch-up. What must hold is the
            # target: whatever `project` pointed at is what got committed.
            if decision is not None:
                assert decision.slide_idx == projection.target_slide
            assert projection.advancing == (projection.target_slide == 1)
            checked += 1
    assert checked == 28


def test_projection_reports_when_it_is_blocked_by_unpredictable_order():
    """A countdown at zero that never fires looks broken from outside. The
    projection has to distinguish 'not yet' from 'blocked here', which is the
    difference between waiting and a bridge the band has looped before."""
    setlist = _setlist(
        [[["a", "b", "c", "d"], ["e", "f"], ["g", "h", "i", "j"], ["k", "l"]]]
    )
    scheduler = LeadTimeScheduler(setlist=setlist)
    # Standing at the end of slide 2 — the end of a bridge, in the case this
    # protects.
    tracked = TrackedPosition(
        item_idx=0, slide_idx=2, confidence=1.0, position=0.99, rate=2.0, t_seconds=5.0
    )

    assert scheduler.project(tracked).advancing is True

    # Now teach it that the band has left slide 2 backwards before — looped
    # the bridge — so what follows it is no longer a safe assumption.
    scheduler.progress.note_commit(0, 2)
    scheduler.progress.note_commit(0, 1)
    blocked = scheduler.project(tracked)
    assert blocked.advancing is False
    assert blocked.target_slide == 2
    # The countdown itself is unchanged — it elapsed; the step was refused.
    assert blocked.seconds_remaining < blocked.lead_seconds


def test_projection_is_pure():
    """The debug panel polls this. If it mutated scheduler state, watching the
    countdown would change what the engine does."""
    setlist = _setlist([[["a", "b", "c", "d"], ["e", "f"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)
    tracked = TrackedPosition(
        item_idx=0, slide_idx=0, confidence=1.0, position=0.99, rate=2.0, t_seconds=5.0
    )
    for _ in range(5):
        scheduler.project(tracked)

    # Still able to commit — nothing was consumed or marked as fired.
    assert scheduler.on_position(tracked) is not None


def test_no_projection_where_there_is_nothing_to_project_to():
    """Reported as a dash rather than a fabricated zero."""
    setlist = _setlist([[[], ["a", "b"]]])
    scheduler = LeadTimeScheduler(setlist=setlist)

    def at(slide_idx: int):
        return TrackedPosition(
            item_idx=0, slide_idx=slide_idx, confidence=1.0,
            position=0.5, rate=1.0, t_seconds=1.0,
        )

    assert scheduler.project(at(0)) is None  # the blank lead-in
    assert scheduler.project(at(1)) is None  # last slide of the song


# --- leaving a song ----------------------------------------------------------
#
# Traced live: the marginal fell to 0.20-0.34 mid-song, mass pooled onto
# another song's opening, and at 0.46 the screen jumped there for 2.8 s. One
# threshold was gating "advance one slide" and "abandon this song" alike.


def _two_songs():
    return _setlist(
        [
            [[], ["a", "b", "c", "d"], ["e", "f", "g", "h"], ["i", "j", "k", "l"]],
            [[], ["m", "n", "o", "p"], ["q", "r", "s", "t"]],
        ]
    )


def _at(item, slide, conf, t=0.0, position=0.5, rate=1.0):
    return TrackedPosition(
        item_idx=item, slide_idx=slide, confidence=conf,
        position=position, rate=rate, t_seconds=t,
    )


def _establish(scheduler):
    """Get song 0 onto the screen so a later move is a genuine song change."""
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None


def test_the_live_jump_no_longer_fires():
    """0.46 on a single observation moved the screen to another song. It must
    not, at any point in the sequence that produced it."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)
    assert scheduler.on_position(_at(1, 1, 0.46, t=5.0)) is None


def _heard(scheduler, tracked, words=4):
    """One transcript event: its words, then the belief they produced."""
    scheduler.note_words(words)
    return scheduler.on_position(tracked)


def test_a_confident_song_change_still_happens_once_it_persists():
    """The guard must not make songs unreachable — the band really does move
    on, and before `cross_song` existed the tracker could not follow at all."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)

    assert _heard(scheduler, _at(1, 1, 0.9, t=5.0)) is None  # 1st
    assert _heard(scheduler, _at(1, 1, 0.9, t=6.0)) is None  # 2nd
    decision = _heard(scheduler, _at(1, 1, 0.9, t=7.0))      # 3rd
    assert decision is not None and decision.item_idx == 1


def test_a_dip_in_confidence_breaks_the_streak():
    """Otherwise a long run of wobbling observations accumulates a majority it
    never actually held, which is the same spike with extra steps."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)

    _heard(scheduler, _at(1, 1, 0.9, t=5.0))
    _heard(scheduler, _at(1, 1, 0.5, t=6.0))  # dip — streak resets
    assert _heard(scheduler, _at(1, 1, 0.9, t=7.0)) is None
    assert _heard(scheduler, _at(1, 1, 0.9, t=8.0)) is None
    assert _heard(scheduler, _at(1, 1, 0.9, t=9.0)) is not None


def test_a_few_stray_words_cannot_change_songs():
    """Background chatter arrives about a word per event, so three confident
    observations can rest on three words — not a song the band has started."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)

    for t in (5.0, 6.0, 7.0):
        assert _heard(scheduler, _at(1, 1, 0.9, t=t), words=1) is None
    # The 4th word since the streak began carries it.
    assert _heard(scheduler, _at(1, 1, 0.9, t=8.0), words=1) is not None


def test_talking_blocks_a_song_change_and_breaks_the_streak():
    """Reported live 2026-09-28: conversation in the room, nobody singing,
    and the screen changed songs. Leaving a song needs the room to sound sung."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)

    scheduler.talking_score = 0.8
    for t in range(5, 10):
        assert _heard(scheduler, _at(1, 1, 0.95, t=float(t))) is None

    # Singing resumes: the streak starts over rather than carrying the talk.
    scheduler.talking_score = 0.02
    assert _heard(scheduler, _at(1, 1, 0.9, t=10.0)) is None
    assert _heard(scheduler, _at(1, 1, 0.9, t=11.0)) is None
    assert _heard(scheduler, _at(1, 1, 0.9, t=12.0)) is not None


def test_talking_does_not_gate_moves_within_a_song():
    """The talking gate is for leaving a song only; the blanking feature owns
    what the screen does while someone talks mid-song."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)
    scheduler.talking_score = 0.9
    assert _heard(scheduler, _at(0, 2, 0.5, t=5.0)) is not None


def test_a_song_tie_cannot_change_songs_however_sure_the_slide_looks():
    """Traced 2026-09-29: a lone "!" left seven songs tied at ~0.14 each, and
    the tie-winner's slide confidence — conditioned on the song — read 0.95.
    Leaving a song has to ask about the song."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)
    for t in range(5, 12):
        tied = dataclasses.replace(_at(1, 1, 0.95, t=float(t)), song_share=0.14)
        assert _heard(scheduler, tied) is None


def test_a_song_the_belief_has_moved_to_is_followed():
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)
    decision = None
    for t in (5.0, 6.0, 7.0):
        decision = _heard(
            scheduler, dataclasses.replace(_at(1, 1, 0.9, t=t), song_share=0.9)
        )
    assert decision is not None and decision.item_idx == 1


def test_a_belief_torn_between_two_songs_commits_to_neither():
    """Alternating between the displayed song and a candidate keeps resetting
    the streak, so an unsettled belief never moves the screen off its song."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)
    for t in range(5, 12):
        scheduler.on_position(_at(1, 1, 0.9, t=float(t)))
        scheduler.on_position(_at(0, 2, 0.9, t=float(t) + 0.5))

    assert scheduler._last_committed is not None
    assert scheduler._last_committed[0] == 0


def test_moves_within_the_displayed_song_are_untouched():
    """The ordinary path must not pay for this guard."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    _establish(scheduler)
    decision = scheduler.on_position(_at(0, 2, 0.5, t=5.0))
    assert decision is not None and decision.item_idx == 0


def test_the_first_song_of_a_session_needs_no_streak():
    """Nothing is displayed yet, so there is no song to be abandoned."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    assert scheduler.on_position(_at(1, 1, 0.5, t=1.0)) is not None


# --- the freeze watchdog -----------------------------------------------------
#
# `marginal_threshold` was the only gate on the commit path and it has no
# floor. Traced live: the marginal sat at 0.08-0.27 for tens of seconds and the
# screen never moved again for the last 51 s of a song.


def _long_song():
    # 20-word slides: at 1 w/s these genuinely outlast the derived lead, which
    # four-word slides no longer do.
    return _setlist([[[], ["a"] * 20, ["b"] * 20, ["c"] * 20, ["d"] * 20]])


def test_a_settled_belief_eventually_moves_a_stuck_screen():
    scheduler = LeadTimeScheduler(setlist=_long_song())
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None

    # Now the belief sits on slide 3, too unconfident to commit normally.
    assert scheduler.on_position(_at(0, 3, 0.25, t=2.0)) is None
    assert scheduler.on_position(_at(0, 3, 0.25, t=12.0)) is None
    decision = scheduler.on_position(_at(0, 3, 0.25, t=19.0))
    assert decision is not None and decision.slide_idx == 3


def test_a_long_slide_sung_correctly_never_trips_it():
    """The screen holding for a minute is normal when it is *right*. The
    trigger is disagreement, not elapsed time."""
    scheduler = LeadTimeScheduler(setlist=_long_song())
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None
    for t in range(2, 60):
        # Belief agrees with the screen; position stays mid-slide.
        assert scheduler.on_position(_at(0, 1, 0.9, t=float(t), position=0.3)) is None


def test_a_thrashing_belief_does_not_get_to_guess():
    """When the tracker genuinely does not know, freezing beats guessing — so
    a candidate that keeps changing resets the timer and never fires."""
    scheduler = LeadTimeScheduler(setlist=_long_song())
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None
    for t in range(2, 80):
        slide = 2 if t % 2 else 3
        assert scheduler.on_position(_at(0, slide, 0.25, t=float(t))) is None


def test_noise_is_below_the_floor():
    scheduler = LeadTimeScheduler(setlist=_long_song())
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None
    for t in range(2, 80):
        assert scheduler.on_position(_at(0, 3, 0.05, t=float(t))) is None


def test_the_watchdog_will_not_change_songs():
    """Leaving a song needs *more* evidence, and this path has less by
    definition — so it must never be the thing that does it."""
    scheduler = LeadTimeScheduler(setlist=_two_songs())
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None
    for t in range(2, 80):
        assert scheduler.on_position(_at(1, 1, 0.25, t=float(t))) is None


def test_it_can_be_turned_off():
    scheduler = LeadTimeScheduler(
        setlist=_long_song(),
        config=LeadTimeSchedulerConfig(stall_after_seconds=None),
    )
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None
    for t in range(2, 80):
        assert scheduler.on_position(_at(0, 3, 0.25, t=float(t))) is None


# --- committing on the distribution, not its mean ----------------------------


def _spread(*pairs):
    return tuple((p, w) for p, w in pairs)


def test_a_split_belief_is_not_averaged_into_a_confident_middle():
    """The failure the point estimate cannot see: half the belief says the
    slide is nearly done, half says it barely started. Their mean is a
    confident "halfway", which is a claim neither half makes."""
    setlist = _setlist([[[], ["a", "b", "c", "d"], ["e", "f"], ["g", "h"]]])
    config = LeadTimeSchedulerConfig(fixed_lead_time_ms=1000, transition_probability=0.7)

    split = LeadTimeScheduler(setlist=setlist, config=config)
    split.on_position(_at(0, 1, 0.95, t=1.0))
    # Mean position 0.5 at 2 w/s over 4 words -> 1.0 s remaining, right at the
    # lead. But only half the mass is actually near the end.
    projection = split.project(
        TrackedPosition(
            item_idx=0, slide_idx=1, confidence=0.9, position=0.5, rate=2.0,
            t_seconds=5.0, position_spread=_spread((0.0, 0.5), (1.0, 0.5)),
        )
    )
    assert projection is not None
    assert projection.advancing is False  # 0.5 of the mass < the 0.7 bar


def test_a_belief_agreeing_the_slide_is_ending_advances():
    setlist = _setlist([[[], ["a", "b", "c", "d"], ["e", "f"], ["g", "h"]]])
    config = LeadTimeSchedulerConfig(fixed_lead_time_ms=1000, transition_probability=0.7)
    scheduler = LeadTimeScheduler(setlist=setlist, config=config)
    scheduler.on_position(_at(0, 1, 0.95, t=1.0))

    projection = scheduler.project(
        TrackedPosition(
            item_idx=0, slide_idx=1, confidence=0.9, position=0.5, rate=2.0,
            t_seconds=5.0, position_spread=_spread((0.9, 0.8), (0.1, 0.2)),
        )
    )
    assert projection is not None and projection.advancing is True


def test_a_tracker_with_no_spread_uses_the_point_estimate():
    """Trackers without a state cloud, and every hand-built TrackedPosition,
    must behave exactly as before rather than silently never advancing."""
    setlist = _setlist([[[], ["a", "b", "c", "d"], ["e", "f"], ["g", "h"]]])
    config = LeadTimeSchedulerConfig(fixed_lead_time_ms=1000, transition_probability=0.7)
    scheduler = LeadTimeScheduler(setlist=setlist, config=config)

    projection = scheduler.project(
        TrackedPosition(item_idx=0, slide_idx=1, confidence=0.9, position=0.95,
                        rate=2.0, t_seconds=5.0)
    )
    assert projection is not None and projection.advancing is True


def test_the_distributional_rule_can_be_turned_off():
    setlist = _setlist([[[], ["a", "b", "c", "d"], ["e", "f"], ["g", "h"]]])
    config = LeadTimeSchedulerConfig(fixed_lead_time_ms=1000, transition_probability=None)
    scheduler = LeadTimeScheduler(setlist=setlist, config=config)

    # Spread says "not ending"; the point estimate says it is. Off means the
    # point estimate wins.
    projection = scheduler.project(
        TrackedPosition(
            item_idx=0, slide_idx=1, confidence=0.9, position=0.95, rate=2.0,
            t_seconds=5.0, position_spread=_spread((0.0, 1.0)),
        )
    )
    assert projection is not None and projection.advancing is True


# --- the derived lead --------------------------------------------------------
#
# `lead_time_ms` looked like a preference and was not one. The lead that lands
# a slide on the beat is `delivery_lag + gap_to_next_update + target_margin`,
# and the first two are measurements that move continuously.


def test_the_lead_is_the_lag_plus_the_gap_plus_the_margin():
    scheduler = LeadTimeScheduler(
        setlist=_long_song(), config=LeadTimeSchedulerConfig(target_margin_ms=500)
    )
    scheduler.observed_lag_seconds = 2.0
    # Two beliefs a second apart establish the sampling interval.
    scheduler.on_position(_at(0, 1, 0.9, t=10.0))
    scheduler.on_position(_at(0, 1, 0.9, t=11.0))

    assert scheduler.effective_lead_seconds() == pytest.approx(2.0 + 1.0 + 0.5)


def test_the_lead_follows_a_pipeline_that_slows_down():
    """The point of deriving it. A pass that overruns widens both the lag and
    the gap, and those are exactly the moments a fixed lead is most wrong."""
    scheduler = LeadTimeScheduler(setlist=_long_song())
    scheduler.observed_lag_seconds = 2.0
    scheduler.on_position(_at(0, 1, 0.9, t=10.0))
    scheduler.on_position(_at(0, 1, 0.9, t=11.0))
    before = scheduler.effective_lead_seconds()

    scheduler.observed_lag_seconds = 4.0
    assert scheduler.effective_lead_seconds() > before


def test_an_unmeasured_lag_falls_back_rather_than_assuming_zero():
    """Zero lag would mean the scheduler believed it was acting instantly,
    which is the assumption that made everything late in the first place."""
    scheduler = LeadTimeScheduler(setlist=_long_song())
    assert scheduler.observed_lag_seconds is None
    assert scheduler.effective_lead_seconds() >= scheduler.config.fallback_lag_seconds


def test_a_wild_latency_reading_cannot_run_away():
    scheduler = LeadTimeScheduler(setlist=_long_song())
    scheduler.observed_lag_seconds = 600.0
    assert scheduler.effective_lead_seconds() == scheduler.config.max_lead_seconds


def test_a_negative_margin_lands_the_slide_late_on_purpose():
    """A legitimate preference the old knob could not express at all."""
    scheduler = LeadTimeScheduler(
        setlist=_long_song(), config=LeadTimeSchedulerConfig(target_margin_ms=-1500)
    )
    scheduler.observed_lag_seconds = 3.0
    assert scheduler.effective_lead_seconds() == pytest.approx(1.5)


def test_a_single_long_gap_does_not_multiply_the_lead():
    """A silent passage should widen the lead a little, not blow it up."""
    scheduler = LeadTimeScheduler(setlist=_long_song())
    scheduler.observed_lag_seconds = 1.0
    for t in (10.0, 11.0, 12.0, 13.0):
        scheduler.on_position(_at(0, 1, 0.9, t=t))
    steady = scheduler.effective_lead_seconds()

    scheduler.on_position(_at(0, 1, 0.9, t=25.0))  # 12 s of silence
    assert scheduler.effective_lead_seconds() < steady + 1.0


# --- looking past a slide too short to hit -----------------------------------


def test_the_projection_walks_past_a_slide_shorter_than_the_lead():
    """The `<4s` bucket, structurally. With a ~4 s lead and a 2 s slide, the
    band passes through it before the screen was ever going to move — landing
    it on time would mean committing before the previous slide began."""
    # 4-word slides at 2 w/s = 2 s each, against a 4.5 s lead.
    setlist = _setlist([[[], ["a"] * 4, ["b"] * 4, ["c"] * 4, ["d"] * 4, ["e"] * 4]])
    scheduler = LeadTimeScheduler(
        setlist=setlist, config=LeadTimeSchedulerConfig(fixed_lead_time_ms=4500)
    )
    projection = scheduler.project(_at(0, 1, 0.9, t=10.0, position=0.95, rate=2.0))

    assert projection is not None
    # 0.1 s left of the current slide, then a whole 2 s slide fits inside the
    # remaining 4.4 s of lead before `max_lookahead_slides` stops the walk.
    assert projection.slides_ahead == 2
    assert projection.target_slide == 3


def test_long_slides_are_still_a_single_step():
    """The walk must not run ahead when the next slide genuinely outlasts the
    lead — that would be the over-anticipation this is not meant to add."""
    setlist = _setlist([[[], ["a"] * 4, ["b"] * 40, ["c"] * 40]])
    scheduler = LeadTimeScheduler(
        setlist=setlist, config=LeadTimeSchedulerConfig(fixed_lead_time_ms=4500)
    )
    projection = scheduler.project(_at(0, 1, 0.9, t=10.0, position=0.95, rate=2.0))
    assert projection is not None and projection.slides_ahead == 1


def test_the_walk_is_capped():
    """Every hop past the first rests on the predicted duration of a slide
    nobody has started singing, so the extrapolation is bounded."""
    setlist = _setlist([[[], *([["a"]] * 12)]])  # one-word slides
    scheduler = LeadTimeScheduler(
        setlist=setlist,
        config=LeadTimeSchedulerConfig(fixed_lead_time_ms=9000, max_lookahead_slides=3),
    )
    projection = scheduler.project(_at(0, 1, 0.9, t=10.0, position=0.99, rate=2.0))
    assert projection is not None and projection.slides_ahead <= 3


def test_the_walk_stops_where_order_stops_predicting():
    setlist = _setlist([[[], ["a"] * 4, ["b"] * 4, ["c"] * 4, ["d"] * 4, ["e"] * 4]])
    scheduler = LeadTimeScheduler(
        setlist=setlist, config=LeadTimeSchedulerConfig(fixed_lead_time_ms=4500)
    )
    # The band has left slide 2 backwards before, so what follows it is not safe.
    scheduler.progress.note_commit(0, 2)
    scheduler.progress.note_commit(0, 1)
    projection = scheduler.project(_at(0, 1, 0.9, t=10.0, position=0.95, rate=2.0))
    assert projection is not None and projection.target_slide == 2


# --- the watchdog on the audio clock -----------------------------------------
#
# It was evaluated only inside `on_position`, so it ran on the very clock that
# had stalled: no transcript means no belief means nothing evaluated, and a
# display frozen *because evidence stopped* was exactly the case it could not
# see. Traced live — 17.5 s with zero transcript events while the belief sat
# stable on a slide the screen was not showing.


def test_the_watchdog_fires_with_no_new_belief_at_all():
    scheduler = LeadTimeScheduler(setlist=_long_song())
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None
    # One belief puts slide 3 on the table, too unconfident to commit.
    assert scheduler.on_position(_at(0, 3, 0.25, t=2.0)) is None

    # Now the transcript stops. Audio keeps flowing, so the clock advances.
    assert scheduler.tick(10.0) is None
    decision = scheduler.tick(20.0)
    assert decision is not None and decision.slide_idx == 3
    # Stamped on the clock that noticed, not on the stale belief's.
    assert decision.t_seconds == 20.0


def test_a_tick_before_any_belief_does_nothing():
    scheduler = LeadTimeScheduler(setlist=_long_song())
    assert scheduler.tick(99.0) is None


def test_the_watchdog_will_not_act_on_an_ancient_belief():
    """It fires on a belief nobody has refreshed, which is the point — but one
    from two minutes ago describes a service that has moved on."""
    scheduler = LeadTimeScheduler(setlist=_long_song())
    scheduler.on_position(_at(0, 1, 0.95, t=1.0))
    scheduler.on_position(_at(0, 3, 0.25, t=2.0))

    assert scheduler.tick(500.0) is None


def test_ticking_does_not_disturb_the_normal_path():
    """The tick runs on a cadence whether or not words arrive, so it must not
    consume or reset anything the transcript path depends on."""
    scheduler = LeadTimeScheduler(setlist=_long_song())
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None
    for t in (2.0, 3.0, 4.0):
        scheduler.tick(t)
    # A confident belief still commits normally afterwards.
    decision = scheduler.on_position(_at(0, 2, 0.95, t=5.0))
    assert decision is not None and decision.slide_idx == 2


def test_a_tick_never_fires_when_the_screen_already_agrees():
    scheduler = LeadTimeScheduler(setlist=_long_song())
    assert scheduler.on_position(_at(0, 1, 0.95, t=1.0)) is not None
    for t in range(2, 60):
        assert scheduler.tick(float(t)) is None


def test_a_passage_read_aloud_takes_its_own_margin():
    """Spoken words reach the engine sooner than sung ones, so a reading's
    slides land on the singing lead minus `spoken_margin_ms`."""
    words = [["a"] * 20, ["b"] * 20]
    content = SetlistContent(items=[
        SetlistItemContent(item_idx=0, song_id="song", slides=words),
        SetlistItemContent(item_idx=1, song_id="item_reading", slides=words, spoken=True),
    ])
    scheduler = LeadTimeScheduler(
        setlist=build_index(content),
        config=LeadTimeSchedulerConfig(fixed_lead_time_ms=None, spoken_margin_ms=-3000),
    )
    scheduler.observed_lag_seconds = 4.0
    sung = scheduler.effective_lead_seconds(0)
    spoken = scheduler.effective_lead_seconds(1)
    assert sung == pytest.approx(4.0)
    assert spoken == pytest.approx(1.0)
    assert scheduler.effective_lead_seconds() == pytest.approx(4.0)
    # never below the floor, however early the spoken margin pulls it
    scheduler.observed_lag_seconds = 1.0
    assert scheduler.effective_lead_seconds(1) == pytest.approx(scheduler.config.min_lead_seconds)
