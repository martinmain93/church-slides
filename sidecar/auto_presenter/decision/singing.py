"""Is anyone singing the words on the screen?

Today nothing asks. The tracker holds whatever slide it last committed, forever
— through the instrumental break, through the prayer, through the pastor's five
minutes between songs. The screen shows a verse nobody is on.

Two different failures wear the same face, and they need different answers:

**Nothing is arriving.** Silence, or audio the ASR finds nothing in. No
transcript events at all, so there is nothing to score — only elapsed time can
notice this. That is `silent`, and it stays a simple clock.

**Words are arriving that are not these words.** Someone is talking, or singing
a song that is not on the screen. Events keep coming, and two things about them
give it away.

Two signals, one score
----------------------

*Match quality* — the filter's `evidence` term, the mean quality of what was
heard against everything it might have been. Real singing ran a median 0.268
against 0.094 for talking.

*Word rate* — people speak far faster than they sing. Over a trailing four
seconds, sung worship ran a median 1.25 words/s against 2.75 for talking.

Measured separately over 1738 transcript events — six approved goldens, four of
the user's own recordings containing real interjections, and five public-domain
sermon readings — as a ranking of "this is talking":

    signal                       AUC   (0.50 = chance)
    word rate                   0.933
    low match quality           0.844

An earlier version OR'd a threshold on each together with a third rule. That is
the wrong shape: independent triggers compound their false alarms, and it
measured **14.6%** false alarms on the goldens — one blanked screen in seven,
mid-song. These are combined into a single score instead, weighted by a balanced
logistic fit, and the same operating point costs **1.3%**.

What was tried and rejected
---------------------------

The literature on singing-vs-speech (Sarasola et al.; the Fraunhofer SR-SAD
work) leans hard on **pitch**: singing holds a note, speech glides, so pitch
should be far steadier when sung. It does not survive our microphone. Measured
as the spread of an autocorrelation F0 track over 750 ms:

    within one recording, same voice and room     AUC 0.811   promising
    pooled across recordings, absolute            AUC 0.313   worse than chance
    pooled, normalised against each recording     AUC 0.448   chance

The published results assume an isolated vocal track. We have a room mic over a
full band, where the F0 track follows whatever instrument is loudest — during
pure singing on the goldens the "pitch spread" reached 5.69 semitones at p95,
wider than talking. Normalising against each recording's own baseline does not
rescue it, because the baseline is made of the same noise. If we ever get a
vocal-only feed, this is worth revisiting; on a room mic it is not.

How well it works
-----------------

Fitted weights come from the whole set, but every number below is held out —
the recording being measured was removed from the fit first.

    speech, per speaker held out       caught   noticed after
      Sermon on the Mount                98%        6 s
      Calvin, two sermons                98%        6 s
      Calvin on Ephesians                90%        7 s
      Spurgeon, sermon 192               79%        7 s
      Booth, Purity of Heart             25%        9 s

    worship, per golden held out       false alarms
      pooled over 1113 events              1.3%

Booth is the honest outlier: a 21 s clip read slowly enough to run 1.87 words/s,
inside the range people sing at. A deliberate speaker is the case this misses,
and match quality alone has to carry it.

Brief interjections — "okay, let's go back to verse one" — are *not* caught at
this setting, where the previous rule caught some of them. That is deliberate.
Such an aside is two or three transcript events, over before any smoothing has
filled, and buying it back cost eleven times the false-alarm rate. Leaving the
slide up through a ten-second aside is the status quo and nobody notices;
blanking the screen mid-chorus is the failure people see.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: Balanced logistic fit over (word rate, 1 - evidence), standardised. Refit by
#: `research/eval/fit_singing.py` if the recording set grows.
_BIAS = -2.1968
_W_RATE = 3.0929
_W_LOW_EVIDENCE = 2.5731
_MEAN_RATE, _SD_RATE = 1.6526, 1.3007
_MEAN_LOW_EVIDENCE, _SD_LOW_EVIDENCE = 0.7691, 0.1236


def talking_score(rate_wps: float, evidence: float) -> float:
    """How much this one event looks like talking, in 0..1."""
    z = (
        _BIAS
        + _W_RATE * (rate_wps - _MEAN_RATE) / _SD_RATE
        + _W_LOW_EVIDENCE * ((1.0 - evidence) - _MEAN_LOW_EVIDENCE) / _SD_LOW_EVIDENCE
    )
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


@dataclass
class SingingDetector:
    """Watches match quality, word rate and the clock, and says whether to
    trust the screen."""

    #: How many events the score is averaged over before it can decide. Three is
    #: about six seconds of audio: enough to ride out one bad event, short
    #: enough to notice a pastor before the congregation does.
    smooth: int = 3
    #: Mean score at which we stop trusting the screen, and the lower bar for
    #: going back. The gap is hysteresis — without it the state flickered twice
    #: as often (35 changes against 16) for no gain in either error.
    enter: float = 0.80
    exit: float = 0.40
    #: With no transcript event at all for this long, nobody is singing —
    #: match quality cannot help here because there is nothing to score.
    silence_after_seconds: float = 6.0
    #: Silence long enough to *blank the screen* over. A long instrumental
    #: break blanking is wanted (the user, 2026-10-16); the gaps between sung
    #: lines are not, and neither is a blank that lasts a second. Replayed in
    #: real time, counting every word that arrives (partial transcripts too):
    #: gaps inside songs reached 10.7 s on the 2026-10-15 set; the breaks
    #: between its parts ran 20-38 s; on the 2026-10-02 room recording 12 s
    #: blanked for 0.4 s and 1.9 s, which reads as a glitch, and 14 s does not.
    #: Measured on *arrival*: a word reaches us 2-4 s after it was sung, and
    #: the clock that ticks is the live one (`tick`) — timed from when it was
    #: sung, a 12 s bar fired after 8 s mid-song.
    blank_silence_seconds: float = 14.0
    #: The window the word rate is measured over.
    rate_window_seconds: float = 4.0

    _scores: list[float] = field(default_factory=list, init=False)
    _off_book: bool = field(default=False, init=False)
    #: Word onset times, for the rate estimate.
    _word_times: list[float] = field(default_factory=list, init=False)
    _last_event_t: float | None = field(default=None, init=False)
    #: When the latest words *arrived*, on the clock `tick` advances.
    _last_arrival: float | None = field(default=None, init=False)
    _now: float = field(default=0.0, init=False)

    def observe(self, evidence: float, t_seconds: float, n_words: int = 0) -> None:
        """Fold in one transcript event."""
        self._word_times.extend([t_seconds] * n_words)
        cutoff = t_seconds - self.rate_window_seconds
        self._word_times = [t for t in self._word_times if t >= cutoff]
        self._last_event_t = t_seconds
        self._now = max(self._now, t_seconds)
        self._last_arrival = self._now

        self._scores.append(talking_score(self.rate_wps, evidence))
        del self._scores[: -self.smooth]
        if len(self._scores) == self.smooth:
            mean = sum(self._scores) / len(self._scores)
            self._off_book = mean >= (self.exit if self._off_book else self.enter)

    def heard(self) -> None:
        """Words arrived that will not be scored (a provisional transcript):
        not silence, whatever they turn out to be."""
        if self._last_arrival is not None:
            self._last_arrival = self._now

    def tick(self, t_seconds: float) -> None:
        """Advance the clock without an event — how silence is noticed."""
        self._now = max(self._now, t_seconds)

    def reset(self) -> None:
        self._scores.clear()
        self._off_book = False
        self._word_times.clear()
        self._last_event_t = None
        self._last_arrival = None
        self._now = 0.0

    @property
    def rate_wps(self) -> float:
        """Words per second over the trailing window."""
        if self._now < self.rate_window_seconds:
            return 0.0
        return len(self._word_times) / self.rate_window_seconds

    @property
    def score(self) -> float:
        """The smoothed talking score — for blanking and the debug panel.

        Smoothed on purpose: blanking wants hysteresis, because a black
        screen mid-chorus is the failure people notice. Do **not** use this
        to gate anything that must react to a brief utterance — see
        `last_score`.
        """
        if not self._scores:
            return 0.0
        return sum(self._scores) / len(self._scores)

    @property
    def last_score(self) -> float:
        """This event's talking score, unsmoothed.

        **No longer consumed.** It existed for the spoken-instruction wake
        gate, which is lexical now — see `decision.instructions`. The acoustic
        score could not represent a short command at all: measured live, a
        clearly spoken four-word instruction had a *ceiling* of 0.74 against a
        0.8 bar, because the rate term divides by a fixed 4 s window and a
        command spoken into a pause reads as slow. Kept because the
        distinction it draws against `score` is real and the reasoning below
        is worth not rediscovering.

        `score`'s rolling mean over `smooth` events is right for blanking and
        structurally wrong for a wake gate: a command spanning k events can
        reach at most k/smooth of its raw value, so at the default
        `smooth = 3` a two-event instruction caps at 0.667 and cannot cross
        the 0.8 wake threshold *however unambiguous the speech is*. The
        module docstring above says brief interjections are deliberately not
        caught — true and correct for blanking, and the exact opposite of
        what spoken instructions need.

        That ceiling is also what made a clearly-spoken command look like
        evidence that "the acoustic score doesn't discriminate speech from
        degraded singing": the discrimination was there, the smoothing was
        hiding it. Measured 2026-09-04 over 2511 real transcript events
        across all seven approved goldens (singing only, so every wake is a
        false one): raw scores crossed 0.8 zero times, while clearly spoken
        commands score 1.0 raw.
        """
        return self._scores[-1] if self._scores else 0.0

    @property
    def established(self) -> bool:
        """Has enough been heard to judge singing at all? Before this, the
        session has not yet shown anyone singing."""
        return len(self._scores) >= self.smooth

    @property
    def silent(self) -> bool:
        if self._last_event_t is None:
            return False        # nothing has started yet; not the same thing
        return self._now - self._last_event_t >= self.silence_after_seconds

    @property
    def long_silence(self) -> bool:
        """Nothing has arrived for `blank_silence_seconds`: blank the screen."""
        if self._last_arrival is None:
            return False
        return self._now - self._last_arrival >= self.blank_silence_seconds

    @property
    def off_book(self) -> bool:
        """Words are arriving, but they are not the ones on screen."""
        return self._off_book

    @property
    def singing(self) -> bool:
        return not (self.silent or self.off_book)

    @property
    def reason(self) -> str:
        """Why the screen is not trusted, for the UI to explain itself."""
        if self.silent:
            return "silence"
        if self.off_book:
            return "not_these_words"
        return "singing"
