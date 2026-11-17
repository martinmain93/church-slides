"""Synthetic-input tests for the four eval metrics.

Each scenario has analytically-known answers so a regression in the metric
math fires immediately.
"""

from __future__ import annotations

import pytest

from auto_presenter.eval.metrics import (
    PerceptualWeights,
    _early_weight,
    _late_weight,
    anticipation_error_ms,
    blank_cue_windows,
    derive_deviation_markers,
    identical_slide_groups,
    override_rate,
    perceptual_score,
    recovery_time_ms,
    tokenize_for_wer,
    word_error_rate,
    wrong_slide_rate,
)
from auto_presenter.eval.sung_timing import cue_timings, slide_lines
from auto_presenter.eval.types import (
    CueTiming,
    Decision,
    DeviationMarker,
    GroundTruthEvent,
    SetlistItemSnapshot,
)


def gt(t: float, item: int, slide: int) -> GroundTruthEvent:
    return GroundTruthEvent(t_seconds=t, item_idx=item, slide_idx=slide)


def dec(t: float, item: int, slide: int, source: str = "ai") -> Decision:
    return Decision(t_seconds=t, item_idx=item, slide_idx=slide, source=source)  # type: ignore[arg-type]


# --- anticipation_error_ms ---------------------------------------------------


def test_perfect_anticipation_returns_zero_offsets():
    truth = [gt(0, 0, 0), gt(10, 0, 1), gt(20, 0, 2)]
    decs = [dec(0, 0, 0), dec(10, 0, 1), dec(20, 0, 2)]
    assert anticipation_error_ms(decs, truth) == [0.0, 0.0, 0.0]


def test_late_decisions_produce_positive_offsets():
    truth = [gt(0, 0, 0), gt(10, 0, 1)]
    decs = [dec(0.5, 0, 0), dec(10.5, 0, 1)]
    result = anticipation_error_ms(decs, truth)
    assert result == [500.0, 500.0]


def test_early_anticipation_produces_negative_offsets():
    truth = [gt(0, 0, 0), gt(10, 0, 1)]
    decs = [dec(-0.5, 0, 0), dec(9.5, 0, 1)]
    result = anticipation_error_ms(decs, truth)
    assert result == [-500.0, -500.0]


def test_decision_for_wrong_slide_does_not_match():
    truth = [gt(10, 0, 1)]
    decs = [dec(10, 0, 99)]  # wrong slide
    assert anticipation_error_ms(decs, truth) == []


def test_decision_outside_window_does_not_match():
    truth = [gt(0, 0, 0)]
    decs = [dec(100, 0, 0)]  # 100s away — outside 5s window
    assert anticipation_error_ms(decs, truth) == []


def test_pairing_is_one_to_one_for_repeated_slides():
    """If the chorus is sung twice, both ground-truth events are matched
    against the closest decision each, never the same one twice."""
    truth = [gt(0, 0, 0), gt(10, 0, 0)]
    decs = [dec(0.1, 0, 0), dec(10.2, 0, 0)]
    result = anticipation_error_ms(decs, truth)
    assert result == pytest.approx([100.0, 200.0])


def test_empty_inputs_return_empty():
    assert anticipation_error_ms([], []) == []
    assert anticipation_error_ms([], [gt(0, 0, 0)]) == []
    assert anticipation_error_ms([dec(0, 0, 0)], []) == []


# --- wrong_slide_rate --------------------------------------------------------


def test_perfect_tracking_gives_zero_wrong_rate():
    truth = [gt(0, 0, 0), gt(10, 0, 1), gt(20, 0, 2)]
    decs = [dec(0, 0, 0), dec(10, 0, 1), dec(20, 0, 2)]
    rate = wrong_slide_rate(decs, truth)
    assert rate == 0.0


def test_decision_one_slide_behind_for_full_duration_gives_full_wrong_rate():
    """Ground truth advances at t=0,10,20. Decisions never advance.
    From t=10 onwards the decision is wrong."""
    truth = [gt(0, 0, 0), gt(10, 0, 1), gt(20, 0, 2)]
    decs = [dec(0, 0, 0)]  # never advances
    rate = wrong_slide_rate(decs, truth)
    # Total = 20s. Wrong from t=10 to t=20 = 10s. Plus tail (no further GT).
    # 10 / 20 = 0.5
    assert rate == 0.5


def test_decision_late_by_500ms_gives_proportional_wrong_rate():
    truth = [gt(0, 0, 0), gt(10, 0, 1), gt(20, 0, 2)]
    decs = [dec(0, 0, 0), dec(10.5, 0, 1), dec(20.5, 0, 2)]
    rate = wrong_slide_rate(decs, truth)
    # Wrong window: [10, 10.5] — 0.5s out of 20s = 0.025
    # No wrong window past 20 because we clamp to last GT.
    assert abs(rate - 0.025) < 1e-9


def test_no_decisions_means_full_wrong_rate():
    truth = [gt(0, 0, 0), gt(10, 0, 1)]
    decs: list[Decision] = []
    assert wrong_slide_rate(decs, truth) == 1.0


def test_single_ground_truth_returns_zero():
    truth = [gt(0, 0, 0)]
    decs = [dec(0, 0, 0)]
    # Need at least 2 GT events to form an interval.
    assert wrong_slide_rate(decs, truth) == 0.0


# --- recovery_time_ms --------------------------------------------------------


def test_recovery_after_chorus_repeat_marker():
    """Setup: GT shows chorus twice. Marker fires at the start of the second
    chorus; decisions catch up 2 seconds later."""
    truth = [
        gt(0, 0, 0),  # verse 1
        gt(10, 0, 1),  # chorus
        gt(20, 0, 1),  # chorus repeated (deviation)
        gt(30, 0, 2),  # verse 2
    ]
    decs = [
        dec(0, 0, 0),
        dec(10, 0, 1),
        # Stuck on chorus already, but the marker says band started chorus
        # again at t=20. The decision at t=22 is the matching one.
        dec(22, 0, 1),
        dec(30, 0, 2),
    ]
    markers = [DeviationMarker(t_seconds=20, kind="chorus_repeat")]
    result = recovery_time_ms(decs, truth, markers)
    assert result == [2000.0]


def test_no_recovery_returns_none():
    truth = [gt(0, 0, 0), gt(10, 0, 1)]
    decs = [dec(0, 0, 0)]  # never matches the second slide
    markers = [DeviationMarker(t_seconds=10, kind="jump")]
    result = recovery_time_ms(decs, truth, markers)
    assert result == [None]


def test_recovery_zero_when_decision_already_matches():
    truth = [gt(0, 0, 0), gt(10, 0, 1)]
    decs = [dec(0, 0, 0), dec(10, 0, 1)]
    markers = [DeviationMarker(t_seconds=10, kind="jump")]
    result = recovery_time_ms(decs, truth, markers)
    assert result == [0.0]


# --- override_rate -----------------------------------------------------------


def test_override_rate_per_song():
    decs = [
        dec(0, 0, 0),
        dec(5, 0, 1, source="override"),
        dec(10, 0, 2, source="override"),
        dec(15, 1, 0),  # next song
    ]
    assert override_rate(decs, n_songs=2) == 1.0


def test_override_rate_zero_with_no_overrides():
    decs = [dec(0, 0, 0), dec(10, 0, 1)]
    assert override_rate(decs, n_songs=1) == 0.0


def test_override_rate_handles_zero_songs_safely():
    decs = [dec(0, 0, 0, source="override")]
    assert override_rate(decs, n_songs=0) == 0.0


# --- word_error_rate ---------------------------------------------------------


def test_wer_zero_when_hypothesis_matches_reference():
    ref = ["amazing", "grace", "how", "sweet"]
    result = word_error_rate(ref, ref)
    assert result.wer == 0.0
    assert result.substitutions == 0
    assert result.deletions == 0
    assert result.insertions == 0


def test_wer_counts_substitutions():
    result = word_error_rate(
        ["amazing", "race", "how", "sweet"],
        ["amazing", "grace", "how", "sweet"],
    )
    assert result.substitutions == 1
    assert result.deletions == 0
    assert result.insertions == 0
    assert result.wer == 0.25


def test_wer_counts_deletions():
    result = word_error_rate(
        ["amazing", "how", "sweet"],
        ["amazing", "grace", "how", "sweet"],
    )
    assert result.substitutions == 0
    assert result.deletions == 1
    assert result.insertions == 0
    assert result.wer == 0.25


def test_wer_counts_insertions():
    result = word_error_rate(
        ["amazing", "amazing", "grace", "how", "sweet"],
        ["amazing", "grace", "how", "sweet"],
    )
    assert result.substitutions == 0
    assert result.deletions == 0
    assert result.insertions == 1
    assert result.wer == 0.25


def test_wer_mixed_edits():
    # ref: amazing grace how sweet the sound
    # hyp: amazing race how the sound (sub: race for grace; del: sweet)
    result = word_error_rate(
        ["amazing", "race", "how", "the", "sound"],
        ["amazing", "grace", "how", "sweet", "the", "sound"],
    )
    assert result.n_reference_words == 6
    assert result.n_hypothesis_words == 5
    # 1 sub + 1 del = 2 edits / 6 ref = 0.333…
    assert abs(result.wer - 2 / 6) < 1e-9
    assert result.edit_distance == 2


def test_wer_empty_hypothesis_against_nonempty_reference():
    result = word_error_rate([], ["amazing", "grace"])
    assert result.deletions == 2
    assert result.insertions == 0
    assert result.substitutions == 0
    assert result.wer == 1.0


def test_wer_nonempty_hypothesis_against_empty_reference():
    # Convention: ref-empty → wer is 1.0 (every hyp word is an insertion).
    result = word_error_rate(["spurious"], [])
    assert result.insertions == 1
    assert result.wer == 1.0
    assert result.n_reference_words == 0


def test_wer_both_empty():
    result = word_error_rate([], [])
    assert result.wer == 0.0


def test_tokenize_for_wer_matches_slide_words_tokenization():
    # Apostrophes preserved inside words, smart quotes normalized, lowercased.
    assert tokenize_for_wer("My God, my Shepherd has carried me!") == [
        "my",
        "god",
        "my",
        "shepherd",
        "has",
        "carried",
        "me",
    ]
    assert tokenize_for_wer("It’s a “great” day") == [
        "it's",
        "a",
        "great",
        "day",
    ]


# --- false lost rate ---------------------------------------------------------


def test_no_lost_episodes_is_no_false_alarms():
    from auto_presenter.eval.metrics import false_lost_rate

    assert false_lost_rate([], []) == 0.0


def test_an_episode_following_a_deviation_is_explained():
    """Lost mode is the correct response to a deviation, not a failure."""
    from auto_presenter.eval.metrics import false_lost_rate

    devs = [DeviationMarker(t_seconds=100.0, kind="chorus_repeat")]
    assert false_lost_rate([(101.5, 108.0)], devs) == 0.0


def test_an_episode_out_of_nowhere_is_a_false_alarm():
    from auto_presenter.eval.metrics import false_lost_rate

    devs = [DeviationMarker(t_seconds=100.0, kind="chorus_repeat")]
    assert false_lost_rate([(40.0, 45.0)], devs) == 1.0


def test_an_episode_before_its_deviation_is_not_explained_by_it():
    """Attribution is one-sided: detection lags the band, so an episode that
    starts first cannot have been caused by what came after."""
    from auto_presenter.eval.metrics import false_lost_rate

    devs = [DeviationMarker(t_seconds=100.0, kind="chorus_repeat")]
    assert false_lost_rate([(97.0, 99.0)], devs) == 1.0


def test_a_late_episode_is_not_credited_to_a_distant_deviation():
    from auto_presenter.eval.metrics import false_lost_rate

    devs = [DeviationMarker(t_seconds=100.0, kind="chorus_repeat")]
    assert false_lost_rate([(130.0, 140.0)], devs) == 1.0


def test_many_deviations_do_not_flatter_the_score():
    """A count, not a ratio — dividing by deviations would let a repeat-heavy
    song hide spurious entries."""
    from auto_presenter.eval.metrics import false_lost_rate

    devs = [DeviationMarker(t_seconds=float(t), kind="chorus_repeat")
            for t in (100, 200, 300)]
    episodes = [(101.0, 105.0), (201.0, 205.0), (500.0, 505.0)]
    assert false_lost_rate(episodes, devs) == 1.0


# --- deviation derivation + the forgiveness toggle ---------------------------


def _gt(*triples):
    return [
        GroundTruthEvent(t_seconds=t, item_idx=i, slide_idx=s) for t, i, s in triples
    ]


class TestDeriveDeviationMarkers:
    def test_in_order_progress_has_no_deviations(self):
        gt = _gt((0.0, 0, 1), (10.0, 0, 2), (20.0, 0, 3))
        assert derive_deviation_markers(gt) == []

    def test_a_repeat_a_skip_and_a_song_change_are_all_deviations(self):
        gt = _gt(
            (0.0, 0, 1),
            (10.0, 0, 2),
            (20.0, 0, 1),   # repeat backwards
            (30.0, 0, 5),   # skip forwards
            (40.0, 1, 1),   # different song
        )
        assert [m.t_seconds for m in derive_deviation_markers(gt)] == [20.0, 30.0, 40.0]

    def test_a_forward_skip_counts_where_the_old_hand_rolled_version_missed_it(self):
        """`test_deviation_recovery.py`'s private copy only caught
        `slide_idx <= prev`, so a band skipping ahead read as predictable."""
        gt = _gt((0.0, 0, 1), (10.0, 0, 4))
        assert len(derive_deviation_markers(gt)) == 1

    def test_the_first_cue_is_never_a_deviation(self):
        """Finding the song a service opens on is the tracker's job, and
        excusing it would hide the cold-start behaviour the opening prior
        exists to fix."""
        gt = _gt((0.0, 1, 3), (10.0, 1, 4))
        assert derive_deviation_markers(gt) == []


class TestForgiveUnpredictable:
    def test_recovering_inside_the_grace_window_scores_perfect(self):
        """The band jumps at t=10; the display advances in order (the right
        move on average), then corrects 1.5 s later. That is the system doing
        what a human operator does, and must not read as error."""
        gt = _gt((0.0, 0, 1), (10.0, 0, 5), (30.0, 0, 6))
        decisions = [
            Decision(t_seconds=0.0, item_idx=0, slide_idx=1, source="ai"),
            Decision(t_seconds=10.0, item_idx=0, slide_idx=2, source="ai"),
            Decision(t_seconds=11.5, item_idx=0, slide_idx=5, source="ai"),
            Decision(t_seconds=30.0, item_idx=0, slide_idx=6, source="ai"),
        ]
        assert wrong_slide_rate(decisions, gt) == 0.0
        # The same run is charged under the strict measure.
        assert wrong_slide_rate(decisions, gt, forgive_unpredictable=False) > 0.0

    def test_failing_to_recover_after_the_window_still_counts(self):
        """Forgiveness is bounded. Past the grace the screen is simply wrong,
        which is the whole point of a 2 s bound rather than a blanket excuse."""
        gt = _gt((0.0, 0, 1), (10.0, 0, 5), (30.0, 0, 6))
        decisions = [
            Decision(t_seconds=0.0, item_idx=0, slide_idx=1, source="ai"),
            Decision(t_seconds=10.0, item_idx=0, slide_idx=2, source="ai"),
        ]
        forgiving = wrong_slide_rate(decisions, gt)
        assert forgiving > 0.0
        # Exactly the grace window is excused, no more.
        strict = wrong_slide_rate(decisions, gt, forgive_unpredictable=False)
        assert strict > forgiving

    def test_explicit_markers_win_over_derivation(self):
        """A recording carrying hand-annotated markers knows better than the
        timeline heuristic."""
        gt = _gt((0.0, 0, 1), (10.0, 0, 5), (30.0, 0, 6))
        decisions = [Decision(t_seconds=0.0, item_idx=0, slide_idx=1, source="ai")]
        with_derived = wrong_slide_rate(decisions, gt)
        with_none = wrong_slide_rate(decisions, gt, [])
        assert with_none > with_derived


class TestForgiveIdenticalSlides:
    """Two slides with the same words are the same thing on screen."""

    def _content(self):
        return [
            SetlistItemSnapshot(
                item_idx=0,
                song_id="s",
                slides=[
                    [],
                    ["verse", "one"],
                    ["you", "came"],   # 2
                    ["bridge"],
                    ["you", "came"],   # 4 — identical to 2
                ],
            )
        ]

    def test_identical_slide_groups_pairs_the_copies(self):
        groups = identical_slide_groups(self._content())
        assert groups[(0, 2)] == frozenset({2, 4})
        assert groups[(0, 4)] == frozenset({2, 4})
        assert groups[(0, 1)] == frozenset({1})

    def test_blank_slides_are_not_all_identical_to_each_other(self):
        """A wordless lead-in must not be pooled with every other blank —
        that would make "nothing on screen" match any other blank slide."""
        content = [
            SetlistItemSnapshot(item_idx=0, song_id="s", slides=[[], ["a"], []])
        ]
        groups = identical_slide_groups(content)
        assert groups[(0, 0)] == frozenset({0})
        assert groups[(0, 2)] == frozenset({2})

    def test_showing_the_other_copy_is_not_charged(self):
        gt = _gt((0.0, 0, 1), (10.0, 0, 2), (20.0, 0, 3))
        # The screen sits on slide 4 while the golden says 2 — same words.
        decisions = [
            Decision(t_seconds=0.0, item_idx=0, slide_idx=1, source="ai"),
            Decision(t_seconds=10.0, item_idx=0, slide_idx=4, source="ai"),
            Decision(t_seconds=20.0, item_idx=0, slide_idx=3, source="ai"),
        ]
        forgiving = wrong_slide_rate(
            decisions, gt, setlist_content=self._content(), forgive_unpredictable=False
        )
        exact = wrong_slide_rate(
            decisions,
            gt,
            setlist_content=self._content(),
            forgive_unpredictable=False,
            forgive_identical_slides=False,
        )
        assert forgiving == 0.0
        assert exact > 0.0

    def test_without_setlist_content_it_cannot_help_and_says_so_by_doing_nothing(self):
        """No snapshot, no way to know two slides match — falls back to exact
        indices rather than guessing."""
        gt = _gt((0.0, 0, 1), (10.0, 0, 2), (20.0, 0, 3))
        decisions = [
            Decision(t_seconds=0.0, item_idx=0, slide_idx=1, source="ai"),
            Decision(t_seconds=10.0, item_idx=0, slide_idx=4, source="ai"),
        ]
        assert wrong_slide_rate(decisions, gt, forgive_unpredictable=False) > 0.0

    def test_a_genuinely_different_slide_is_still_wrong(self):
        gt = _gt((0.0, 0, 1), (10.0, 0, 2), (20.0, 0, 3))
        decisions = [
            Decision(t_seconds=0.0, item_idx=0, slide_idx=1, source="ai"),
            Decision(t_seconds=10.0, item_idx=0, slide_idx=3, source="ai"),
        ]
        assert wrong_slide_rate(
            decisions, gt, setlist_content=self._content(),
            forgive_unpredictable=False,
        ) > 0.0


class TestBlankCues:
    """Cues asking for a wordless slide are not the word tracker's to answer."""

    def _content(self):
        return [
            SetlistItemSnapshot(
                item_idx=0, song_id="s", slides=[[], ["verse", "one"], ["verse", "two"]]
            )
        ]

    def test_windows_span_from_the_blank_cue_to_the_next_one(self):
        gt = _gt((0.0, 0, 1), (10.0, 0, 0), (25.0, 0, 2))
        assert blank_cue_windows(gt, self._content()) == [(10.0, 25.0)]

    def test_blank_time_is_not_charged_by_default(self):
        """`ForwardFilter` only ever occupies slides with words, so it cannot
        produce slide 0 at all; blanking is `blank_when_idle`'s job and ships
        off. Charging for it puts a floor under the metric that no tracker
        change can move."""
        gt = _gt((0.0, 0, 1), (10.0, 0, 0), (25.0, 0, 2))
        decisions = [
            Decision(t_seconds=0.0, item_idx=0, slide_idx=1, source="ai"),
            Decision(t_seconds=25.0, item_idx=0, slide_idx=2, source="ai"),
        ]
        assert wrong_slide_rate(
            decisions, gt, setlist_content=self._content(),
            forgive_unpredictable=False,
        ) == 0.0

    def test_score_blank_cues_restores_the_old_behaviour(self):
        gt = _gt((0.0, 0, 1), (10.0, 0, 0), (25.0, 0, 2))
        decisions = [
            Decision(t_seconds=0.0, item_idx=0, slide_idx=1, source="ai"),
            Decision(t_seconds=25.0, item_idx=0, slide_idx=2, source="ai"),
        ]
        assert wrong_slide_rate(
            decisions, gt, setlist_content=self._content(),
            forgive_unpredictable=False, score_blank_cues=True,
        ) > 0.0

    def test_real_slides_around_a_blank_cue_are_still_scored(self):
        """Only the blank span is excused — being wrong on either side of it
        still counts, so this cannot be used to hide ordinary error."""
        gt = _gt((0.0, 0, 1), (10.0, 0, 0), (25.0, 0, 2))
        decisions = [
            Decision(t_seconds=0.0, item_idx=0, slide_idx=2, source="ai"),  # wrong
        ]
        assert wrong_slide_rate(
            decisions, gt, setlist_content=self._content(),
            forgive_unpredictable=False,
        ) > 0.0


class TestPerceptualScore:
    """Weights encode perception, so the tests pin the *shape* of the
    judgement — not particular constants, which are meant to be tunable."""

    def _content(self):
        return [
            SetlistItemSnapshot(
                item_idx=0, song_id="a",
                slides=[[], ["one"], ["two"], ["three"], ["four"], ["five"], ["six"]],
            ),
            SetlistItemSnapshot(item_idx=1, song_id="b", slides=[[], ["other", "song"]]),
        ]

    #: Cues at 0/10/20 s; the band sings each slide's first word 0.5 s after
    #: its cue, and each slide's last line starts 3 s before the next word.
    def _timed(self):
        gt = _gt((0.0, 0, 1), (10.0, 0, 2), (20.0, 0, 3))
        tm = [CueTiming(c, c + 0.5, c + 0.5 - 3.0, True, True) for c in (0.0, 10.0, 20.0)]
        return gt, tm

    def _score(self, decisions, gt, tm=None):
        return perceptual_score(decisions, gt, self._content(), timings=tm)

    def test_switching_anywhere_in_the_last_line_is_free(self):
        """The whole point of anticipation: once the band is on the last line,
        the next slide is what the room should be reading."""
        gt, tm = self._timed()
        for t in (7.5, 8.5, 10.0, 10.4):  # last line 7.5..10.5
            b = self._score([dec(0.0, 0, 1), dec(t, 0, 2), dec(20.5, 0, 3)], gt, tm)
            assert b.early_penalty == 0.0, t
            assert b.score == 0.0, t

    def test_switching_before_the_last_line_is_charged_without_a_cliff(self):
        gt, tm = self._timed()
        slightly = self._score([dec(0.0, 0, 1), dec(7.2, 0, 2), dec(20.5, 0, 3)], gt, tm)
        well = self._score([dec(0.0, 0, 1), dec(2.0, 0, 2), dec(20.5, 0, 3)], gt, tm)
        assert 0.0 < slightly.early_penalty < 0.3
        assert well.early_penalty > slightly.early_penalty * 10
        w = PerceptualWeights()
        assert _early_weight(0.0, w) == 0.0
        assert 0.0 < _early_weight(w.early_ramp_seconds / 2, w) < 1.0
        assert _early_weight(99.0, w) == 1.0

    def test_holding_the_slide_until_the_first_word_is_free(self):
        """A click before the band gets there is the operator's habit, not the
        band's: until the new slide's first word, the old slide is right."""
        gt, tm = self._timed()
        b = self._score([dec(0.0, 0, 1), dec(10.5, 0, 2), dec(20.5, 0, 3)], gt, tm)
        assert b.score == 0.0

    def test_lateness_is_mild_for_a_second_then_full(self):
        gt, tm = self._timed()
        w = PerceptualWeights()
        a_little = self._score([dec(0.0, 0, 1), dec(11.0, 0, 2), dec(20.5, 0, 3)], gt, tm)
        a_lot = self._score([dec(0.0, 0, 1), dec(13.5, 0, 2), dec(20.5, 0, 3)], gt, tm)
        assert a_little.late_penalty == pytest.approx(0.5 * w.late_small_weight)
        assert a_lot.late_penalty == pytest.approx(
            w.late_grace_seconds * w.late_small_weight + 2.0 * w.late_weight
        )
        assert _late_weight(-0.1, w) == 0.0
        assert _late_weight(0.5, w) == w.late_small_weight
        assert _late_weight(5.0, w) == w.late_weight

    def test_a_second_late_costs_more_than_a_second_early_in_the_last_line(self):
        """The asymmetry the model exists for: on a borderline call, early."""
        gt, tm = self._timed()
        early = self._score([dec(0.0, 0, 1), dec(9.0, 0, 2), dec(20.5, 0, 3)], gt, tm)
        late = self._score([dec(0.0, 0, 1), dec(12.0, 0, 2), dec(20.5, 0, 3)], gt, tm)
        assert early.score == 0.0 < late.score

    def test_without_timings_the_cue_stands_in_for_the_first_word(self):
        gt = _gt((0.0, 0, 1), (10.0, 0, 2), (20.0, 0, 3))
        w = PerceptualWeights()
        on_time = self._score([dec(0.0, 0, 1), dec(10.0, 0, 2), dec(20.0, 0, 3)], gt)
        inside = self._score([dec(0.0, 0, 1), dec(10.0 - w.assumed_last_line_seconds / 2, 0, 2),
                              dec(20.0, 0, 3)], gt)
        assert on_time.score == 0.0
        assert inside.score == 0.0
        # The old metric charges the same anticipation as fully wrong.
        assert wrong_slide_rate([dec(0.0, 0, 1), dec(9.0, 0, 2)], gt,
                                setlist_content=self._content(),
                                forgive_unpredictable=False) > 0.0

    def test_an_adjacent_slide_costs_less_than_a_distant_one(self):
        """Away from a slide change, one slide out is the mildest kind of
        wrong — and the one a binary metric most over-charges. (Slides 1 and 5
        are this cue's neighbours in the *song order*, so slide 2 next to the
        wanted 3 is index-adjacent but neither late nor early.)"""
        gt = _gt((0.0, 0, 1), (10.0, 0, 3), (20.0, 0, 5))
        adjacent = [dec(0.0, 0, 1), dec(10.0, 0, 2)]
        distant = [dec(0.0, 0, 1), dec(10.0, 0, 6)]
        a = self._score(adjacent, gt).score
        d = self._score(distant, gt).score
        assert 0 < a < d
        assert wrong_slide_rate(
            adjacent, gt, setlist_content=self._content(), forgive_unpredictable=False
        ) == wrong_slide_rate(
            distant, gt, setlist_content=self._content(), forgive_unpredictable=False
        )

    def test_the_wrong_song_is_never_mild(self):
        gt = _gt((0.0, 0, 1), (10.0, 0, 3), (20.0, 0, 5))
        wrong_song = [dec(0.0, 0, 1), dec(10.0, 1, 1)]
        adjacent = [dec(0.0, 0, 1), dec(10.0, 0, 2)]
        assert self._score(wrong_song, gt).score > self._score(adjacent, gt).score

    def test_flicker_is_counted_even_when_the_screen_ends_up_right(self):
        """A fast wrong->right correction can *improve* `wrong_slide_rate`
        while looking broken in the room. It needs its own term."""
        gt = _gt((0.0, 0, 1), (10.0, 0, 2), (20.0, 0, 3))
        steady = [dec(0.0, 0, 1), dec(10.0, 0, 2)]
        twitchy = [*steady, dec(10.4, 0, 3), dec(10.8, 0, 2)]
        assert self._score(steady, gt).flicker_count == 0
        assert self._score(twitchy, gt).flicker_count >= 2
        assert self._score(twitchy, gt).score > self._score(steady, gt).score

    def test_a_perfect_run_scores_zero(self):
        gt, tm = self._timed()
        b = self._score([dec(0.0, 0, 1), dec(10.0, 0, 2), dec(20.0, 0, 3)], gt, tm)
        assert b.score == 0.0

    def test_the_breakdown_accounts_for_the_score(self):
        """Decomposed on purpose — a single number invites tuning against the
        number rather than fixing the thing it is measuring."""
        gt = _gt((0.0, 0, 1), (10.0, 0, 2), (20.0, 0, 3))
        b = self._score([dec(0.0, 0, 5)], gt)
        assert b.scored_seconds > 0
        assert b.far_seconds > 0
        assert "perceptual score" in b.render()


class TestSungTiming:
    """Where the band sang each cue, from transcript words."""

    def _content(self):
        return [
            SetlistItemSnapshot(item_idx=0, song_id="a", slides=[
                [],
                "let the light of the morning be the lamp where i walk oh it is my song".split(),
                "let the light of the morning be the shelter where i rest oh it is my song".split(),
            ]),
        ]

    def _words(self, text: str, t0: float, step: float = 0.5):
        return [(t0 + k * step, t0 + k * step + 0.4, w) for k, w in enumerate(text.split())]

    def test_first_word_and_last_line_are_found_at_the_junction(self):
        """Both slides open "let the light of the morning": the junction with the
        end of the previous slide is what tells the two apart."""
        content = self._content()
        s1 = "let the light of the morning be the lamp where i walk oh it is my song"
        s2 = "let the light of the morning be the shelter where i rest oh it is my song"
        words = self._words(s1, 10.0) + self._words(s2, 20.0)
        gt = _gt((9.0, 0, 1), (19.5, 0, 2))
        lines = {(0, 1): [6, 6, 5], (0, 2): [6, 6, 5]}
        tm = cue_timings(gt, content, words, lines)
        assert tm[1].first_word_measured and tm[1].first_word == pytest.approx(20.0)
        # "oh it is my song" is word 12 of slide 1: 10 + 12 * 0.5
        assert tm[1].last_line_measured and tm[1].last_line == pytest.approx(16.0)

    def test_unheard_cues_are_estimated_from_the_recordings_own_offset(self):
        content = self._content()
        s1 = "let the light of the morning be the lamp where i walk oh it is my song"
        words = self._words(s1, 10.0)  # slide 2 never transcribed
        gt = _gt((9.0, 0, 1), (19.5, 0, 2))
        tm = cue_timings(gt, content, words, {(0, 1): [6, 6, 5]})
        assert tm[0].first_word_measured  # +1.0 s after its click
        assert not tm[1].first_word_measured
        assert tm[1].first_word == pytest.approx(20.5)
        # slide 1's last line was heard, so that end is still measured
        assert tm[1].last_line_measured and tm[1].last_line == pytest.approx(16.0)

    def test_line_counts_come_from_the_library_text(self):
        content = self._content()
        body = ("Let the light of the morning\nBe the lamp where I walk\nOh, it is my song\n\n"
                "Let the light of the morning\nBe the shelter where I rest\nOh, it is my song")
        assert slide_lines(content, {"a": body}) == {(0, 1): [6, 6, 5], (0, 2): [6, 6, 5]}
