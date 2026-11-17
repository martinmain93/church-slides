"""Deciding whether anyone is singing the words on the screen.

This gates blanking the display during a service, so the failure that matters is
a false alarm: blanking mid-song is far worse than leaving a stale slide up a
few seconds longer. The measured operating point is 1.3% false alarms against
79-98% of sustained speech caught; see `decision/singing.py` for the workings.
"""

from __future__ import annotations

from auto_presenter.decision.singing import SingingDetector, talking_score

#: Word counts per event that put the rate either side of the boundary. Sung
#: worship runs about 1.25 words/s over a 4 s window, talking about 2.75.
SUNG_WORDS, SPOKEN_WORDS = 1, 4
SUNG_EVIDENCE, SPOKEN_EVIDENCE = 0.30, 0.06


def _feed(detector, evidence, n, n_words=SUNG_WORDS, t0=0.0, step=1.0):
    for i in range(n):
        detector.observe(evidence, t0 + i * step, n_words)
    return detector


def _sing(detector, n=6, t0=0.0):
    return _feed(detector, SUNG_EVIDENCE, n, SUNG_WORDS, t0)


def _talk(detector, n=6, t0=0.0):
    return _feed(detector, SPOKEN_EVIDENCE, n, SPOKEN_WORDS, t0)


def _middling(detector, t0, n=3):
    """Events scoring between the two bars — 2.0 words/s at evidence 0.15
    lands at 0.58, inside the hysteresis gap. Spaced wider than the rate
    window so each event's words do not stack up in the next one's."""
    return _feed(detector, 0.15, n, 8, t0, step=5.0)


class TestTheScoreItself:
    def test_singing_scores_low_and_talking_scores_high(self):
        """The two measured medians must land either side of the thresholds,
        or nothing downstream can work."""
        assert talking_score(rate_wps=1.25, evidence=0.27) < 0.10
        assert talking_score(rate_wps=2.75, evidence=0.09) > 0.90

    def test_both_signals_move_it(self):
        """It is a compound score, not either signal wearing a disguise. Each
        one alone, with the other held at its singing value, must still push."""
        sung = talking_score(rate_wps=1.25, evidence=0.27)
        assert talking_score(rate_wps=2.75, evidence=0.27) > sung
        assert talking_score(rate_wps=1.25, evidence=0.09) > sung

    def test_a_slow_talker_is_the_known_weak_case(self):
        """Someone speaking at a sung pace can only be caught by match quality.
        Documented because it is the one speaker in five we largely miss."""
        assert talking_score(rate_wps=1.25, evidence=0.06) > 0.5


class TestNotEnoughToSayYet:
    def test_a_fresh_detector_trusts_the_screen(self):
        assert SingingDetector().singing is True

    def test_a_partial_window_does_not_fire(self):
        """Below `smooth` events there is nothing to average, so the screen
        keeps the benefit of the doubt."""
        d = SingingDetector(smooth=3)
        d.observe(SPOKEN_EVIDENCE, 5.0, SPOKEN_WORDS)
        d.observe(SPOKEN_EVIDENCE, 6.0, SPOKEN_WORDS)
        assert d.singing is True

    def test_silence_is_not_claimed_before_anything_starts(self):
        """No events yet is the app waiting, not the band stopping."""
        d = SingingDetector()
        d.tick(300.0)
        assert d.silent is False


class TestWordsThatAreNotTheseWords:
    def test_sustained_talking_stops_trusting_the_screen(self):
        d = _talk(SingingDetector(), n=8, t0=5.0)
        assert d.singing is False
        assert d.reason == "not_these_words"

    def test_singing_keeps_the_screen(self):
        d = _sing(SingingDetector(), n=20, t0=5.0)
        assert d.singing is True
        assert d.reason == "singing"

    def test_it_recovers_when_the_singing_comes_back(self):
        """A pastor finishing must un-blank the screen, not need an operator."""
        d = _talk(SingingDetector(), n=8, t0=5.0)
        assert d.singing is False
        _sing(d, n=8, t0=100.0)
        assert d.singing is True

    def test_one_bad_event_among_good_ones_is_ignored(self):
        """Whisper mishears constantly; a single miss must not blank a
        service. This is what the smoothing window is for."""
        d = _sing(SingingDetector(), n=7, t0=5.0)
        d.observe(0.0, 12.0, SUNG_WORDS)
        assert d.singing is True


class TestHysteresis:
    """Once blanked, a lower bar to stay blanked. Without this the state
    changed twice as often for no gain in either error."""

    def test_entering_needs_more_than_staying(self):
        d = SingingDetector()
        assert d.enter > d.exit

    def test_a_middling_stretch_does_not_start_blanking(self):
        """Scores between the two bars must not trip a detector that is
        currently trusting the screen."""
        d = _sing(SingingDetector(), n=6, t0=5.0)
        _middling(d, t0=20.0)
        assert d.singing is True

    def test_the_same_stretch_keeps_an_existing_blank(self):
        """The identical events, on a detector that has already blanked, must
        keep it blanked. Only the prior state differs."""
        d = _talk(SingingDetector(), n=8, t0=5.0)
        assert d.singing is False
        _middling(d, t0=20.0)
        assert d.singing is False


class TestSilence:
    def test_a_long_gap_with_no_events_reads_as_silence(self):
        """Match quality cannot help here — with nothing arriving there is
        nothing to score, so only the clock notices."""
        d = SingingDetector(silence_after_seconds=6.0)
        d.observe(0.3, 10.0, SUNG_WORDS)
        d.tick(17.0)
        assert d.silent is True
        assert d.reason == "silence"

    def test_a_short_gap_is_just_a_breath(self):
        d = SingingDetector(silence_after_seconds=6.0)
        d.observe(0.3, 10.0, SUNG_WORDS)
        d.tick(13.0)
        assert d.silent is False

    def test_a_new_event_ends_the_silence(self):
        d = SingingDetector(silence_after_seconds=6.0)
        d.observe(0.3, 10.0, SUNG_WORDS)
        d.tick(20.0)
        assert d.silent is True
        d.observe(0.3, 21.0, SUNG_WORDS)
        assert d.silent is False

    def test_silence_outranks_the_score_in_the_explanation(self):
        """Both can be true at once; the clock is the more specific reason."""
        d = _talk(SingingDetector(silence_after_seconds=6.0), n=6, t0=5.0)
        d.tick(100.0)
        assert d.reason == "silence"


def test_reset_forgets_everything():
    d = _talk(SingingDetector(), n=6, t0=5.0)
    assert d.singing is False
    d.reset()
    assert d.singing is True
    assert d.score == 0.0


class TestLastScore:
    """The wake gate for spoken instructions reads `last_score`, not `score`."""

    def test_smoothing_structurally_caps_a_short_utterance(self):
        """A two-event command cannot reach the 0.8 wake bar through `score`.

        This is arithmetic, not tuning: a rolling mean over `smooth` events
        gives a k-event burst at most k/smooth of its raw value. At the
        default smooth=3 that is 0.667 for k=2 — under the bar however
        unambiguous the speech. It is why a clearly spoken instruction read
        as "the acoustic score can't discriminate speech from singing".
        """
        det = SingingDetector()
        t = 0.0
        for _ in range(3):  # fill the window with quiet, on-book singing
            t += 1.0
            det.observe(0.9, t, n_words=4)
        assert det.last_score < 0.1

        peak_smoothed = 0.0
        for _ in range(2):  # two events of unmistakable speech
            t += 1.0
            det.observe(0.02, t, n_words=7)
            assert det.last_score > 0.9, "raw score should be unambiguous"
            peak_smoothed = max(peak_smoothed, det.score)

        assert peak_smoothed <= 2 / 3 + 1e-9
        assert peak_smoothed < 0.8, "smoothed score cannot cross the wake bar"

    def test_last_score_is_this_events_value_not_a_mean(self):
        det = SingingDetector()
        det.observe(0.9, 1.0, n_words=4)
        det.observe(0.9, 2.0, n_words=4)
        quiet = det.last_score
        det.observe(0.02, 3.0, n_words=7)
        assert det.last_score > quiet
        assert det.last_score != det.score

    def test_last_score_is_zero_before_anything_is_observed(self):
        assert SingingDetector().last_score == 0.0


class TestBlankingOnSilence:
    """Silence is noticed by the clock; the screen blanks only when it is long."""

    def test_a_long_silence_blanks_but_an_instrumental_gap_does_not(self):
        det = _sing(SingingDetector())
        det.tick(5.0 + 10.0)  # a 10 s gap between sung lines: don't blank
        assert not det.singing and not det.long_silence
        det.tick(5.0 + 14.0)  # a long instrumental break: blank
        assert det.long_silence and det.reason == "silence"

    def test_silence_is_timed_from_when_words_arrived_not_when_they_were_sung(self):
        """Words land 2-4 s after they are sung; the clock that ticks is live."""
        det = SingingDetector()
        det.tick(13.0)
        _sing(det, n=1, t0=10.0)  # sung at 10 s, arrived at 13 s
        det.tick(25.0)
        assert not det.long_silence  # 12 s since it arrived (15 s since sung)
        det.tick(27.0)
        assert det.long_silence

    def test_provisional_words_are_not_silence(self):
        """Held notes: partial transcripts keep coming, finals don't."""
        det = _sing(SingingDetector())
        for t in (13.0, 19.0, 25.0, 31.0):
            det.tick(t)
            det.heard()
        det.tick(36.0)
        assert not det.long_silence

    def test_words_arriving_again_restore_the_screen(self):
        det = _sing(SingingDetector())
        det.tick(30.0)
        assert det.long_silence
        _sing(det, n=1, t0=31.0)
        assert not det.long_silence

    def test_talking_is_not_silence(self):
        """Talking is told apart by `off_book`; whether it blanks is the
        server's call (`blank_on_talking`, off)."""
        det = _talk(SingingDetector(), n=8, t0=5.0)
        assert det.off_book and not det.long_silence
