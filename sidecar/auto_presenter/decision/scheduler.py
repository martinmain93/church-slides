"""Lead-time scheduler — converts a particle-filter belief into commits.

Phase 6's matcher emits a `MatcherDecision` for the *next* slide
shortly before the operator would naturally advance. This is the
"anticipation" property the user actually cares about — the slide
flips at the right musical beat instead of half a second late.

Mechanics: given the belief's mode hypothesis (item, slide, position,
rate), estimate the wall-clock time at which the band will finish
the current slide:

    seconds_remaining = (1 - position) * n_words_on_slide / rate
    t_end = t_now + seconds_remaining

When `seconds_remaining < lead_time` (default 500 ms) and the next
slide hasn't already been committed, fire a `MatcherDecision` for
`(item, slide + 1)`. The decision goes through the director, which
gates on `mode` (off/shadow/active) and the `following_human`
cooldown — the scheduler itself doesn't know about those.

Edge cases:

  - **Last slide of the song**: don't auto-advance. Cross-song
    transitions are Phase 7+ work.
  - **Operator override**: the filter has been re-anchored, so the
    next belief snapshot already reflects the new slide. To avoid
    re-firing a stale "next slide" decision, the scheduler tracks
    the most recent `(item, slide)` it has committed to and only
    fires when the belief shows the cloud has actually entered that
    slide. Once on a slide, the scheduler will fire its successor.
  - **Confidence floor**: only commit when the mode's marginal
    exceeds `marginal_threshold`. A diffuse cloud is the filter's
    "I'm not sure" signal; firing in that state would just produce
    bad commits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from auto_presenter.decision.plan_gate import PlanGate
from auto_presenter.index.types import IndexedSetlist
from auto_presenter.matcher.types import MatcherDecision
from auto_presenter.tracker.progress import SongProgress
from auto_presenter.tracker.types import BeliefState

log = logging.getLogger(__name__)


@dataclass
class LeadTimeSchedulerConfig:
    """Tunable knobs for the scheduler. Exposed via the debug-panel TunablesTab."""

    #: How early the slide should land, in milliseconds. The *only* genuine
    #: preference here: positive lands it before the line, negative after.
    #:
    #: This replaced `lead_time_ms`, which was never a preference at all. The
    #: commit rule fires when the projected time to the transition falls under
    #: the lead, but that projection is on the *sung* clock while the scheduler
    #: is acting `delivery_lag` later, and once it declines it gets no further
    #: chance until the next belief update. So the lead that actually lands a
    #: slide on the beat is
    #:
    #:     delivery_lag  +  gap_to_next_update  +  target_margin
    #:
    #: of which only the last term is a choice. Measured over the goldens the
    #: first two are 2.81 s and 1.11 s at the median — 3.92 s — and 4.14 s and
    #: 2.99 s at p90, or 7.13 s. Sweeping a *fixed* lead found 4500 ms optimal,
    #: which is that median sum plus about 600 ms: the magic number was the
    #: derived one all along, and a constant is simply wrong by however much
    #: the two measurements have moved.
    #: Swept on the goldens (wrong-slide / anticipation / worst bucket):
    #:
    #:     -750   0.388  +754 ms   +909
    #:     -500   0.392  +346 ms   +409   <- best balance measured
    #:     -250   0.406  +235 ms   +346
    #:       +0   0.420  -175 ms   -464
    #:     +500   0.450  -274 ms  +1650
    #:
    #: Left at 0 — aim at the line — because that is the honest neutral for a
    #: preference, and the slider is centred there. -500 measures better on
    #: both accuracy and bucket spread, so it is worth trying live.
    target_margin_ms: int = 0
    #: Added to the margin while the slide is in a passage read aloud
    #: (`IndexedItem.spoken`). See `ParticleFilterTunables.spoken_margin_ms`.
    spoken_margin_ms: int = 0
    #: Bypass the derivation entirely and use this lead. For experiments that
    #: need a fixed value — the eval sweeps — not for production, which leaves
    #: it `None` so the lead follows the measurements.
    fixed_lead_time_ms: float | None = None
    #: Delivery lag assumed before any has been measured. The goldens' median.
    fallback_lag_seconds: float = 2.81
    #: Bounds on the derived lead, so a wild latency reading cannot make the
    #: scheduler commit slides minutes ahead or stop anticipating entirely.
    min_lead_seconds: float = 0.3
    max_lead_seconds: float = 9.0
    #: How many slides ahead the projection may walk.
    #:
    #: Swept on the goldens against `target_margin_ms` (wrong-slide, and the
    #: 4-7 s bucket):
    #:
    #:     margin   1 hop          2 hops         3 hops
    #:      -750    0.388 / +346   0.388 / +346   identical to 2
    #:      -500    0.392 / +346   0.392 / +245   identical to 2
    #:      -250    0.405 / +346   0.406 / +245   identical to 2
    #:        +0    0.414 / -204   0.420 / -204   identical to 2
    #:
    #: The third hop never fires, so 2 is the whole of the mechanism. It is
    #: accuracy-neutral where it matters and improves the 4-7 s bucket by
    #: ~100 ms; at margin 0 it costs 0.006, which is the one place a longer
    #: reach starts skipping slides rather than anticipating them.
    #:
    #: **It is not what fixed the short-slide bucket.** `<4s` moved from its
    #: long-stuck +756 ms to -175 ms, but a one-hop control does that too: the
    #: cause is the *derived* lead being long enough at last, not the walk.
    #: The walk's contribution is confined to 4-7 s.
    max_lookahead_slides: int = 2
    #: Mode marginal must exceed this before the scheduler will commit.
    #: Diffuse clouds (early in a session, after a deviation) sit below
    #: this floor and the scheduler waits.
    marginal_threshold: float = 0.4
    #: (config continues)
    #: How far behind the displayed slide the belief must be before a
    #: backward commit is allowed. 1 is unusable — that is the normal
    #: anticipation lag. See the `on_belief` backward branch.
    backward_min_slides: int = 2
    #: Backward moves demand more confidence than forward ones.
    backward_marginal_threshold: float = 0.6
    #: Leaving one song for another demands more still, and for a different
    #: reason than a backward move.
    #:
    #: Traced on a live run (Reckless Love, 2026-09-08): evidence collapsed
    #: mid-song, the marginal fell to 0.20-0.34, mass pooled onto other songs'
    #: opening slides via `ForwardConfig.cross_song`, and the instant one of
    #: them reached **0.46** the screen jumped to a different song for 2.8 s.
    #: One `marginal_threshold` was gating "advance one slide" and "abandon
    #: this song" identically, and 0.46 is a coin-flip.
    #:
    #: The asymmetry is not about confidence alone. A wrong forward step is
    #: corrected by the next line; a wrong *song* strands the congregation and
    #: takes the tracker with it. And the moment it fires is the worst
    #: possible one — mass reaches another song's opening precisely when the
    #: current song has stopped producing evidence, so the jump is most likely
    #: exactly when it is least justified.
    #: Swept on the goldens at lead 3000 (wrong-slide, guard off = 0.354):
    #:
    #:     0.50 / 2 obs   0.357      0.75 / 2 obs   0.359
    #:     0.60 / 2 obs   0.357      0.75 / 3 obs   0.361
    #:     0.60 / 3 obs   0.359      0.90 / 3 obs   0.366
    #:
    #: 0.60 / 2 blocks the live jump (a single observation at 0.46) for the
    #: smallest cost. Note the cost is carried by *one* recording — six of the
    #: seven goldens are single-song — so treat its size as weakly evidenced
    #: even though its direction is not.
    #:
    #: Raised to 0.70 / 3 on 2026-09-28, together with the word and talking
    #: gates below, after live use with only background conversation in the
    #: room produced several song jumps. Replayed on rec_20260903-115044_425
    #: (the one multi-song recording with cached words), the four genuine
    #: song changes still all happen, but land ~6-10 s after the band starts
    #: instead of ~3-5 s. That is the price; loosen here first if song
    #: changes feel sluggish.
    cross_song_marginal_threshold: float = 0.70
    #: Consecutive observations the new song must stay on top before the
    #: screen follows it. A threshold alone cannot tell a settled
    #: re-identification from a single-observation spike, which is what the
    #: live jump was: it held for one observation, then handed back.
    cross_song_min_observations: int = 3
    #: Transcribed words that must arrive while the new song holds the lead.
    #: Observations are transcript events and the median event is *one*
    #: word, so the observation count alone let two or three stray words of
    #: chatter carry the screen into another song. On the recording above,
    #: 4 keeps every genuine change; 6 already loses two of the four
    #: outright, so this is close to the ceiling. `None` disables.
    cross_song_min_words: int | None = 4
    #: Leaving a song also requires that the room sounds like singing:
    #: `SingingDetector.score` (smoothed talking score, pushed onto
    #: `LeadTimeScheduler.talking_score` by the server) must be below this.
    #: Over ~4000 sung events in the cached-transcript recordings it ran
    #: median 0.03, p90 0.29, p95 0.41, p99 0.67; clear speech sits ~0.8+.
    #: `None` disables.
    cross_song_max_talking_score: float | None = 0.5
    #: The new song's share of the *whole* belief (`TrackedPosition.song_share`)
    #: must reach this before the screen leaves the displayed song.
    #:
    #: `cross_song_marginal_threshold` gates `confidence`, which is conditioned
    #: on the song — it measures "which slide, given the song", and says
    #: nothing about whether the song is right. Traced 2026-09-29: a lone
    #: Whisper "!" left all seven songs tied at 0.14 of the belief each, and
    #: the tie-winner's conditional confidence read 0.95. This is the gate
    #: that actually asks the song question. `None` disables.
    #:
    #: Swept 2026-09-30 with live windowing (MLX small.en) on
    #: rec_20260903-115044_425, the one approved multi-song recording: without
    #: it the screen changed to the last song at 511.5 s, 36 s before the band
    #: did (547.2 s); at 0.6 that becomes 539.1 s, at the cost of one genuine
    #: change landing 7 s later (386.4 -> 393.4 s). 0.6 and 0.7 behaved
    #: identically; genuine changes measured 0.77-0.99, so 0.6 keeps margin.
    #: Keep `cross_song_marginal_threshold` alongside it: relaxing that to 0.4
    #: brought the early jump back to 521.6 s.
    cross_song_min_song_share: float | None = 0.6
    #: A key or tempo change in the music (`music.change_watch`) within this
    #: many seconds lowers `cross_song_marginal_threshold` and
    #: `cross_song_min_song_share` by `cross_song_music_change_relief`.
    #: One-directional: a change is evidence of a new song, an unchanged key
    #: is evidence of nothing (sets are often kept in one key). Kept small on
    #: purpose until goldens with key changes between songs exist; the other
    #: gates (observations, words, talking) are untouched, so it can only
    #: make a switch the words already propose land sooner. 0 disables.
    cross_song_music_change_relief: float = 0.1
    cross_song_music_change_window_s: float = 45.0
    #: How long the belief may disagree with the screen before the scheduler
    #: acts on it *regardless* of `marginal_threshold`. `None` disables.
    #:
    #: `marginal_threshold` was the only gate on the commit path and it has no
    #: floor, so sustained low confidence is an indefinite freeze by
    #: construction. Traced live: the marginal sat between 0.08 and 0.27 for
    #: tens of seconds while the band sang a whole repeated chorus, and the
    #: screen never moved again for the final 51 s of the song. Nothing
    #: anywhere noticed that words kept arriving while the display did not
    #: change.
    #:
    #: The substitution is persistence for certainty — the same reasoning as
    #: `decision.hold`, which was written for lost mode and is not wired in.
    #: A belief that has pointed at the *same* other slide for this long is
    #: saying something a single confident reading cannot, and the thing it is
    #: being compared against is not a better commit but no commit at all,
    #: ever.
    #: Swept on the goldens at lead 3000 (wrong-slide / flicker):
    #:
    #:     off  0.357 / 16      12 s  0.360 / 20
    #:      5 s 0.351 / 21      16 s  0.357 / 16   (never fires)
    #:      8 s 0.362 / 19      25 s  0.357 / 16   (never fires)
    #:
    #: The goldens track well, so they show this knob's *cost* and none of its
    #: benefit — read the sweep as an upper bound, not a trade. 5 s scoring
    #: better than off is not evidence to tune on: the series is non-monotonic
    #: across seven recordings, which is what noise looks like.
    #:
    #: 16 s never fires on any golden, so it is free where things already work
    #: while still capping a freeze at 16 s rather than the 51 s observed live.
    #: That is the right shape for a safety net: invisible in normal operation,
    #: decisive in the failure it exists for.
    stall_after_seconds: float | None = 16.0
    #: Even a stalled display will not follow noise. Below this the posterior
    #: is not making a claim worth acting on.
    stall_min_marginal: float = 0.15
    #: How stale the last belief may be before the watchdog stops acting on it.
    #:
    #: The watchdog fires on a belief nobody has refreshed, which is the point
    #: — but a belief from two minutes ago describes a service that has moved
    #: on. This bounds how far back "the last thing we knew" is allowed to
    #: reach. Generous, because the drought it exists for ran 17.5 s.
    stall_max_belief_age_seconds: float = 60.0
    #: Commit the next slide once this much of the posterior says the current
    #: one ends within the lead time. `None` keeps the old point estimate.
    #:
    #: The projection used to collapse the belief to a single position and
    #: compare `(1 - position) * n_words / rate` against the lead time. But
    #: the posterior is a *distribution* over how far through the slide the
    #: band is, and a mean cannot tell "halfway, confidently" from "either
    #: nearly done or barely started" — opposite instructions for a scheduler.
    #: Averaging first and thresholding second throws away the disagreement
    #: and then acts as if it never existed.
    #:
    #: Asking what fraction of the belief thinks the slide is ending keeps it.
    #:
    #: **Where the gain is, and is not.** Compared against the point estimate
    #: at *matched anticipation* — each rule swept over its own lead times, so
    #: neither is credited for merely firing earlier — the picture is not
    #: uniform:
    #:
    #:     anticipation      point est.   P>=0.7    gain
    #:        +1640 ms          0.359      0.366   -0.007
    #:        +1242 ms          0.358      0.368   -0.011
    #:          -76 ms          0.415      0.400   +0.014
    #:         -701 ms          0.437      0.467   -0.030
    #:
    #: It *loses* while the display is still running late and *wins* near zero
    #: anticipation, which is the region worth having. The same shape appears
    #: independently at P>=0.5 (+0.012 at -175 ms) and with dwell folded in
    #: (+0.003 at -274 ms) — three arms agreeing is why this is not read as one
    #: lucky point. Past about -700 ms it collapses.
    #:
    #: **Re-swept 0.7 -> 0.8** once the lead became derived and the marginal
    #: conditional, both of which changed what this knob is choosing between.
    #: At the default margin (goldens, availability clock):
    #:
    #:     off   0.395      0.7   0.411      0.85  0.372*
    #:     0.5   0.389      0.8   0.407      0.9   0.375*
    #:                                       0.95  0.377*
    #:      (* measured at margin -500, where the curve is shifted)
    #:
    #: 0.8 is free rather than a trade: anticipation is unchanged at -189 ms,
    #: every slide-length bucket moves by at most 66 ms, and flicker rises by
    #: one. Past 0.85 it turns over — waiting for near-unanimity just makes it
    #: late.
    #:
    #: Validated leave-one-out over the seven goldens: refitting with each
    #: held out picks this side every time, and the held-out mean matches the
    #: in-fit mean, which is the opposite of what over-fitting looks like.
    transition_probability: float | None = 0.8
    #: Seconds the display must have been up before a *one-slide* backward move
    #: is allowed. `None` keeps the flat `backward_min_slides` rule.
    #:
    #: The point of the distinction: a one-slide disagreement is ambiguous. Just
    #: after an anticipatory commit it is the anticipation itself — the band has
    #: not arrived yet — and stepping back produces the flip-flop
    #: `backward_min_slides = 2` exists to prevent. Several seconds later the
    #: same disagreement means something else: the band did not go where we
    #: guessed, and the screen is simply wrong.
    #:
    #: **Off by default: measured worse once the machine was clean.**
    #:
    #: First measured on a transcript captured while 46 leaked sidecar processes
    #: were eating ~3.5 cores, where it looked like a good trade — 0.025 of
    #: wrong-slide by displayed text for reversals going 5 -> 8. Re-measured on
    #: a transcript captured idle, which has 33% more words, it is not:
    #:
    #:                          by text   reversals   <2 s flips
    #:   off (back 2 only)        0.430        4           9
    #:   one-slide after 4 s      0.422       18          10
    #:   one-slide after 6 s      0.423       14          10
    #:   backward_min 1 flat      0.411       23          17
    #:
    #: 0.008 of accuracy for 4.5x the reversals. The rule was largely
    #: compensating for evidence gaps the leak was causing: with a fuller
    #: transcript the tracker reaches the slide on its own, and the extra
    #: backward moves are mostly it second-guessing a correct anticipation.
    #:
    #: Kept as an option rather than deleted, because the finding is
    #: conditional: when evidence is sparse it did help. If a future change
    #: thins the transcript again, this is the knob that compensates.
    #:
    #: Still off, but no longer the only way to correct a one-slide error —
    #: `backward_one_max_position` below now does that job, and measured
    #: better on both cost axes. Leave this at `None` unless the two are being
    #: compared: enabling both together measured worse than either alone.
    backward_one_after_seconds: float | None = None
    #: Allow a one-slide backward move when the belief sits within this
    #: fraction of the target slide — i.e. near its *start*.
    #:
    #: This is the discriminator the other two rules are proxies for.
    #: Anticipation lag and a real repeat both present as "the belief is one
    #: slide behind the screen", and the belief carries something that
    #: separates them — imperfectly:
    #:
    #:   - **Lag**: we committed N+1 early *because* the band was finishing
    #:     slide N, so the belief is usually late in N and about to leave on
    #:     its own.
    #:   - **A real move back**: the band re-entered slide N from the top, so
    #:     the belief lands near position 0 and climbs.
    #:
    #: **The separation is a tendency, not a rule**, and an earlier version of
    #: this comment overstated it. Measured over the 33 observations that
    #: genuinely lag after an anticipatory commit on the seven goldens: median
    #: position 0.84, which is the effect — but the 0th percentile is **0.10**,
    #: fully overlapping the re-entry case, and 8 of 33 sit below 0.60. Low
    #: position makes a repeat much more likely, not certain, which is why
    #: this gate is one of three (`gap == 1`, and
    #: `backward_marginal_threshold`) rather than the whole decision. On the
    #: three lag observations that did fall under 0.25, it was the confidence
    #: bar that stopped them, at 0.21-0.24 against 0.60.
    #:
    #: Distance (`backward_min_slides`) asks "is this too big to be lag?" and
    #: can never fix a one-slide error — which is every repeat and reprise, the
    #: most common kind. Duration (`backward_one_after_seconds`) asks "would lag
    #: have resolved by now?", which is at least causally related, but it waits
    #: out a fixed delay while the wrong slide is on screen.
    #:
    #: Swept on the seven approved goldens, scoring the *displayed* slide
    #: (`eval.metrics.wrong_slide_rate` over commits — the belief was already
    #: right in every one of these cases, so scoring the belief shows nothing):
    #:
    #:                              displayed   medley   backward   <2 s flips
    #:      off                         0.273    0.218         29            2
    #:      after 3 s                   0.271    0.206         35            3
    #:      after 5 s                   0.271    0.206         34            4
    #:      position <= 0.25            0.271    0.206         31            2
    #:      position <= 0.40            0.271    0.206         33            4
    #:      position <= 0.60            0.271    0.206         34            4
    #:      both (0.40 or 3 s)          0.271    0.206         36            5
    #:
    #: Every variant buys the same accuracy, so the choice is entirely about
    #: cost — and position at 0.25 buys it for **two** extra backward commits
    #: and no extra sub-2-second flips, where the duration rule costs six and
    #: adds one. Enabling both is strictly worse than either. That is the
    #: measured form of the argument above: the tighter the rule is bound to
    #: the actual re-entry signature, the less it fires on anything else.
    #:
    #: Note the gain is small here *because* `lead_time_ms` was simultaneously
    #: corrected from the frontend's stale 500 to the tuned 1500. Measured at
    #: 500 the same rule was worth 0.297 -> 0.285; most of that was really the
    #: lead time, and a sweep run before that fix would have overstated this.
    #:
    #: **Re-swept 2026-09-04 on a display-aware metric**, after finding that
    #: the exact-index comparison could not see this knob at all — it read
    #: 0.271 for every threshold, because the slides the looser settings moved
    #: onto carry *identical words* to the golden's (see
    #: `eval.metrics.identical_slide_groups`). Scored on what the congregation
    #: would actually see:
    #:
    #:      threshold   display-aware   backward   <2 s flips   lag obs below it
    #:      off                 0.205         29            2           —
    #:      0.25                0.203         31            2       3 of 33
    #:      0.40                0.201         33            4       ~4 of 33
    #:      0.60                0.197         34            4       8 of 33
    #:
    #: 0.60 scores best and is not taken. It buys 0.006 for nearly triple the
    #: exposure to misreading real lag, and the extra sub-2-second flips are
    #: the failure a congregation actually notices — the thing this whole rule
    #: is trying not to cause. 0.25 sits where the overlap with lag is
    #: smallest while still catching the re-entry case it was built for.
    backward_one_max_position: float | None = 0.25
    #: Minimum estimated tempo (words/sec) used in the time-remaining
    #: calculation. Stops a degenerate near-zero `rate` from producing
    #: infinite ETA estimates.
    min_rate_wps: float = 0.5


@dataclass(frozen=True)
class TrackedPosition:
    """Where a tracker believes the band is — the scheduler's whole input.

    Extracted so the commit rule below is stated once and shared. The particle
    filter reaches it through `on_belief`, which reduces its belief to these six
    numbers; any other tracker supplies them directly. The rule is identical
    either way, which is the point: when two trackers are compared, the
    comparison is of the trackers and not of two subtly divergent copies of
    this logic.

    `confidence` occupies the slot the particle filter fills with a top-slide
    marginal. Any tracker's "how sure am I" in `[0, 1]` works, and it is gated
    against `marginal_threshold` the same way.
    """

    item_idx: int
    slide_idx: int
    confidence: float
    #: Progress through `slide_idx`, `[0, 1]`.
    position: float
    #: Tempo in words per second.
    rate: float
    t_seconds: float
    #: `(position, weight)` over the states on this slide, when the tracker
    #: has them. The scheduler asks what fraction of the belief thinks the
    #: slide is ending rather than averaging first — see
    #: `LeadTimeSchedulerConfig.transition_probability`. Empty is a valid
    #: answer meaning "no distribution available"; the point estimate is used
    #: then, so trackers that carry no cloud behave exactly as before.
    position_spread: tuple[tuple[float, float], ...] = ()
    #: How much of the belief is on `item_idx` at all, over every slide of it.
    #: `confidence` is conditioned on the song and so cannot say this. `None`
    #: when the tracker does not report one; the song-share gate then passes.
    song_share: float | None = None


@dataclass(frozen=True)
class SlideProjection:
    """Where the band will be a lead-time from now, and how that was reached.

    Exists so the commit rule and the debug readout cannot disagree. The
    countdown an operator watches in the Perf tab is *this* object — the one
    `on_position` branched on — rather than a second copy of the arithmetic
    that could drift from it silently.
    """

    #: Progress through the current slide, `[0, 1]`.
    position: float
    #: Tempo actually used, after the `min_rate_wps` floor.
    rate_wps: float
    #: Words on the slide the band is on.
    n_words: int
    #: `(1 - position) * n_words / rate` — how long this slide has left.
    seconds_remaining: float
    #: The slide the rule projects to, which may be the current one.
    target_slide: int
    #: True when `seconds_remaining` dropped under the lead time *and* song
    #: order was still predictive enough to step forward. False with a small
    #: `seconds_remaining` means the projection was blocked by
    #: `order_is_predictive` — worth seeing, since it looks identical from
    #: outside to the countdown simply not having elapsed.
    advancing: bool
    #: Fraction of the belief saying this slide ends within the lead time, or
    #: `None` where the point estimate was used instead.
    ending_probability: float | None = None
    #: The lead actually used, after derivation and clamping.
    lead_seconds: float = 0.0
    #: How many slides forward of the belief's own slide the target sits. More
    #: than one means the lead outlasted a short slide entirely.
    slides_ahead: int = 0


@dataclass(frozen=True)
class SchedulerTelemetry:
    """A read-only look at the scheduler's running state, for the debug panel.

    The panel used to reach into the scheduler's private fields directly, which
    made renaming any of them a silent break — the readout is not covered by
    the decision tests, so nothing would have failed. Going through here means
    the internals can move as long as this stays honest.
    """

    #: Smoothed interval between belief snapshots, or `None` before two have
    #: arrived. One of the three terms in the derived lead time.
    gap_seconds: float | None
    #: Sung-clock time the belief started disagreeing with the screen, or
    #: `None` when it agrees — see `stall_after_seconds`.
    stall_since: float | None
    #: The other song the belief has been favouring, and for how many
    #: consecutive observations — see `cross_song_min_observations`.
    pending_item: int | None
    pending_count: int


@dataclass
class LeadTimeScheduler:
    """Stateful scheduler — call `on_belief(belief)` once per belief snapshot."""

    setlist: IndexedSetlist
    config: LeadTimeSchedulerConfig = field(default_factory=LeadTimeSchedulerConfig)
    #: Tracks where in this song order has stopped being predictive.
    progress: SongProgress = field(default_factory=SongProgress)
    #: Measured sung-to-actionable lag, in seconds, set by whoever owns the
    #: ASR pipeline (`asr.latency.DeliveryLatency`). `None` until something
    #: has measured it, in which case `fallback_lag_seconds` stands in.
    observed_lag_seconds: float | None = None
    #: Smoothed talking score from `decision.singing.SingingDetector`, set by
    #: whoever owns it (the server). Only gates leaving a song — see
    #: `cross_song_max_talking_score`. 0.0 until something reports one, so
    #: harnesses without a detector behave as before.
    talking_score: float = 0.0
    #: Whether a band is playing (`music.band`), set by the server: the plan
    #: gate then treats the room as singing whatever `talking_score` says.
    band_playing: bool = False
    #: Moves the service plan does not predict must be backed by the target
    #: slide's own words (`decision.plan_gate`). Fed words by the server;
    #: `None` (harnesses without it) commits as before.
    plan_gate: PlanGate | None = None
    #: Audio time of the latest key or tempo change (`note_music_change`).
    music_change_t: float | None = None

    #: Sung-clock time of the previous belief, and a running estimate of the
    #: gap between them — the scheduler's own sampling interval. Measured
    #: here rather than configured because it moves with ASR pass time:
    #: `SlidingWindowWrapper` paces the next pass off the last one, so a slow
    #: pass widens this exactly when evidence is already late.
    _last_belief_t: float | None = field(default=None, init=False, repr=False)
    _gap_estimate: float | None = field(default=None, init=False, repr=False)

    #: Most recent `(item_idx, slide_idx)` we fired a decision for. The
    #: scheduler won't re-fire the same key until the cloud has moved
    #: off it and onto a new slide.
    _last_committed: tuple[int, int] | None = field(default=None, init=False, repr=False)
    #: When that commit happened, for `backward_one_after_seconds`.
    _last_commit_t: float | None = field(default=None, init=False, repr=False)
    #: Which other song the belief has been favouring, and for how many
    #: consecutive observations — see `cross_song_min_observations`.
    _pending_item: int | None = field(default=None, init=False, repr=False)
    _pending_count: int = field(default=0, init=False, repr=False)
    #: Running count of transcribed words (`note_words`), and its value when
    #: the pending song took the lead — see `cross_song_min_words`.
    _words_heard: int = field(default=0, init=False, repr=False)
    #: The slide the screen showed before this engine's last move, while the
    #: tracker has not left it since: the watchdog never puts it back
    #: (`_break_stall`). None once the tracker has moved on, or after an
    #: operator's move.
    _moved_from: tuple[int, int] | None = field(default=None, init=False, repr=False)
    _pending_words_from: int = field(default=0, init=False, repr=False)
    _last_note: int = field(default=0, init=False, repr=False)
    #: The slide the belief has been stuck disagreeing with the screen about,
    #: and when that disagreement started — see `stall_after_seconds`.
    _stall_candidate: tuple[int, int] | None = field(
        default=None, init=False, repr=False
    )
    _stall_since: float | None = field(default=None, init=False, repr=False)
    #: The last position a tracker reported, so `tick` has something to act on
    #: when no new belief is arriving — which is the whole drought case.
    _last_tracked: TrackedPosition | None = field(
        default=None, init=False, repr=False
    )

    def on_belief(self, belief: BeliefState) -> MatcherDecision | None:
        """Possibly emit a `MatcherDecision` based on this belief.

        Two ways a decision fires:

          - **Catch-up**: the belief's mode slide is *ahead* of the slide
            currently on screen (the band moved on, or the cloud crossed a
            boundary between snapshots, or we're sitting on the blank
            song-start slide). Commit straight to where the band is, with
            no lead-time gate — we're already behind.
          - **Anticipation**: the band is on the displayed slide; commit the
            *next* slide shortly before the current one ends, so the flip
            lands on the musical beat instead of a half-second late.

        The displayed slide is tracked via `_last_committed` (set on our own
        fires and on operator overrides through `note_external_commit`).
        Without the catch-up path the scheduler would freeze the display one
        slide behind whenever the cloud advanced past it — including never
        leaving the blank lead-in slide.
        """
        if not belief.particles:
            return None
        item_idx, mode_slide, _marginal = belief.top_hypothesis
        # Gate on confidence in *what to display*, not in which index we are on.
        # A chorus written as four identical slides splits the mass four ways,
        # so the per-slide marginal sits near 0.25 while the pooled mass over
        # the copies — every one of which puts the same words on screen — is
        # comfortably above threshold. Gating on the split value refuses to
        # commit to a slide we are in fact sure about.
        #
        # Measured on the six goldens: of the moments where the filter's top
        # slide was the ground-truth one and the screen was showing something
        # else, 39% were blocked here and nowhere else — the single largest
        # cause of the tracker never reaching a slide at all.
        #
        # `commit_confidence` falls back to the top marginal when the filter
        # supplies no equivalence information, so trackers that do not pool
        # behave exactly as before.
        return self.on_position(
            TrackedPosition(
                item_idx=item_idx,
                slide_idx=mode_slide,
                confidence=belief.commit_confidence,
                position=belief.mode_position,
                rate=belief.mode_rate,
                t_seconds=belief.t_seconds,
                position_spread=tuple(belief.top_position_spread()),
                song_share=belief.song_share,
            )
        )

    def on_position(self, tracked: TrackedPosition) -> MatcherDecision | None:
        """Decide, and if the ordinary rule declines, check for a stalled screen.

        The rule itself is `_decide`. This wrapper exists because every one of
        that method's many `return None` paths is a way for the display to stop
        moving, and none of them was accountable for how long it had been
        saying no.
        """
        self._note_belief_gap(tracked.t_seconds)
        self._last_tracked = tracked
        here = (tracked.item_idx, tracked.slide_idx)
        if self._moved_from is not None and here != self._moved_from:
            self._moved_from = None  # the tracker moved on: back is a real move again
        decision = self._decide(tracked)
        if decision is not None:
            self._stall_candidate = None
            self._stall_since = None
            return decision
        return self._break_stall(tracked)

    def tick(self, t_seconds: float) -> MatcherDecision | None:
        """Advance the watchdog on the audio clock, with no new belief.

        The watchdog was evaluated only inside `on_position`, so it ran on the
        very clock that had stalled: when the ASR delivers nothing, no belief
        arrives, nothing is evaluated, and a display frozen *because evidence
        stopped* is exactly the case it could not see. Traced live — a 17.5 s
        stretch with zero transcript events, during which the belief sat
        stable on a slide the screen was not showing and the watchdog never
        got a turn.

        `t_seconds` is the audio clock, the same timeline as a belief's
        `t_seconds`. It keeps advancing while the transcript does not, which
        is what makes it the right clock to notice a stall on.

        Only the stall path runs here. Committing on a projection would mean
        anticipating from a belief nobody has refreshed, which is a guess; all
        this does is stop the screen contradicting the last thing the tracker
        actually said.
        """
        tracked = self._last_tracked
        if tracked is None:
            return None
        if t_seconds - tracked.t_seconds > self.config.stall_max_belief_age_seconds:
            return None
        return self._break_stall(tracked, now=t_seconds)

    def _break_stall(
        self, tracked: TrackedPosition, *, now: float | None = None
    ) -> MatcherDecision | None:
        """Act on a belief the screen has been ignoring for too long.

        Deliberately requires the *same* slide throughout the window rather
        than a low-confidence reading at one instant. A thrashing posterior
        resets the timer on every change and never fires here, which is
        correct: when the tracker genuinely does not know, freezing beats
        guessing. What this rescues is the other case — a tracker that has
        quietly settled on an answer it is not confident enough to state.
        """
        after = self.config.stall_after_seconds
        if after is None:
            return None
        t_now = tracked.t_seconds if now is None else now
        item = self.setlist.item(tracked.item_idx)
        if item is None:
            return None
        candidate = (tracked.item_idx, tracked.slide_idx)
        if candidate == (tracked.item_idx, self._displayed_slide(tracked.item_idx)):
            self._stall_candidate = None
            self._stall_since = None
            return None
        if candidate != self._stall_candidate:
            self._stall_candidate = candidate
            self._stall_since = t_now
            return None
        if self._stall_since is None:
            self._stall_since = t_now
            return None
        if t_now - self._stall_since < after:
            return None
        if tracked.confidence < self.config.stall_min_marginal:
            return None
        # Leaving a song is never a stall decision: `_cross_song_ready` exists
        # because that move needs *more* evidence, not less, and this path has
        # less by definition.
        shown = self._last_committed
        if shown is not None and shown[0] != tracked.item_idx:
            return None
        # Nor undoing this engine's own early advance: after it the tracker
        # still sits on the line just finished, and 16 s into an instrumental
        # the watchdog put that line back up, a slide behind when singing
        # resumed. Once the tracker has been anywhere else since the move
        # (the band really went back), it may.
        if candidate == self._moved_from:
            return None
        if not self._plan_allows(candidate, t_now):
            return None

        slide = item.slide(tracked.slide_idx)
        if slide is None:
            return None
        log.info(
            "stall: screen has ignored item %d slide %d for %.1fs (marginal "
            "%.2f) — committing anyway",
            candidate[0], candidate[1],
            t_now - self._stall_since, tracked.confidence,
        )
        self._stall_candidate = None
        self._stall_since = None
        self._last_belief_t = None
        self._gap_estimate = None
        self._last_tracked = None
        self._moved_from = None  # a watchdog move is never undone by this rule
        self._last_committed = candidate
        # Also the noticing clock: `backward_one_after_seconds` measures how
        # long the display has been up, and recording a stale belief's time
        # here would tell it the screen changed seconds before it did.
        self._last_commit_t = t_now
        self.progress.note_commit(candidate[0], candidate[1])
        return MatcherDecision(
            item_idx=candidate[0],
            slide_idx=candidate[1],
            # Stamped on the clock that noticed, not on the stale belief's —
            # when this fires from `tick` the belief may be many seconds old.
            t_seconds=t_now,
            confidence=tracked.confidence,
        )

    def _decide(self, tracked: TrackedPosition) -> MatcherDecision | None:
        """The commit rule itself, over a tracker-agnostic position.

        See `on_belief` for what the two branches are and why the lead-time
        projection replaced branching on where the display happened to be.
        """
        item_idx = tracked.item_idx
        mode_slide = tracked.slide_idx
        marginal = tracked.confidence
        if marginal < self.config.marginal_threshold:
            return None

        item = self.setlist.item(item_idx)
        if item is None:
            return None

        if not self._cross_song_ready(tracked):
            return None

        displayed = self._displayed_slide(item_idx)

        # Where will the band be a lead-time from now?
        #
        # This used to branch on where the *display* was: catch up if the mode
        # was ahead of it, anticipate only when the mode sat exactly on it.
        # That second condition is unreachable in practice. Evidence arrives
        # about three quarters of a second stale, so by the time a slide is
        # committed the cloud has already moved past it, and the scheduler
        # re-enters catch-up on the next belief instead of ever anticipating.
        # Measured over both verified recordings: 41 of 41 commits came from
        # the catch-up and backward branches, and *none* from anticipation.
        # The lead-time mechanism — the thing Phase 6 was built around — was
        # dead code, which is why sweeping `lead_time_ms` from 300 ms to
        # 2400 ms moved timing without ever improving accuracy.
        #
        # So project instead of branching. Ask where the band will be after
        # `lead_time`, and commit that. The two old behaviours fall out of one
        # rule: if the cloud is ahead of the screen the projection is ahead
        # (catch-up), and if the band is near the end of their current slide
        # the projection is the next one (anticipation) — now reachable
        # whatever the display happens to be showing.
        projection = self.project(tracked)
        target = projection.target_slide if projection is not None else mode_slide

        if target == displayed:
            # The projection is already on screen — nothing to do. This covers
            # sitting mid-slide with the band nowhere near its end, and the
            # blank lead-in slide before anyone has sung.
            return None

        if target < displayed:
            # Moving the screen backwards. Safe only for a real jump, not for
            # the ordinary case of having anticipated a slide the band has not
            # reached yet — so require a step of at least
            # `backward_min_slides` and more confidence than a forward move,
            # since yanking a congregation backwards is worse than being late.
            #
            # A duration-gated exception for *persistent* one-slide
            # disagreements was tried and measured worse — see docs/done/PHASE_7.md
            # ("Tried and reverted: correcting an off-by-one").
            gap = displayed - target
            if gap < self.config.backward_min_slides:
                # Unknown commit time counts as *just now*, not as long ago:
                # an operator override has no timestamp, and stepping the screen
                # back one slide immediately after they moved it is the worst
                # version of this behaviour.
                held = (
                    tracked.t_seconds - self._last_commit_t
                    if self._last_commit_t is not None
                    else 0.0
                )
                after = self.config.backward_one_after_seconds
                max_pos = self.config.backward_one_max_position
                # Either signal is enough. They fail in different directions:
                # position catches a re-entry on the first observation, and
                # duration still covers a repeat the tracker joined late (so
                # position is already high) that has simply been wrong for a
                # while.
                # `_last_commit_t is None` means the screen was moved by a
                # human (`note_external_commit` has no timestamp). Undoing that
                # a moment later is the worst version of this behaviour, so the
                # re-entry signal is not allowed to do it either — the operator
                # keeps the last word until the band moves the display forward
                # on its own, or goes somewhere far enough to clear
                # `backward_min_slides`.
                ours = self._last_commit_t is not None
                looks_like_re_entry = (
                    ours and max_pos is not None and tracked.position <= max_pos
                )
                lag_would_have_cleared = after is not None and held >= after
                # Strictly the *one*-slide exception. A wider gap that is still
                # under `backward_min_slides` (only possible when that has been
                # raised) is not what either signal was characterised on, and
                # letting it through here quietly defeated a raised threshold.
                if gap != 1 or not (looks_like_re_entry or lag_would_have_cleared):
                    return None
            if marginal < self.config.backward_marginal_threshold:
                return None

        if target >= len(item.slides):
            # End of song. Cross-song commits are Phase 7+.
            return None

        candidate = (item_idx, target)
        if self._last_committed == candidate:
            return None
        if not self._plan_allows(candidate, tracked.t_seconds):
            return None

        decision = MatcherDecision(
            item_idx=item_idx,
            slide_idx=target,
            t_seconds=tracked.t_seconds,
            confidence=marginal,
        )
        before = self._last_committed
        self._moved_from = before if before is not None and before[0] == item_idx else None
        self._last_committed = candidate
        self._last_commit_t = tracked.t_seconds
        self.progress.note_commit(item_idx, target)
        log.debug(
            "scheduler commit: item=%d displayed=%d→%d (mode=%d) marginal=%.2f",
            item_idx,
            displayed,
            target,
            mode_slide,
            marginal,
        )
        return decision

    def project(self, tracked: TrackedPosition) -> SlideProjection | None:
        """Run the lead-time projection, without committing anything.

        `on_position` calls this to decide; the server calls it to *show* the
        countdown. Pure — no state is touched — so the debug panel polling it
        cannot perturb the decision it is reporting.

        Returns `None` where the projection does not apply at all: no such
        item, or a slide with no words (the blank lead-in), or the last slide
        of the song, where there is nothing to project to.
        """
        item = self.setlist.item(tracked.item_idx)
        if item is None:
            return None
        mode_slide = tracked.slide_idx
        at_last = mode_slide >= len(item.slides) - 1
        slide = item.slide(mode_slide)
        if slide is None or not slide.words or at_last:
            return None

        rate = max(self.config.min_rate_wps, tracked.rate)
        position = max(0.0, min(1.0, tracked.position))
        n_words = len(slide.words)
        seconds_remaining = (1.0 - position) * n_words / rate

        lead = self.effective_lead_seconds(tracked.item_idx)
        ending = self._ending_probability(tracked, n_words, rate, lead)
        if ending is None:
            ends_within_lead = seconds_remaining < lead
        else:
            ends_within_lead = ending >= self.config.transition_probability

        advancing = False
        target = mode_slide
        if ends_within_lead:
            # The current slide is ending. Step to the next one in order —
            # but only where order still predicts what follows. Standing on
            # a slide the band has previously left unpredictably (the end
            # of a bridge they looped), there is no safe assumption, so
            # hold at `mode_slide` and let the next words decide.
            advancing = self.progress.order_is_predictive(
                mode_slide, at_last_slide=at_last
            )
            if advancing:
                target = mode_slide + 1

        # Keep walking while the lead outlasts the slides it crosses.
        #
        # Stepping once silently assumes the next slide lasts at least as long
        # as the lead. When it does not — and the required lead is around 3.9 s,
        # so anything under four seconds qualifies — the band passes through
        # that slide entirely before the screen was ever going to move, and no
        # tuning reaches it: landing it on time would mean committing it before
        # the previous slide began. Measured across two sessions, slides under
        # 4 s sat at +756 ms late under every mechanism tried, including a
        # fixed lead swept to 5.5 s and a second ASR channel. This is why.
        #
        # So spend the lead instead of stepping once: consume what is left of
        # the current slide, then whole slides, and target wherever the band
        # will actually be. Capped by `max_lookahead_slides`, because every hop
        # past the first rests on the predicted duration of a slide nobody has
        # started singing yet.
        if advancing:
            budget = lead - seconds_remaining
            hops = 1
            while hops < self.config.max_lookahead_slides:
                if target >= len(item.slides) - 1:
                    break
                if not self.progress.order_is_predictive(target, at_last_slide=False):
                    break
                nxt = item.slide(target)
                if nxt is None or not nxt.words:
                    break
                duration = len(nxt.words) / rate
                if budget <= duration:
                    break
                budget -= duration
                target += 1
                hops += 1

        return SlideProjection(
            position=position,
            rate_wps=rate,
            n_words=n_words,
            seconds_remaining=seconds_remaining,
            target_slide=target,
            advancing=advancing,
            ending_probability=ending,
            lead_seconds=lead,
            slides_ahead=target - mode_slide,
        )

    def effective_lead_seconds(self, item_idx: int | None = None) -> float:
        """The lead the commit rule should actually be using, right now.

        `delivery_lag + gap_to_next_update + target_margin`, clamped. The first
        two are measurements that move continuously; only the margin is a
        setting. See `LeadTimeSchedulerConfig.target_margin_ms`. A passage
        read aloud (`item_idx` names one) takes `spoken_margin_ms` on top.
        """
        if self.config.fixed_lead_time_ms is not None:
            return self.config.fixed_lead_time_ms / 1000.0
        lag = (
            self.observed_lag_seconds
            if self.observed_lag_seconds is not None
            else self.config.fallback_lag_seconds
        )
        gap = self._gap_estimate if self._gap_estimate is not None else 0.0
        margin = self.config.target_margin_ms
        item = self.setlist.item(item_idx) if item_idx is not None else None
        if item is not None and item.spoken:
            margin += self.config.spoken_margin_ms
        lead = lag + gap + margin / 1000.0
        return max(
            self.config.min_lead_seconds, min(self.config.max_lead_seconds, lead)
        )

    def telemetry_snapshot(self) -> SchedulerTelemetry:
        """Current internal state, for reporting only — never for deciding."""
        return SchedulerTelemetry(
            gap_seconds=self._gap_estimate,
            stall_since=self._stall_since,
            pending_item=self._pending_item,
            pending_count=self._pending_count,
        )

    def _note_belief_gap(self, t_seconds: float) -> None:
        """Track how long the scheduler waits between chances to act.

        Declining to commit is only safe if another chance arrives before the
        band moves, so the interval belongs in the lead. Smoothed rather than
        instantaneous: a single long gap (a silent passage) should widen the
        lead a little, not multiply it.
        """
        last = self._last_belief_t
        self._last_belief_t = t_seconds
        if last is None or t_seconds <= last:
            return
        # Clamped at the measured p90 gap (2.99 s over the goldens). Beyond
        # that is a silent passage, not the sampling interval, and letting one
        # of those into the estimate widens the lead for the whole song after.
        gap = min(3.0, t_seconds - last)
        self._gap_estimate = (
            gap if self._gap_estimate is None else 0.7 * self._gap_estimate + 0.3 * gap
        )

    def _ending_probability(
        self,
        tracked: TrackedPosition,
        n_words: int,
        rate: float,
        lead_seconds: float,
    ) -> float | None:
        """How much of the belief says this slide ends within `lead_seconds`.

        Returns `None` when the caller should fall back to the point estimate:
        the feature is off, or the tracker supplied no spread (any tracker
        without a state cloud, and every hand-built `TrackedPosition` in the
        tests).
        """
        if self.config.transition_probability is None:
            return None
        spread = tracked.position_spread
        if not spread:
            return None
        total = sum(w for _p, w in spread)
        if total <= 0.0:
            return None
        ending = sum(
            w
            for pos, w in spread
            if (1.0 - max(0.0, min(1.0, pos))) * n_words / rate < lead_seconds
        )
        return ending / total

    def _plan_allows(self, candidate: tuple[int, int], t_seconds: float) -> bool:
        """The plan gate's verdict, checked before anything is committed so a
        withheld move leaves no trace and is simply proposed again later."""
        gate = self.plan_gate
        if gate is None or gate.allows(
            self.setlist, self._last_committed, candidate, t_seconds, self.talking_score,
            self.band_playing,
        ):
            return True
        log.debug("plan gate: withheld %s -> %s", self._last_committed, candidate)
        return False

    def _cross_song_ready(self, tracked: TrackedPosition) -> bool:
        """May the screen leave the song it is currently showing?

        Returns True for any move *within* the displayed song, so the ordinary
        path is untouched. Leaving one has to clear a higher marginal and hold
        it for several observations in a row, because the failure this guards
        is a one-observation spike onto another song's opening during an
        evidence drought — see `cross_song_marginal_threshold`.
        """
        shown = self._last_committed
        if shown is None or shown[0] == tracked.item_idx:
            # Nothing displayed yet, or staying in the same song.
            self._pending_item = None
            self._pending_count = 0
            return True

        if tracked.item_idx != self._pending_item:
            self._pending_item = tracked.item_idx
            self._pending_count = 0

        max_talk = self.config.cross_song_max_talking_score
        min_share = self.config.cross_song_min_song_share
        relief = self._music_change_relief(tracked.t_seconds)
        if min_share is not None:
            min_share -= relief
        if (
            tracked.confidence < self.config.cross_song_marginal_threshold - relief
            or (max_talk is not None and self.talking_score >= max_talk)
            or (
                min_share is not None
                and tracked.song_share is not None
                and tracked.song_share < min_share
            )
        ):
            # Not merely "don't move yet" — a dip *breaks the streak*, so
            # confidence has to be sustained rather than merely touched once
            # somewhere across a long run of wobbling observations. Talking
            # breaks it too: chatter that happens to share words with another
            # song's opening is the false jump this guards.
            self._pending_count = 0
            return False

        if self._pending_count == 0:
            # Words already folded into this observation count toward it.
            self._pending_words_from = self._words_heard - self._last_note
        self._pending_count += 1
        if self._pending_count < self.config.cross_song_min_observations:
            return False
        min_words = self.config.cross_song_min_words
        if (
            min_words is not None
            and self._words_heard - self._pending_words_from < min_words
        ):
            return False

        self._pending_item = None
        self._pending_count = 0
        return True

    def note_music_change(self, t_seconds: float) -> None:
        """The music changed key or tempo at audio time `t_seconds`."""
        self.music_change_t = t_seconds

    def _music_change_relief(self, t_now: float) -> float:
        t = self.music_change_t
        window = self.config.cross_song_music_change_window_s
        if t is None or not (0.0 <= t_now - t <= window):
            return 0.0
        return self.config.cross_song_music_change_relief

    def _displayed_slide(self, item_idx: int) -> int:
        """The slide currently on screen for `item_idx`.

        Derived from the last commit we emitted (or the operator's last
        override). Before anything has been committed for this item the
        display is at slide 0 (the blank song-start slide).
        """
        if self._last_committed is not None and self._last_committed[0] == item_idx:
            return self._last_committed[1]
        return 0

    def note_words(self, n: int) -> None:
        """Count `n` newly transcribed words — call before the belief they
        produced reaches `on_belief`. Feeds `cross_song_min_words`."""
        self._words_heard += n
        self._last_note = n

    def note_external_commit(self, item_idx: int, slide_idx: int) -> None:
        """Record an operator-driven slide change so the scheduler won't
        try to re-commit the same destination.

        Operator moves feed `progress` too: an operator jumping back to the
        chorus is the band having repeated it, and it teaches the same lesson
        about this song as the tracker discovering it alone.
        """
        self._last_committed = (item_idx, slide_idx)
        self._last_commit_t = None  # treated as "just now" — see `on_position`
        self._moved_from = None
        self.progress.note_commit(item_idx, slide_idx)

    def reset(self) -> None:
        """Forget the most-recent commit — used on session restart."""
        self._last_committed = None
        self._moved_from = None
        self._last_commit_t = None
        self.progress.reset()
        self._pending_item = None
        self._pending_count = 0
        self._pending_words_from = self._words_heard
        self._stall_candidate = None
        self._stall_since = None
