"""Metric computations over a (decisions, ground_truth) pair.

Each function is pure: same inputs → same outputs, no side effects, no I/O.
This is the layer the A/B harness calls.

Conventions:
    - All `t_seconds` fields are unix-style absolute or relative — the metrics
      only care about deltas, so the choice of zero is up to the caller.
    - "Slide identity" is the tuple `(item_idx, slide_idx)`.
    - When a metric reports per-event detail (anticipation_error_ms,
      recovery_time_ms), the return type is `list[float]` with one value
      per matched event; aggregate to median/p90 elsewhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from auto_presenter.eval.types import (
    CueTiming,
    Decision,
    DeviationMarker,
    GroundTruthEvent,
    SetlistItemSnapshot,
)
from auto_presenter.library.parser import tokenize

#: Window for matching a decision to a ground-truth event (seconds).
ANTICIPATION_MATCH_WINDOW_S = 5.0

#: Seconds after an unpredictable jump during which the display is not scored.
#: Recovering inside this window counts as a clean handling of the jump, not as
#: error — advancing in order and correcting once the words arrive is what a
#: human operator does too, and what the tracker is *supposed* to do.
DEFAULT_DEVIATION_GRACE_S = 2.0


def derive_deviation_markers(
    ground_truth: Sequence[GroundTruthEvent],
) -> list[DeviationMarker]:
    """Ground-truth cues that no in-order model could have seen coming.

    A cue is *predictable* when it is the next slide of the same song. Anything
    else — a repeat, a skip forward, a jump to another song — is the band doing
    something the display could not have known about in advance.

    Recordings carry a hand-annotated `deviation_markers` field for this, and
    every golden ships it empty, so the grace machinery in `wrong_slide_rate`
    has never actually run on them. Deriving the markers from the timeline
    makes the metric usable without annotation, and derives strictly more than
    the hand-rolled copy in `tests/integration/test_deviation_recovery.py`,
    which only caught backward moves (`slide_idx <= prev`) and so missed
    forward skips entirely.

    The first cue is deliberately *not* a deviation. Nothing precedes it, but
    finding the song a service opens on is a thing the tracker is expected to
    do — excusing it here would hide exactly the cold-start behaviour that
    `ForwardFilter._reset`'s opening prior exists to fix.
    """
    gt = sorted(ground_truth, key=lambda g: g.t_seconds)
    return [
        DeviationMarker(t_seconds=cur.t_seconds, kind="jump")
        for prev, cur in pairwise(gt)
        if not (cur.item_idx == prev.item_idx and cur.slide_idx == prev.slide_idx + 1)
    ]


def identical_slide_groups(
    setlist_content: Sequence[SetlistItemSnapshot],
) -> dict[tuple[int, int], frozenset[int]]:
    """`(item, slide)` -> every slide of that song carrying the same words.

    A chorus authored as four identical slides is ordinary in this library,
    and which copy is on screen is invisible to a congregation: the words are
    the same. Scoring copy #1 as wrong when the golden says copy #3 measures
    an index, not the display.

    This is not hypothetical bookkeeping. On `rec_20260818-213825_312` slides
    3 and 6 are word-for-word identical, and raising
    `backward_one_max_position` moved the screen onto slide 6 for 30 s that
    the golden calls slide 3 — a real improvement in what was displayed that
    an index-comparing metric reports as exactly zero change, which is what
    made that tuning decision look like a wash.

    `index.library.build_index` computes the same grouping as
    `IndexedItem.identical_by_slide`, and the tracker already pools it into
    `ForwardBelief.display_marginal`. Recomputing here keeps `metrics` free of
    the indexing package, and takes the snapshot the recording actually
    carries rather than a re-derived one.
    """
    groups: dict[tuple[int, int], frozenset[int]] = {}
    for item in setlist_content:
        by_text: dict[tuple[str, ...], list[int]] = {}
        for idx, slide in enumerate(item.slides):
            if slide:  # blank lead-ins are not "identical" to each other
                by_text.setdefault(tuple(slide), []).append(idx)
        for idx, slide in enumerate(item.slides):
            same = by_text.get(tuple(slide), [idx]) if slide else [idx]
            groups[(item.item_idx, idx)] = frozenset(same)
    return groups


def blank_cue_windows(
    ground_truth: Sequence[GroundTruthEvent],
    setlist_content: Sequence[SetlistItemSnapshot],
) -> list[tuple[float, float]]:
    """`(start, end)` spans where the ground truth asks for a wordless slide.

    The operator returning to the leading blank slide — between songs, during
    a prayer or an instrumental — is a real and common cue, and the tracker
    cannot represent it at all: `ForwardFilter` only ever occupies slides that
    have words. Blanking is a separate feature (`blank_when_idle`, driven by
    `decision.singing`) and it ships **off**.

    So charging the word tracker for this time measures a disabled feature and
    puts a floor under the metric that no tracker work can move. Across the
    seven approved goldens it is 178 s over 5 cues — 28% of all wrong-screen
    time, and the two single largest cues in the whole set.

    Returned separately rather than silently folded in, so the time can still
    be reported: it is a genuine product gap (stale lyrics on screen through a
    69-second gap), just not the matcher's.
    """
    by_item = {item.item_idx: item.slides for item in setlist_content}

    def wordless(item_idx: int, slide_idx: int) -> bool:
        slides = by_item.get(item_idx)
        if slides is None:
            return False
        return slide_idx >= len(slides) or not slides[slide_idx]

    gt = sorted(ground_truth, key=lambda g: g.t_seconds)
    out: list[tuple[float, float]] = []
    for i, ev in enumerate(gt):
        if not wordless(ev.item_idx, ev.slide_idx):
            continue
        end = gt[i + 1].t_seconds if i + 1 < len(gt) else gt[-1].t_seconds
        if end > ev.t_seconds:
            out.append((ev.t_seconds, end))
    return out


def anticipation_error_ms(
    decisions: Sequence[Decision],
    ground_truth: Sequence[GroundTruthEvent],
) -> list[float]:
    """Per-correct-slide signed offset, in milliseconds.

    Negative = decision was early (the AI anticipated correctly — the goal).
    Positive = decision was late (the AI committed after the operator did).

    For each ground-truth event, we pick the closest-in-time decision whose
    slide identity matches and that hasn't been matched by an earlier event
    (greedy 1:1 pairing). Decisions further than `ANTICIPATION_MATCH_WINDOW_S`
    are not considered. Ground-truth events with no matching decision count
    as "missed" and contribute no value.
    """
    used_decisions: set[int] = set()
    out: list[float] = []
    for gt in ground_truth:
        best_idx: int | None = None
        best_dt: float = float("inf")
        for i, d in enumerate(decisions):
            if i in used_decisions:
                continue
            if d.item_idx != gt.item_idx or d.slide_idx != gt.slide_idx:
                continue
            dt = abs(d.t_seconds - gt.t_seconds)
            if dt > ANTICIPATION_MATCH_WINDOW_S:
                continue
            if dt < best_dt:
                best_dt = dt
                best_idx = i
        if best_idx is not None:
            d = decisions[best_idx]
            used_decisions.add(best_idx)
            out.append((d.t_seconds - gt.t_seconds) * 1000.0)
    return out


def wrong_slide_rate(
    decisions: Sequence[Decision],
    ground_truth: Sequence[GroundTruthEvent],
    deviation_markers: Sequence[DeviationMarker] | None = None,
    *,
    forgive_unpredictable: bool = True,
    setlist_content: Sequence[SetlistItemSnapshot] | None = None,
    forgive_identical_slides: bool = True,
    score_blank_cues: bool = False,
    grace_seconds: float | None = None,
) -> float:
    """Fraction of presentation time the displayed slide ≠ ground truth.

    The interval covered runs from the first ground-truth event to the last.
    Time before the first decision is treated as "wrong" (no slide shown yet
    counts as not matching).

    `deviation_markers` + `grace_seconds` exclude the window immediately after
    an unpredictable jump. This is not leniency — it is what the metric has to
    do to mean anything. A deviation is by definition unpredictable, so the
    right behaviour on reaching the end of a slide is to advance to the next
    one in order (overwhelmingly the common case) and correct within a couple
    of seconds if the band went elsewhere. Charging for that window scores the
    correct strategy as a failure and rewards a system that freezes instead,
    which is worse for a congregation. What should be measured is whether the
    system corrects *promptly* — and `recovery_time_ms` already measures that
    directly.

    **`forgive_unpredictable` (default on) is a real switch, not a formality.**
    On, markers are derived from the timeline (`derive_deviation_markers`) when
    none are supplied, and `DEFAULT_DEVIATION_GRACE_S` of grace is applied. Off,
    this is the plain unforgiving measure — every earlier baseline in this repo
    was computed that way, so that is the setting to use when comparing against
    them.

    Keep the off switch. If musical features ever learn to *predict* an
    unexpected transition — the open idea in `Plan/MUSIC_LITERATURE.md` — then
    the jumps excused here are precisely the events such a model would be
    demonstrating skill on, and a scorer that always excused them could not
    show the difference. Forgiving by default measures today's system fairly;
    the strict mode is what a future one would have to beat.

    **`forgive_identical_slides` (default on) needs `setlist_content`.** Two
    slides carrying the same words are the same thing on screen, and charging
    for which copy is displayed measures an index rather than the display —
    see `identical_slide_groups`. Without `setlist_content` there is no way to
    know, and this silently has no effect; pass the recording's snapshot to
    turn it on in practice. Off, the comparison is exact-index, which is what
    every number in this repo predating 2026-09-04 used.
    """
    if len(ground_truth) < 2:
        return 0.0
    start = ground_truth[0].t_seconds
    end = ground_truth[-1].t_seconds
    total = end - start
    if total <= 0:
        return 0.0

    # Resolve the forgiveness settings. Explicit markers always win — a
    # recording that carries hand-annotated ones knows better than the
    # timeline heuristic does.
    if forgive_unpredictable:
        if deviation_markers is None:
            deviation_markers = derive_deviation_markers(ground_truth)
        if grace_seconds is None:
            grace_seconds = DEFAULT_DEVIATION_GRACE_S
    else:
        deviation_markers = ()
        grace_seconds = 0.0
    if grace_seconds is None:
        grace_seconds = 0.0

    # Slides carrying the same words are the same thing on screen. Needs
    # `setlist_content`; without it we cannot know, and fall back to
    # comparing indices exactly rather than guessing.
    equivalent: dict[tuple[int, int], frozenset[int]] = {}
    if forgive_identical_slides and setlist_content:
        equivalent = identical_slide_groups(setlist_content)

    # Cues asking for a wordless slide are not scored by default — the word
    # tracker cannot produce one, and blanking is a separate, disabled
    # feature. `blank_cue_windows` explains the reasoning and reports the
    # time so it is excluded rather than lost.
    blank_windows: list[tuple[float, float]] = []
    if not score_blank_cues and setlist_content:
        blank_windows = blank_cue_windows(ground_truth, setlist_content)

    def showing_the_right_thing(
        g: tuple[int, int] | None, d: tuple[int, int] | None
    ) -> bool:
        if g is None or d is None:
            return False
        if g == d:
            return True
        return d[0] == g[0] and d[1] in equivalent.get(g, frozenset())

    # Build the merged event timeline.
    events: list[tuple[float, str, tuple[int, int]]] = []
    for g in ground_truth:
        events.append((g.t_seconds, "g", (g.item_idx, g.slide_idx)))
    for d in decisions:
        events.append((d.t_seconds, "d", (d.item_idx, d.slide_idx)))
    events.sort(key=lambda e: (e[0], 0 if e[1] == "g" else 1))

    # Windows after each deviation during which no system could yet know.
    excused: list[tuple[float, float]] = list(blank_windows)
    if grace_seconds > 0:
        excused += [
            (m.t_seconds, m.t_seconds + grace_seconds) for m in deviation_markers
        ]
    excused.sort()

    def excused_overlap(lo: float, hi: float) -> float:
        """Seconds of [lo, hi) falling inside an excused window."""
        out = 0.0
        for a, b in excused:
            if b <= lo:
                continue
            if a >= hi:
                break
            out += min(hi, b) - max(lo, a)
        return out

    current_g: tuple[int, int] | None = None
    current_d: tuple[int, int] | None = None
    last_t = start
    wrong_time = 0.0
    excused_time = 0.0

    for t, kind, ident in events:
        if t > end:
            break
        if t > last_t:
            # Accumulate the interval [last_t, t) using prior state.
            if t > start:
                clamped_lo = max(last_t, start)
                clamped_hi = min(t, end)
                if clamped_hi > clamped_lo and not showing_the_right_thing(
                    current_g, current_d
                ):
                    wrong_time += clamped_hi - clamped_lo
                    excused_time += excused_overlap(clamped_lo, clamped_hi)
            last_t = t
        if kind == "g":
            current_g = ident
        else:
            current_d = ident

    # Tail: from last event to end of recording.
    if end > last_t and not showing_the_right_thing(current_g, current_d):
        wrong_time += end - last_t
        excused_time += excused_overlap(last_t, end)

    # Excused time leaves both the numerator and the denominator: it is not
    # scored either way, rather than being counted as correct.
    scored = total - min(excused_time, total)
    if scored <= 0:
        return 0.0
    return max(0.0, wrong_time - excused_time) / scored


@dataclass(frozen=True)
class PerceptualWeights:
    """How much each kind of wrongness actually costs a person watching.

    `wrong_slide_rate` is binary and symmetric: every second the displayed
    slide differs from the golden's counts the same, whether the screen is one
    line ahead, four slides adrift, or showing a different song. Measured over
    the seven approved goldens, that flattens a very uneven distribution —
    **48% of all wrong time is the adjacent slide**, 23% is 4+ away, and only
    5% is a different song. A congregation reads on comfortably when the screen
    is one line out; a different song is disorienting. Scoring them alike
    measures the metric's convenience, not the experience.

    Slide changes are judged against the *singing*, not the operator's click
    (`eval.sung_timing`): from the moment the band starts the current slide's
    last line until it sings the next slide's first word, either slide is
    right. After that first word, still showing the old slide is late — mildly
    for `late_grace_seconds`, then fully.

    Every number here is a judgement about perception, not a measurement.
    They are gathered in one place, and named, so that is obvious.
    """

    #: Switching to the next slide before the last line of the current one has
    #: started hides a line still to be sung. The penalty ramps from nothing at
    #: the last line's start to full this far before it — no cliff, because the
    #: measured start is good to a few tenths of a second, not a hundredth.
    early_ramp_seconds: float = 1.0
    #: Still showing the previous slide once the next one's first word is
    #: sung: this cheap for this long ...
    late_grace_seconds: float = 1.0
    late_small_weight: float = 0.25
    #: ... and this expensive after. The congregation is reading words the band
    #: has finished, and every second of it is visible.
    late_weight: float = 1.0
    #: With no sung timing (no transcript), the last line is assumed to start
    #: this long before the cue.
    assumed_last_line_seconds: float = 2.0

    #: Same song, one slide out. The single largest bucket, and the mildest:
    #: the words above or below the ones being sung are still the right song
    #: and usually still on the same screen in the reader's memory.
    adjacent_weight: float = 0.3
    #: Same song, 2-3 slides out. Recoverable but visibly wrong.
    near_weight: float = 0.7
    #: Same song, far away — no better than a different song, from the pew.
    far_weight: float = 1.0
    #: A different song entirely.
    wrong_song_weight: float = 1.0
    #: Nothing committed yet.
    nothing_shown_weight: float = 1.0

    #: Two commits closer together than this read as a flicker. Highly visible
    #: — and invisible to `wrong_slide_rate`, which a fast wrong->right
    #: correction can actually *improve*.
    flicker_within_seconds: float = 2.0
    #: What one flicker costs, expressed as equivalent seconds of wrong screen.
    #: Pure judgement: a visible double-change is about as irritating as a
    #: couple of seconds on the wrong slide.
    flicker_cost_seconds: float = 2.0


@dataclass(frozen=True)
class PerceptualBreakdown:
    """`score` plus where it came from.

    Decomposed on purpose. A single number invites tuning against the number;
    the components say which *kind* of wrongness to go and fix.
    """

    score: float
    scored_seconds: float
    #: Next slide shown before the current slide's last line started.
    early_seconds: float
    early_penalty: float
    #: Previous slide still shown after the new slide's first word.
    late_seconds: float
    late_penalty: float
    adjacent_seconds: float
    near_seconds: float
    far_seconds: float
    wrong_song_seconds: float
    flicker_count: int
    flicker_penalty: float
    #: Either slide was fine here: inside a last line, before the first word.
    transition_seconds: float = 0.0
    nothing_seconds: float = 0.0

    @property
    def late_rate(self) -> float:
        return self.late_penalty / self.scored_seconds if self.scored_seconds else 0.0

    @property
    def early_rate(self) -> float:
        return self.early_penalty / self.scored_seconds if self.scored_seconds else 0.0

    def render(self) -> str:
        w = PerceptualWeights()
        rows = [
            ("early (before the last line)", self.early_seconds, self.early_penalty),
            ("late (after the first word)", self.late_seconds, self.late_penalty),
            ("adjacent slide", self.adjacent_seconds, self.adjacent_seconds * w.adjacent_weight),
            ("2-3 slides away", self.near_seconds, self.near_seconds * w.near_weight),
            ("4+ slides away", self.far_seconds, self.far_seconds * w.far_weight),
            ("a different song", self.wrong_song_seconds,
             self.wrong_song_seconds * w.wrong_song_weight),
            ("nothing shown", self.nothing_seconds, self.nothing_seconds * w.nothing_shown_weight),
            (f"flicker ({self.flicker_count} double-changes)", 0.0, self.flicker_penalty),
        ]
        out = [f"perceptual score {self.score:.3f} over {self.scored_seconds:.0f}s"
               f" ({self.transition_seconds:.0f}s in free transitions)"]
        for label, secs, pen in rows:
            out.append(f"   {pen:7.1f} penalty  ({secs:6.1f}s)  {label}")
        return "\n".join(out)


def _early_weight(before_last_line: float, w: PerceptualWeights) -> float:
    """Penalty for showing the next slide this long before its last line starts."""
    if before_last_line <= 0:
        return 0.0
    if w.early_ramp_seconds <= 0 or before_last_line >= w.early_ramp_seconds:
        return 1.0
    return before_last_line / w.early_ramp_seconds


def _late_weight(after_first_word: float, w: PerceptualWeights) -> float:
    """Penalty for still showing the previous slide this long after the new
    slide's first word was sung."""
    if after_first_word < 0:
        return 0.0
    if after_first_word < w.late_grace_seconds:
        return w.late_small_weight
    return w.late_weight


def perceptual_score(
    decisions: Sequence[Decision],
    ground_truth: Sequence[GroundTruthEvent],
    setlist_content: Sequence[SetlistItemSnapshot],
    *,
    timings: Sequence[CueTiming] | None = None,
    weights: PerceptualWeights | None = None,
) -> PerceptualBreakdown:
    """What the room would say, rather than what the clock says.

    Reported **alongside** `wrong_slide_rate`, never instead of it. Each
    softening here makes the number prettier and less able to detect a
    regression, and this repo has already added three (jump grace, identical
    slides, blank cues). `wrong_slide_rate` with `forgive_unpredictable=False`
    stays the unforgiving guard; this is the one that should track whether the
    app feels right.

    `timings` (`eval.sung_timing.cue_timings`, one per cue) puts each slide
    change where the band sang it. The golden's slide *order* is kept; its
    click *times* only stand in where there is no timing, with the last line
    assumed to start `assumed_last_line_seconds` before the click.

    Shares `wrong_slide_rate`'s conventions where they still apply: blank cues
    are not the word tracker's to answer, and slides carrying identical words
    are the same thing on screen.
    """
    w = weights or PerceptualWeights()
    gt = sorted(ground_truth, key=lambda g: g.t_seconds)
    dec = sorted(decisions, key=lambda d: d.t_seconds)
    if len(gt) < 2:
        return PerceptualBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0)
    if timings is None:
        timings = [
            CueTiming(g.t_seconds, g.t_seconds, g.t_seconds - w.assumed_last_line_seconds,
                      False, False)
            for g in gt
        ]
    if len(timings) != len(gt):
        raise ValueError("one timing per ground-truth cue")

    equivalent = identical_slide_groups(setlist_content)
    slides_by_item = {i.item_idx: i.slides for i in setlist_content}

    # The band's own boundaries: each slide runs from its first word to the
    # next slide's. Kept in order (a mis-timed cue can't reorder the song).
    starts: list[float] = []
    for tm in timings:
        starts.append(max(tm.first_word, starts[-1] + 0.05) if starts else tm.first_word)
    last_line = [min(tm.last_line, s) for tm, s in zip(timings, starts, strict=True)]

    def matches(shown: tuple[int, int] | None, want: tuple[int, int]) -> bool:
        if shown is None:
            return False
        return shown[0] == want[0] and shown[1] in equivalent.get(
            want, frozenset({want[1]})
        )

    def wordless(slide: tuple[int, int]) -> bool:
        s = slides_by_item.get(slide[0]) or []
        return slide[1] >= len(s) or not s[slide[1]]

    def displayed_at(t: float) -> tuple[int, int] | None:
        cur = None
        for d in dec:
            if d.t_seconds <= t:
                cur = (d.item_idx, d.slide_idx)
            else:
                break
        return cur

    scored = penalty = 0.0
    early_s = early_p = late_s = late_p = free_s = none_s = 0.0
    adj_s = near_s = far_s = song_s = 0.0

    for i, ev in enumerate(gt[:-1]):
        t0, t1 = starts[i], starts[i + 1]
        want = (ev.item_idx, ev.slide_idx)
        if wordless(want) or t1 <= t0:
            continue  # blank cue — a separate feature's job
        prev = (gt[i - 1].item_idx, gt[i - 1].slide_idx) if i else None
        nxt = (gt[i + 1].item_idx, gt[i + 1].slide_idx)
        next_last = last_line[i + 1]

        cuts = {t0, t1, t0 + w.late_grace_seconds, next_last, next_last - w.early_ramp_seconds}
        cuts |= {d.t_seconds for d in dec}
        bounds = sorted(x for x in cuts if t0 <= x <= t1)
        for a, b in pairwise(bounds):
            dur = b - a
            mid = (a + b) / 2
            scored += dur
            shown = displayed_at(a)
            if matches(shown, want):
                continue
            # A slide change: the old slide after the new one's first word,
            # or the next one before this one's last line. Where a slide is
            # both (A B A), the kinder reading wins.
            weights_here = []
            if prev is not None and matches(shown, prev):
                weights_here.append(("late", _late_weight(mid - t0, w)))
            if matches(shown, nxt):
                weights_here.append(("early", _early_weight(next_last - mid, w)))
            if weights_here:
                kind, weight = min(weights_here, key=lambda x: x[1])
                if weight == 0.0:
                    free_s += dur
                elif kind == "late":
                    late_s += dur
                    late_p += dur * weight
                else:
                    early_s += dur
                    early_p += dur * weight
                penalty += dur * weight
                continue
            if shown is None:
                none_s += dur
                penalty += dur * w.nothing_shown_weight
                continue
            if shown[0] != want[0]:
                song_s += dur
                penalty += dur * w.wrong_song_weight
            else:
                gap = abs(shown[1] - want[1])
                if gap == 1:
                    adj_s += dur
                    penalty += dur * w.adjacent_weight
                elif gap <= 3:
                    near_s += dur
                    penalty += dur * w.near_weight
                else:
                    far_s += dur
                    penalty += dur * w.far_weight

    flicker = sum(
        1
        for x, y in pairwise(dec)
        if y.t_seconds - x.t_seconds < w.flicker_within_seconds
    )
    flicker_p = flicker * w.flicker_cost_seconds
    penalty += flicker_p

    return PerceptualBreakdown(
        score=penalty / scored if scored else 0.0,
        scored_seconds=scored,
        early_seconds=early_s,
        early_penalty=early_p,
        late_seconds=late_s,
        late_penalty=late_p,
        adjacent_seconds=adj_s,
        near_seconds=near_s,
        far_seconds=far_s,
        wrong_song_seconds=song_s,
        flicker_count=flicker,
        flicker_penalty=flicker_p,
        transition_seconds=free_s,
        nothing_seconds=none_s,
    )


def false_lost_rate(
    lost_episodes: Sequence[tuple[float, float]],
    deviation_markers: Sequence[DeviationMarker],
    *,
    attribution_window_s: float = 6.0,
) -> float:
    """Lost-mode entries per song that no deviation explains.

    Lost mode is not a failure in itself — it is the correct response to the
    band doing something unexpected, and a repeat-heavy song should produce
    several. What matters is entering it when nothing happened, because the
    tracker then holds the slide, and to an operator a deliberate hold is
    indistinguishable from a hang.

    An episode counts as explained if it *begins* within
    `attribution_window_s` after a deviation. The window is one-sided on
    purpose: detection is debounced and evidence takes time to accumulate, so
    a genuine deviation is noticed a few seconds late — but an episode
    starting *before* the deviation cannot have been caused by it.

    Returns a count rather than a ratio: the Phase 7 criterion is "< 1 per
    song", and dividing by the number of deviations would flatter a recording
    that happens to contain many.
    """
    spurious = 0
    for start, _end in lost_episodes:
        explained = any(
            0.0 <= (start - m.t_seconds) <= attribution_window_s
            for m in deviation_markers
        )
        if not explained:
            spurious += 1
    return float(spurious)


def recovery_time_ms(
    decisions: Sequence[Decision],
    ground_truth: Sequence[GroundTruthEvent],
    deviation_markers: Sequence[DeviationMarker],
) -> list[float | None]:
    """Per-deviation milliseconds-to-recovery.

    For each deviation marker at time `t_dev`:
      - Find the first decision at time `t_d >= t_dev` whose slide identity
        matches the ground-truth-current slide at that moment.
      - Recovery time = (t_d - t_dev) * 1000.
      - If no such decision exists, return `None` for that marker (never
        recovered within the recording).
    """
    out: list[float | None] = []
    sorted_gt = sorted(ground_truth, key=lambda g: g.t_seconds)
    sorted_decisions = sorted(decisions, key=lambda d: d.t_seconds)

    for marker in deviation_markers:
        recovered_at: float | None = None
        for d in sorted_decisions:
            if d.t_seconds < marker.t_seconds:
                continue
            # What does ground truth say "should be" displayed at d.t?
            gt_at_t = _ground_truth_at(sorted_gt, d.t_seconds)
            if gt_at_t is None:
                continue
            if (d.item_idx, d.slide_idx) == (gt_at_t.item_idx, gt_at_t.slide_idx):
                recovered_at = d.t_seconds
                break
        out.append(
            (recovered_at - marker.t_seconds) * 1000.0
            if recovered_at is not None
            else None
        )
    return out


def override_rate(
    decisions: Sequence[Decision],
    n_songs: int,
) -> float:
    """Overrides per song.

    `n_songs` is the number of distinct songs in the recording (passed in
    rather than derived because a recording with a 1-song setlist that
    repeats would otherwise look like 1 long song).
    """
    if n_songs <= 0:
        return 0.0
    count = sum(1 for d in decisions if d.source == "override")
    return count / n_songs


def _ground_truth_at(
    ground_truth: Sequence[GroundTruthEvent],
    t: float,
) -> GroundTruthEvent | None:
    """Return the most recent ground-truth event at or before `t`."""
    last: GroundTruthEvent | None = None
    for g in ground_truth:
        if g.t_seconds <= t:
            last = g
        else:
            break
    return last


# --- transcription quality ---------------------------------------------------


@dataclass(frozen=True)
class WerResult:
    """One word-error-rate computation.

    The breakdown of edits (substitutions / deletions / insertions) is what
    Phase 4 and Phase 9 need for diagnostics — e.g. is the model dropping
    quiet words (deletions) or hallucinating extras (insertions)?
    """

    wer: float
    substitutions: int
    deletions: int
    insertions: int
    n_reference_words: int
    n_hypothesis_words: int

    @property
    def edit_distance(self) -> int:
        return self.substitutions + self.deletions + self.insertions


def word_error_rate(
    hypothesis: Sequence[str],
    reference: Sequence[str],
) -> WerResult:
    """Standard WER: word-level edit distance / reference word count.

    Both inputs should be pre-tokenized (use `tokenize_for_wer` for the
    canonical tokenization that matches `Slide.words`). The Levenshtein
    DP is straightforward — we walk the (n+1)×(m+1) table and at the end
    backtrack to classify each edit as substitution, deletion, or
    insertion for the diagnostics breakdown.

    Edge cases:
      - Empty reference returns `wer=0.0` only when hypothesis is also
        empty; otherwise `wer=1.0` and every hypothesis word counts as
        an insertion. (Convention: reference-empty WER is undefined in
        the literature; we pick a deterministic answer rather than
        raising so batch eval doesn't bail mid-run.)
    """
    hyp = list(hypothesis)
    ref = list(reference)
    n = len(ref)
    m = len(hyp)

    if n == 0:
        wer = 0.0 if m == 0 else 1.0
        return WerResult(
            wer=wer,
            substitutions=0,
            deletions=0,
            insertions=m,
            n_reference_words=0,
            n_hypothesis_words=m,
        )

    # Wagner-Fischer DP table. Row 0: cost of inserting i hyp words.
    # Col 0: cost of deleting j ref words.
    dp: list[list[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(
                    dp[i - 1][j - 1],  # substitution
                    dp[i - 1][j],      # deletion
                    dp[i][j - 1],      # insertion
                )

    # Backtrack to count each edit type. Choose match > sub > del > ins
    # when ties exist so deterministic diagnostic counts.
    subs = dels = ins = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i -= 1
            j -= 1
            continue
        diag = dp[i - 1][j - 1] if i > 0 and j > 0 else None
        up = dp[i - 1][j] if i > 0 else None
        left = dp[i][j - 1] if j > 0 else None
        cur = dp[i][j]
        if diag is not None and diag + 1 == cur:
            subs += 1
            i -= 1
            j -= 1
        elif up is not None and up + 1 == cur:
            dels += 1
            i -= 1
        elif left is not None and left + 1 == cur:
            ins += 1
            j -= 1
        else:
            # Should be unreachable for a correct DP table.
            break

    return WerResult(
        wer=(subs + dels + ins) / n,
        substitutions=subs,
        deletions=dels,
        insertions=ins,
        n_reference_words=n,
        n_hypothesis_words=m,
    )


def tokenize_for_wer(text: str) -> list[str]:
    """Canonical WER tokenization — mirrors `Slide.words` exactly.

    Using the same tokenizer for hypothesis and reference makes WER
    comparable to the matcher's view of the lyrics. Numbers and
    punctuation collapse the same way on both sides.
    """
    return tokenize(text)
