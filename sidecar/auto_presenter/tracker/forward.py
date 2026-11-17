"""A dense forward filter over word positions.

The particle filter samples a posterior. This one *computes* it, and the reason
that is affordable is easy to miss: a song is 12-17 slides of 10-24 words, so
the entire state space is 150-400 word positions. A forward recursion over that
with a handful of transitions each is a few thousand multiplies per observed
word — cheaper than the 500-2000 particles it replaces.

Everything that has been fought with for weeks falls out of density rather than
being engineered:

  - **Parallel hypotheses are free.** Every position always carries mass, so a
    band jumping to a chorus finds a hypothesis already tracking it. Scouting
    landings with particles measured worse than not bothering precisely because
    each hypothesis cost samples from a fixed budget (Plan/LITERATURE.md); here
    there is no budget to spend.
  - **Jumps are transitions.** Evaluated every step at no extra cost, which is
    what JumpDTW and Nakamura et al. do and what `RelocalizeController`'s
    search-after-detection approximates expensively.
  - **Tempo needs no state.** How long the band lingers on a word is the
    self-transition probability, so a verse at 0.94 words/s and a refrain at
    0.48 are the same model with different dwell. There is no rate to estimate,
    no EMA to oscillate, and no span to size from it — three separate measured
    failures that were all really one missing dwell distribution.
  - **It is deterministic.** No seed, so a difference between two runs is a
    real difference. Every particle-filter number in this repo is a median over
    5-8 seeds for that reason.

What it does *not* do is change the observation. The literature is
frame-synchronous at 40 ms because it has a phoneme posterior; we have Whisper
words at roughly 1 Hz, and a spike showed a phoneme model does far worse on our
sung, full-mix audio. So this takes the half that transfers — dense recursive
accumulation — and leaves the observation rate alone.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.index.phonetic import phonetic_match_score
from auto_presenter.index.types import IndexedSetlist
from auto_presenter.tracker.types import BeliefState, Particle


@dataclass(frozen=True)
class ForwardConfig:
    """Transition and observation shape for the forward filter."""

    #: Advance one word.
    advance: float = 0.33
    #: Stay on the same word. This *is* the tempo model: a slow passage simply
    #: self-transitions more often, and nothing estimates a rate.
    #:
    #: Swept over 0.35/0.45/0.55/0.65 against jump mass on all four goldens.
    #: It wants to be **high** — much higher than the "advance is the common
    #: case" intuition suggests — because the observation stream is not one
    #: word per sung word. Whisper inserts (29 and 15 insertions on the two
    #: measured recordings) and the sliding window re-reports, so many
    #: observations carry no forward progress at all. A low `stay` forces the
    #: posterior forward on every one of them and it runs ahead of the band.
    stay: float = 0.55
    #: Skip a word: an ASR word never seen, or entering mid-word.
    skip: float = 0.044
    #: Total mass on structural jumps, shared across landings. This is the
    #: whole recovery mechanism — a repeat is a transition evaluated every
    #: step, not a search begun after a loss is noticed. 0.08 measured best;
    #: above ~0.15 the posterior smears and everything degrades.
    jump: float = 0.08
    #: Leave this song for another in the setlist, per observation, shared
    #: across every other song's opening.
    #:
    #: Small, because leaving a song is far rarer than moving within one — a
    #: set has a handful of songs and dozens of slides — but non-zero, because
    #: before this the tracker could not leave a song *at all*: its state was
    #: scoped to one item and no evidence could move it. Reported from live use
    #: as singing whole verses of another song in the set and watching nothing
    #: happen.
    #: Lowered 0.02 -> 0.005 once the scheduler grew a cross-song *commit*
    #: guard. This mass exists so the belief *can* leave a song; the guard is
    #: what stops it doing so carelessly, so the transition no longer has to
    #: be large enough to force the issue on its own. At 0.02 it was a
    #: standing tax on every other hypothesis — see `_compute_snapshot`.
    #:
    #: Measured on the live recording, with the conditional marginal: one
    #: fewer delay over 3 s (4 -> 3) and the worst falling 19.8 s -> 17.1 s.
    #: Not taken to zero: before this transition existed the tracker could not
    #: leave a song at all, which was reported from live use as singing whole
    #: verses of another song and watching nothing happen. Six of the seven
    #: goldens are single-song, so the cost side of this is weakly evidenced.
    cross_song: float = 0.005
    #: Emission floor, as in `tracker.emission` — one bad word must not zero a
    #: state outright.
    emission_floor: float = 0.05
    #: How the stay probability decays with each further observation spent on
    #: the same word — the difference between a geometric dwell and an explicit
    #: duration model.
    #:
    #: A constant `stay` has no memory: a word already sung for five
    #: observations is exactly as likely to continue as one just entered. That
    #: is what let a wrong state absorb evidence indefinitely without ever being
    #: tested against the *next* word — traced on King of My Heart, where the
    #: filter sat at position 0.02-0.14 of the chorus for ten observations
    #: while the band sang a verse (Plan/AUDIO_EMISSION.md).
    #:
    #: Cont's Antescofo and Duan & Pardo both carry explicit duration rather
    #: than a self-loop for this reason (Plan/LITERATURE.md). 1.0 restores the
    #: geometric dwell.
    #: Swept 1.0 down to 0.0 on all four goldens: 0.294, 0.291, 0.290, 0.289,
    #: 0.287, 0.286, **0.285**. Monotonic, improving every recording, and 0.0
    #: is also the cheapest because fewer states stay live (1 s against 3 s for
    #: the whole four-recording evaluation).
    #:
    #: At 0.0 a word may be held for at most two observations and must then
    #: advance — the strongest form of the duration model, and a hard bound
    #: rather than a decay. That the sweep runs all the way there says the
    #: geometric self-loop was not modelling dwell so much as letting wrong
    #: states persist.
    stay_decay: float = 0.0
    #: Observations of dwell tracked before it saturates. The state space is
    #: multiplied by this, which is affordable only because it starts at
    #: 150-400 positions.
    #: 2, 3, 4 and 6 measure identically at `stay_decay` 0.3 and below —
    #: nothing survives past the first repeat — so this is the smallest value
    #: that represents the model, and it keeps the state space at 2x.
    max_dwell: int = 2
    #: Fallback words per second before enough has been observed to measure.
    nominal_rate_wps: float = 1.0
    min_rate_wps: float = 0.2
    max_rate_wps: float = 4.0
    #: ASR words per second run slightly ahead of *slide* words per second,
    #: because Whisper also transcribes things that are not on the slide —
    #: insertions, mishearings, a word caught twice across a window boundary.
    #: Measured over the seven approved goldens on live-windowed MLX
    #: transcripts: transcript rate reads a median 1.34 w/s against a true sung
    #: 1.15 w/s, so the scale brings the median onto the truth. It is a
    #: correction for a known bias, not a tuning knob — if the ASR path
    #: changes materially, re-measure it rather than adjusting it to taste.
    transcript_rate_scale: float = 0.86
    #: Seconds of history the rate estimate needs, and the window it uses.
    #: Long enough that correcting by one word does not swing it — the failure
    #: the particle filter's EMA hit repeatedly.
    rate_min_span_seconds: float = 6.0
    rate_window_seconds: float = 12.0
    #: Half-life, in seconds, of the discounted rate estimate. `None` keeps
    #: the flat sliding window above.
    #:
    #: A window has no memory beyond its edge, so the estimate knows exactly
    #: as much at the end of a song as at the start — measured over a live
    #: recording, its spread was 0.346 across the first half and 0.380 across
    #: the second. It never converges, and the scheduler divides by it.
    #:
    #: This accumulates instead: word arrivals are Poisson counts, so a Gamma
    #: posterior over the rate is conjugate and closed-form, and discounting
    #: both of its parameters each step gives graceful forgetting rather than
    #: a cliff. Evidence from 40 s ago still counts at a quarter weight with a
    #: 20 s half-life, where the window had already discarded it entirely.
    #: Tempo does drift within a song, so pure accumulation would over-commit;
    #: the half-life is what keeps it tracking.
    #: **Off by default: it works, and it does not help.** Swept on the
    #: goldens at lead 3000, the estimate converges exactly as intended — the
    #: second-half spread falls from 0.380 (window) to 0.190 at a 20 s
    #: half-life and 0.076 at 90 s — while wrong-slide stays flat to slightly
    #: worse: 0.371 window, 0.370 at 10 s, 0.375 at 20 s, 0.373 at 90 s.
    #:
    #: The rate was already unbiased (median 1.15 w/s against a true 1.15), so
    #: tightening its variance had nothing to fix. That is the useful finding:
    #: the rate is not what is limiting timing, and a better one cannot be the
    #: answer. Kept because it answers that question with evidence, and the
    #: next thing to try should not be this again.
    rate_half_life_seconds: float | None = None
    #: Posterior mass below this is dropped each step. The recursion is dense
    #: in principle; in practice almost all of it is negligible, and pruning
    #: keeps the step cost proportional to the live hypotheses rather than to
    #: the song length.
    prune: float = 1e-6


@dataclass(frozen=True)
class ForwardBelief:
    """What the filter believes after one observation."""

    item_idx: int
    slide_idx: int
    #: Posterior mass on `slide_idx`, summed over its word positions.
    marginal: float
    #: Expected progress through that slide, in `[0, 1]`.
    position: float
    #: Mass on every slide that would display the same words — see
    #: `IndexedItem.identical_by_slide`.
    display_marginal: float
    #: Mean per-word match quality of the last observation under the prior.
    evidence: float
    t_seconds: float
    #: Share of all posterior mass on `item_idx`, over every slide of it —
    #: how sure we are of the *song*. `display_marginal` is conditioned on
    #: the song and cannot say this: a seven-way tie between songs reads 0.95
    #: there and 0.14 here.
    song_share: float = 1.0


@dataclass
class ForwardFilter:
    """Dense forward recursion over `(slide, word)` states of one song."""

    setlist: IndexedSetlist
    config: ForwardConfig = field(default_factory=ForwardConfig)
    current_item_idx: int = 0

    #: state -> probability, where a state is
    #: `(item_idx, slide_idx, word_idx, dwell)` and `dwell` counts consecutive
    #: observations already spent on that word. The item is part of the state so
    #: that another song in the setlist competes on evidence like any other
    #: hypothesis; scoping this to one song meant no amount of singing could
    #: leave it.
    _alpha: dict[tuple[int, int, int, int], float] = field(
        default_factory=dict, init=False
    )
    _last_evidence: float = field(default=1.0, init=False)
    #: `(t, words observed)` samples, for the rate estimate below.
    _word_arrivals: list[tuple[float, int]] = field(default_factory=list, init=False)
    #: Gamma(alpha, beta) over the word rate: alpha counts words seen, beta
    #: seconds elapsed, both discounted. Posterior mean is alpha / beta.
    _rate_alpha: float = field(default=0.0, init=False)
    _rate_beta: float = field(default=0.0, init=False)
    _rate_last_t: float | None = field(default=None, init=False)
    #: When the first arrival landed. The warm-up check needs *elapsed* time;
    #: `_rate_beta` is discounted seconds and saturates below it, so using
    #: beta there keeps the estimate pinned at the nominal rate forever on a
    #: fast passage.
    _rate_started_t: float | None = field(default=None, init=False)
    _t: float = field(default=0.0, init=False)
    #: Memoized `_snapshot()` result, since `_alpha` (and therefore the
    #: belief it implies) does not change between the several places that
    #: read it per update — `update()` reads it twice itself, and
    #: `ForwardTracker._as_belief_state()` reads it a third time via
    #: `belief` right after. Profiled at ~13% of total matcher time,
    #: nearly all of it duplicate work recomputing the same posterior
    #: summary. Invalidated at every site that reassigns `_alpha`, `_t`,
    #: or `_last_evidence` — see `_invalidate_belief_cache`.
    _belief_cache: ForwardBelief | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._reset()

    # ----------------------------------------------------------- public API

    @property
    def belief(self) -> ForwardBelief:
        return self._snapshot()

    def _observe_rate(self, t: float, words: int) -> None:
        """Fold one arrival into the discounted Gamma posterior.

        Both parameters decay by the same factor, so the *ratio* — the rate
        estimate — is unchanged by discounting alone, while the effective
        sample size it rests on stops growing without bound. That is what
        separates this from a plain running average: it converges early, then
        holds a bounded memory instead of freezing.
        """
        half_life = self.config.rate_half_life_seconds
        if half_life is None:
            return
        if self._rate_last_t is None:
            self._rate_last_t = t
            self._rate_started_t = t
        dt = max(0.0, t - self._rate_last_t)
        self._rate_last_t = t
        decay = 0.5 ** (dt / half_life) if half_life > 0 else 0.0
        self._rate_alpha = self._rate_alpha * decay + words
        self._rate_beta = self._rate_beta * decay + dt

    @property
    def rate_wps(self) -> float:
        """Words per second, counted off the transcript.

        The model needs no rate — dwell carries tempo — but the *scheduler*
        does, for its lead-time projection: it asks how long the current slide
        has left as `(1 - position) * n_words / rate`. So a wrong rate does not
        mistrack anything; it stops the display anticipating.

        **This used to measure the posterior's own movement, and that could not
        work.** It took the expected tape position — mass-weighted over the
        states of the top song — and differenced it across a 12 s window. But
        the posterior reallocates mass between distant slides constantly (jump
        transitions alone put `jump` of the mass on word 0 of *every* sung
        slide, every step), so that series is dominated by noise rather than by
        the band: measured over the seven approved goldens, its step-to-step
        change was mean +0.40 words against a standard deviation of **11.00**,
        a signal-to-noise ratio of 0.04, moving *backwards* on 35% of steps
        while the band sang forwards. The reported rate sat on its 0.20 floor
        35% of the time against a true sung 1.15 w/s, which inflated
        `seconds_remaining` about sixfold — median 6.9 s, and only 10% of
        observations ever dropping under the 1.5 s lead time. Anticipation
        fired 76 times in 1448 observations, and when it did fire it was often
        wildly early (90th-percentile lead 9.5 s). One broken input, both
        symptoms.

        Neither a least-squares slope over the same window (0.73) nor using the
        mode instead of the mean (0.96, and noisier still at stdev 21.72)
        rescued it. The series was the problem, not the estimator.

        Counting the words the ASR actually delivered measures the same
        quantity directly and does not collapse: median 1.34 w/s before
        scaling, never once pinned at the floor, interquartile range 1.08-1.61
        against the old 0.20-1.16. `transcript_rate_scale` corrects the known
        bias between transcript words and slide words.

        Only *final* words count. Partial events re-report words that later
        finalize, so counting them would inflate the rate by however many times
        the sliding window happened to re-emit a word.
        """
        if self.config.rate_half_life_seconds is not None:
            elapsed = (
                0.0
                if self._rate_started_t is None
                else self._t - self._rate_started_t
            )
            if elapsed < self.config.rate_min_span_seconds or self._rate_beta <= 0.0:
                return self.config.nominal_rate_wps
            measured = (
                self.config.transcript_rate_scale * self._rate_alpha / self._rate_beta
            )
            return max(
                self.config.min_rate_wps,
                min(self.config.max_rate_wps, measured),
            )
        if len(self._word_arrivals) < 2:
            return self.config.nominal_rate_wps
        span = self._word_arrivals[-1][0] - self._word_arrivals[0][0]
        if span < self.config.rate_min_span_seconds:
            return self.config.nominal_rate_wps
        words = sum(n for _t, n in self._word_arrivals)
        measured = self.config.transcript_rate_scale * words / span
        return max(
            self.config.min_rate_wps,
            min(self.config.max_rate_wps, measured),
        )

    def set_current_item(self, item_idx: int) -> None:
        if 0 <= item_idx < self.setlist.item_count and item_idx != self.current_item_idx:
            self.current_item_idx = item_idx
            self._reset()

    def commit_slide(self, item_idx: int, slide_idx: int) -> None:
        """Operator override: collapse onto the start of a slide."""
        if item_idx != self.current_item_idx:
            self.set_current_item(item_idx)
        item = self.setlist.item(item_idx)
        slide = item.slide(slide_idx) if item else None
        if slide is None or not slide.words:
            return
        self._alpha = {(item_idx, slide_idx, 0, 0): 1.0}
        self._belief_cache = None

    def update(self, event: TranscriptEvent) -> ForwardBelief:
        """Fold one transcript event in, one observed word at a time."""
        if event.t_end is not None:
            self._t = event.t_end
            self._belief_cache = None
        if not event.is_final or not event.words:
            return self._snapshot()

        scores: list[float] = []
        for word in event.words:
            scores.append(self._step(word.text))
        # Tempo comes off the transcript, not the posterior — see `rate_wps`.
        # This runs after the early return for non-final events, so only
        # committed words are counted.
        self._word_arrivals.append((self._t, len(event.words)))
        cutoff = self._t - self.config.rate_window_seconds
        while len(self._word_arrivals) > 2 and self._word_arrivals[0][0] < cutoff:
            self._word_arrivals.pop(0)
        self._observe_rate(self._t, len(event.words))
        if scores:
            # Geometric mean, so events of different lengths compare — the same
            # convention `ParticleFilter.last_evidence` uses.
            self._last_evidence = math.exp(
                sum(math.log(max(1e-12, s)) for s in scores) / len(scores)
            )
            # `_last_evidence` feeds `ForwardBelief.evidence` and changed
            # after the `_snapshot()` call above already cached one — without
            # this, the belief returned below would carry the *previous*
            # observation's evidence instead of this one's.
            self._belief_cache = None
        return self._snapshot()

    # ------------------------------------------------------------ internals

    def _reset(self) -> None:
        """Start from an honest prior: every song in the set could be next.

        This used to put probability 1.0 on `current_item_idx`'s opening —
        certainty about a position nobody supplied. `StartAudioSessionRequest`
        carries no starting item, so a service beginning on anything but the
        first song began fully committed to the wrong one and had to escape at
        `cross_song` (0.02) per observation.

        Observed live on a "The Blood" / "Nothing But The Blood" medley that
        opened on the *second* song: the tracker sat on song one for ~14 s
        before the evidence dragged it across. Priming instead with a uniform
        prior over the songs' openings locks on in a single word, and takes
        that recording's wrong-slide rate 0.072 -> 0.046.

        A single-song setlist has one opening, so this is exactly the old
        behaviour there — only a real set with real ambiguity is affected.
        """
        openings = self._openings()
        if openings:
            weight = 1.0 / len(openings)
            self._alpha = {(i, s, 0, 0): weight for i, s in openings}
        else:
            self._alpha = {}
        self._last_evidence = 1.0
        self._word_arrivals.clear()
        self._rate_alpha = 0.0
        self._rate_beta = 0.0
        self._rate_last_t = None
        self._rate_started_t = None
        self._belief_cache = None

    def _sung_slides(self, item_idx: int | None = None) -> list[int]:
        item = self.setlist.item(
            self.current_item_idx if item_idx is None else item_idx
        )
        if item is None:
            return []
        return [i for i, s in enumerate(item.slides) if s.words]

    def _openings(self) -> list[tuple[int, int]]:
        """`(item_idx, slide_idx)` where each song in the setlist starts.

        Where the band lands when they change song — the first slide that has
        any words, skipping the blank lead-in the app prepends.
        """
        out = []
        for i in range(self.setlist.item_count):
            item = self.setlist.item(i)
            if item is not None and item.spoken and i != self.current_item_idx:
                # A reading is put up by the operator, never jumped into:
                # sermons quote the passage they follow. Once it is up it is
                # the current item, and its opening is where reading starts.
                continue
            sung = self._sung_slides(i)
            if sung:
                out.append((i, sung[0]))
        return out

    def _step(self, observed: str) -> float:
        """Predict, then weight by the observation. Returns the evidence.

        State is `(item, slide, word, dwell)` — the item is part of it so that
        another song in the setlist is an ordinary hypothesis competing on
        evidence, not a mode the operator has to select. Before that it was
        scoped to one song and no amount of singing could leave it.
        """
        if not self._alpha:
            return 1.0
        cfg = self.config

        # --- predict -------------------------------------------------------
        nxt: dict[tuple[int, int, int, int], float] = defaultdict(float)

        def add(state: tuple[int, int, int, int], mass: float) -> None:
            if mass > 0.0:
                nxt[state] += mass

        openings = self._openings()
        # Mass leaving for another song is pooled and spread once, rather than
        # spawning a transition per state per song. With a handful of songs and
        # a few hundred live states the difference is the step staying O(states)
        # instead of becoming O(states x songs).
        leaving = 0.0

        # `_sung_slides(item_idx)` only depends on item_idx, of which only a
        # handful are ever live at once — cache it per call instead of
        # rebuilding it from scratch for every one of the (often hundreds of)
        # live states that share the same song. Profiled at ~1s across a
        # single recording, almost all of it this exact recomputation.
        sung_by_item: dict[int, list[int]] = {}

        for (item_idx, slide_idx, word_idx, dwell), mass in self._alpha.items():
            item = self.setlist.item(item_idx)
            slide = item.slide(slide_idx) if item else None
            if slide is None or not slide.words:
                continue
            sung = sung_by_item.get(item_idx)
            if sung is None:
                sung = self._sung_slides(item_idx)
                sung_by_item[item_idx] = sung
            landings = [i for i in sung if i != 0]
            jump_share = (cfg.jump / len(landings)) if landings else 0.0
            cross = cfg.cross_song if len(openings) > 1 else 0.0
            leaving += mass * cross

            n = len(slide.words)
            # Staying gets less likely the longer we have already stayed; the
            # mass it gives up goes to advancing, so a state that has sat too
            # long is pushed forward instead of absorbing evidence forever.
            stay = cfg.stay * (cfg.stay_decay ** dwell)
            forward = max(0.0, 1.0 - stay - cfg.skip - cfg.jump - cross)
            add((item_idx, slide_idx, word_idx, min(dwell + 1, cfg.max_dwell - 1)),
                mass * stay)
            for offset, weight in ((1, forward), (2, cfg.skip)):
                target = word_idx + offset
                if target < n:
                    add((item_idx, slide_idx, target, 0), mass * weight)
                else:
                    # Off the end of this slide: carry into the next sung one.
                    following = next((i for i in sung if i > slide_idx), None)
                    if following is not None:
                        add((item_idx, following, target - n, 0), mass * weight)
                    else:
                        add((item_idx, slide_idx, n - 1, 0), mass * weight)
            if jump_share:
                for landing in landings:
                    add((item_idx, landing, 0, 0), mass * jump_share)

        if leaving > 0.0 and len(openings) > 1:
            share = leaving / (len(openings) - 1)
            here = {i for i, _, _, _ in self._alpha}
            for item_idx, slide_idx in openings:
                # Only *other* songs: mass staying put is already accounted for.
                if len(here) == 1 and item_idx in here:
                    continue
                add((item_idx, slide_idx, 0, 0), share)

        # --- observe -------------------------------------------------------
        total = 0.0
        prior_total = sum(nxt.values()) or 1.0
        evidence = 0.0
        for state, mass in nxt.items():
            item = self.setlist.item(state[0])
            slide = item.slide(state[1]) if item else None
            expected = (
                slide.words[state[2]]
                if slide and state[2] < len(slide.words)
                else ""
            )
            score = phonetic_match_score(observed, expected) if expected else 0.0
            likelihood = cfg.emission_floor + (1.0 - cfg.emission_floor) * score
            nxt[state] = mass * likelihood
            evidence += (mass / prior_total) * likelihood
            total += nxt[state]

        if total <= 0.0:
            return cfg.emission_floor
        floor = cfg.prune * total
        self._alpha = {s: m / total for s, m in nxt.items() if m >= floor}
        if not self._alpha:  # everything pruned; keep the single best state
            best = max(nxt.items(), key=lambda kv: kv[1])
            self._alpha = {best[0]: 1.0}
        self._belief_cache = None
        return evidence

    def _snapshot(self) -> ForwardBelief:
        """Cached wrapper over `_compute_snapshot()` — see `_belief_cache`."""
        if self._belief_cache is None:
            self._belief_cache = self._compute_snapshot()
        return self._belief_cache

    def _compute_snapshot(self) -> ForwardBelief:
        """The most likely `(song, slide)` and how far through it we are.

        The song is now read off the posterior rather than taken from
        `current_item_idx`, which is what lets the setlist arbitrate: whichever
        song's words are being sung accumulates mass and wins.
        """
        if not self._alpha:
            return ForwardBelief(
                item_idx=self.current_item_idx, slide_idx=0, marginal=0.0,
                position=0.0, display_marginal=0.0, evidence=self._last_evidence,
                t_seconds=self._t,
            )
        by_slide: dict[tuple[int, int], float] = {}
        weighted_word: dict[tuple[int, int], float] = {}
        for (item_idx, slide_idx, word_idx, _dwell), mass in self._alpha.items():
            key = (item_idx, slide_idx)
            by_slide[key] = by_slide.get(key, 0.0) + mass
            weighted_word[key] = weighted_word.get(key, 0.0) + mass * word_idx
        top_item, top_slide = max(by_slide, key=lambda k: by_slide[k])
        item = self.setlist.item(top_item)
        if item is None:
            return ForwardBelief(
                item_idx=self.current_item_idx, slide_idx=0, marginal=0.0,
                position=0.0, display_marginal=0.0, evidence=self._last_evidence,
                t_seconds=self._t,
            )
        slide = item.slide(top_slide)
        n = max(1, len(slide.words)) if slide else 1
        mass = by_slide[(top_item, top_slide)]
        position = (weighted_word[(top_item, top_slide)] / max(1e-12, mass)) / n
        # Slides showing the same words are interchangeable on screen, but only
        # within the same song — the same chorus in a *different* song is a
        # different thing to display.
        equivalent = item.identical_by_slide.get(top_slide) or (top_slide,)
        display = sum(by_slide.get((top_item, i), 0.0) for i in equivalent)

        # Normalize within the song, not across the setlist.
        #
        # `display_marginal` answers "how sure am I of what to put on screen",
        # and the scheduler gates commits on it. Measured globally it also
        # carries doubt about *which song* — and `cross_song` bleeds mass onto
        # every other song's opening slide on every observation, so that doubt
        # is a standing tax that never clears. Traced on a live run: while the
        # correct slide held 0.15, two other songs' opening slides held 0.24
        # between them, and the commit sat blocked under the 0.4 bar.
        #
        # Charging cross-song doubt here is now double-counting: leaving a song
        # has its own, higher bar in `LeadTimeScheduler._cross_song_ready`,
        # which is where that question belongs. Conditioning on the top item
        # leaves this measuring only what it claims to.
        #
        # Measured: wrong-slide 0.420 -> 0.409 and perceptual 0.286 -> 0.280 on
        # the goldens, with blocked observations on the live recording falling
        # from 19 to 14.
        item_mass = sum(m for (i, _s), m in by_slide.items() if i == top_item)
        total_mass = sum(by_slide.values())
        if item_mass > 0.0:
            display = min(1.0, display / item_mass)
        return ForwardBelief(
            item_idx=top_item,
            slide_idx=top_slide,
            marginal=mass,
            position=max(0.0, min(1.0, position)),
            display_marginal=display,
            evidence=self._last_evidence,
            t_seconds=self._t,
            song_share=item_mass / total_mass if total_mass > 0.0 else 0.0,
        )


@dataclass
class ForwardTracker:
    """Presents `ForwardFilter` through the interface `ParticleFilterEngine` uses.

    The point is to make the comparison a comparison *of trackers*. Measured
    standalone, the forward filter was driving a bare scheduler while the
    particle filter ran the full pipeline — hold, branch, confidence monitor —
    so some of the gap could have been those components rather than the
    tracker. Here both sit in the same seat.

    The conversion is barely a conversion: a `BeliefState` is a weighted set of
    `(slide, position)` hypotheses and so is this posterior, so each live state
    becomes one particle carrying its probability as weight. Everything
    downstream — `top_hypothesis`, `top_3`, `ess`, the pooled display marginal
    — then works unchanged.

    `RelocalizeController` is the one thing that cannot come along: it widens a
    cloud by injecting samples, and there is no sample budget here. That is not
    a gap in the comparison but the point of the design — jump transitions are
    evaluated every step, which is what relocalization approximates after a
    detection delay.
    """

    filter: ForwardFilter
    #: Below this pooled confidence the tracker is reported lost to the UI.
    #: A dense posterior cannot use the particle heuristic that would otherwise
    #: apply — see `is_lost`.
    #:
    #: Chosen by replaying the six approved goldens and splitting every update
    #: by whether the top slide was the ground-truth one (n=1021 right, 255
    #: wrong). Pooled confidence separates them; the evidence term, tried as
    #: the alternative, does not — its curve is the diagonal:
    #:
    #:                  confidence          evidence
    #:      threshold   false / caught      false / caught
    #:         0.20      0.1% /  2.4%       11.4% / 29.8%
    #:         0.35      2.2% / 18.4%       59.5% / 77.3%
    #:         0.50      5.7% / 35.7%       92.6% / 92.9%
    #:
    #: 0.35 is where the alarm still means something. Pushing it to 0.5 catches
    #: half again as much for triple the false alarms, and a lost indicator
    #: that cries wolf is the state this whole fix was undoing.
    lost_below: float = 0.35
    #: A dense posterior must not be collapsed on the engine's own commits. The
    #: particle filter re-anchors because a cloud left drifting fights the
    #: display; here a commit is a statement about the screen, and collapsing
    #: to `{(slide, 0, 0): 1.0}` discards every hypothesis the recursion has
    #: built. Operator commits still re-anchor, through `commit_slide`.
    reanchor_on_commit: bool = False

    @property
    def is_lost(self) -> bool:
        """Whether to show the operator an uncertain state.

        Needed because the fallback in `server._broadcast_particle_belief` is
        shaped for a particle cloud: it calls the tracker lost when
        `ess < 0.1 * n`, and for a cloud low ESS does mean degeneracy. For a
        dense posterior it means the opposite — mass concentrated on a few
        states is exactly what confidence looks like. Measured live on Amazing
        Grace: ESS 1.3-9.0 against 100-200 live states, so the UI read "lost"
        on 99% of updates while the correct slide sat on top at marginal
        0.88-0.98.

        Pooled confidence is the right signal here, for the same reason the
        scheduler uses it: what matters is how sure we are of *what to show*.
        """
        return self.filter.belief.display_marginal < self.lost_below

    @property
    def current_item_idx(self) -> int:
        return self.filter.current_item_idx

    @property
    def last_evidence(self) -> float:
        return self.filter._last_evidence

    @property
    def belief(self) -> BeliefState:
        return self._as_belief_state()

    def update(self, event: TranscriptEvent) -> BeliefState:
        self.filter.update(event)
        return self._as_belief_state()

    def commit_slide(self, item_idx: int, slide_idx: int) -> None:
        self.filter.commit_slide(item_idx, slide_idx)

    def set_current_item(self, item_idx: int) -> None:
        self.filter.set_current_item(item_idx)

    def reindex(self, setlist: IndexedSetlist, current_item_idx: int) -> None:
        self.filter.setlist = setlist
        self.filter.current_item_idx = max(
            0, min(current_item_idx, max(0, setlist.item_count - 1))
        )
        self.filter._reset()

    # ------------------------------------------------------------ internals

    def _as_belief_state(self) -> BeliefState:
        """Flatten the forward state into the tracker-agnostic belief.

        **Tried and reverted: folding `dwell` into position.** The state is
        `(item, slide, word, dwell)` and this keeps only `word_idx / n`, so
        position moves in steps of `1/n` and the dimension saying *how long we
        have been on this word* is dropped. Interpolating as
        `(word_idx + dwell) / n` is well-founded — with `stay_decay` at 0 a
        state at `dwell >= 1` must advance next observation — and it measured
        **worse**, by 0.008-0.013 wrong-slide against the point-estimate
        frontier at matched anticipation, across five of six operating points.

        It reads as extra progress, so the projection fires earlier, and firing
        earlier at a fixed lead is a trade already available by raising the
        lead time — without the accuracy it costs. The information is real;
        this is not the way to spend it.
        """
        particles: list[Particle] = []
        rate = self.filter.rate_wps
        for (item_idx, slide_idx, word_idx, _dwell), mass in self.filter._alpha.items():
            item = self.filter.setlist.item(item_idx)
            slide = item.slide(slide_idx) if item else None
            n = max(1, len(slide.words)) if slide and slide.words else 1
            particles.append(
                Particle(
                    item_idx=item_idx,
                    slide_idx=slide_idx,
                    position=min(1.0, word_idx / n),
                    rate=rate,
                    weight=mass,
                )
            )
        b = self.filter.belief
        return BeliefState(
            particles=particles,
            t_seconds=self.filter._t,
            display_marginal=b.display_marginal,
            song_share=b.song_share,
        )
