"""The joint follower, live: one belief over (cell, tempo), fed as evidence arrives.

Ported from the music-only spike's `joint.follow`, which ran over a whole
recording at once. Here evidence is pushed as it arrives and the belief is
read out on demand, with the same arithmetic:

* every piece of evidence is placed at the *audio* time it describes, and
  used only once it has *arrived* (Whisper words ~2.7 s late, scored spans
  2-3 s, chords ~0.7 s);
* a checkpoint holds the belief up to the point where everything has
  arrived; each read-out re-filters the frames after it with whatever has
  arrived since, so a late word corrects the past instead of being dropped;
* the read-out projects the belief `lookahead_s` forward at each state's own
  tempo — anticipation — and shows the slide holding the most belief
  (slides with identical words pooled, ties to the slide already shown).

Each dense source is counted once per *observation*, not per frame (a chord
holds ~39 frames, so a per-frame weight counted it 39 times against a word's
once — the spike's "chords hurt" bug). Evidence that arrives after its
re-filter window closed cannot be placed and is counted in
`late_evidence` (the spike once dropped words like this silently).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np

from auto_presenter.follower.reference import SegmentReference
from auto_presenter.follower.talk import TalkGate

log = logging.getLogger(__name__)

FPS = 10.0
#: Per-frame weight of dense chord evidence: one BTC chord lasts ~39 frames,
#: and each chord counts twice. Chords are the fastest evidence (~0.7 s, words
#: and scored spans 2.6-3.9 s). On the 8 latest live traces: x1 0.150, x1.5
#: 0.140, x2 0.136, x3 0.139, x4 0.141; the gain is on the four recordings
#: that never set the weight (live set 0.219 -> 0.169, medley 0.136 -> 0.122),
#: neutral on the four that did.
CHORD_WEIGHT = 2 / 39.2
#: Per-frame weight of dense melody evidence (mix pitch): one note ~7.4 frames.
MELODY_WEIGHT = 1 / 7.4
#: A scored step covers a 6 s window every 2 s: three windows overlap each moment.
SCORED_WEIGHT = 2.0 / 6.0
#: A slide must hold this share of the projected belief to replace the one
#: shown. Without it the screen followed whichever of two look-alike lines led
#: by a hair (King Of My Heart: slide 7 shown on 40% belief, then five flips in
#: 10 s). Measured on the 4 live traces: flicker (A -> B -> A within 5 s)
#: 16 -> 3, wrong-slide 0.165 -> 0.176, median lateness +0.18 s.
MIN_SWITCH_SHARE = 0.5
#: When no line of this song has been heard (a scored step at or above the
#: null level) for this many seconds, a slide needs `HOLD_MIN_SHARE` of the
#: belief to replace the one shown, not `MIN_SWITCH_SHARE`. Before the song
#: starts and after it ends nothing of it is sung, and the follower kept
#: moving through it (the live set's Awesome God intro, the four minutes after
#: Build My Life); but where the words can't be made out the music must still
#: be able to move the screen, which a complete hold stopped. None: no hold.
LYRIC_HOLD_S: float | None = 8.0
HOLD_MIN_SHARE: float | None = 0.8
#: Per frame, the share of the belief that may jump from mid-line to any
#: line's start (jumps are otherwise only at a line's end). 0: off.
ANYWHERE_JUMP = 0.0
#: At the end of a slide that ends with a tag (`SegmentReference.tag_restart`),
#: this share of what leaves goes back to the tag's start: bands repeat a
#: song's tag ("nothing but the blood of Jesus"). The words decide which.
#: Medley 0.206 -> 0.151 (its "neither" 0.087 -> 0.029); others unchanged;
#: the same at 0.7. With counted loops (`reference.LOOPS`, on) a tag is a
#: counted loop instead and this applies only where loops are off.
TAG_REPEAT = 0.5
#: When the shown slide holds less than this share of the projected belief,
#: the leading slide replaces it even under `MIN_SWITCH_SHARE` (if it holds
#: at least `COLLAPSE_LEAD` times the shown slide's share). Five verses of I
#: Surrender All open alike: the next verse led on 34% while the shown
#: refrain held 1%, and the screen kept the refrain until the verse's own
#: words. With `FIRST_LINE_UNHELD`, on the 8 latest live traces: wrong-slide
#: 0.156 -> 0.150, mistakes neither human nor lag 72 -> 60 s; the same at
#: 0.1-0.3. Not while the lyric hold is on. 0: off.
COLLAPSE_SHARE = 0.2
COLLAPSE_LEAD = 2.0
#: While the lyric hold is on, the move from the blank to the song's first
#: line still needs only `MIN_SWITCH_SHARE`: nothing has been heard *because*
#: the song is only now starting (I Surrender All: the blank held 3.4 s into
#: the first line with no lag at all). False: the hold's bar applies.
FIRST_LINE_UNHELD = True
#: While someone is talking rather than singing this song (`follower.talk`),
#: the screen holds — the first move off the blank included — until the
#: song's words are heard again. False: talk moves the screen like singing.
TALK_GATE = True
#: The screen leaves the blank only once a phrase of the song's lyrics has
#: been heard (with `TALK_GATE`): talk at the start of a recording moved it
#: to the first line 5 s in, before there was enough talk to judge.
BLANK_NEEDS_LYRICS = True
#: For this long after the screen changed, going straight back to the slide
#: it left needs `FLIP_BACK_SHARE` of the belief, not `MIN_SWITCH_SHARE`. The
#: 2 s lookahead sometimes reaches the next slide while the band is still
#: finishing this one; the next evidence pulled it back and it went forward
#: again a second later (Build My Life 3 -> 4 -> 3 -> 4 in 4 s). 0: off.
FLIP_BACK_S = 4.0
FLIP_BACK_SHARE = 0.8
#: For this long after the scorer heard a line that the shown slide carries,
#: the collapse rule may not move the screen: only a clear majority can. The
#: medley's slide 4 ends "nothing but the blood of Jesus", as slides 1 and 2
#: do; through a 38 s vamp on it the belief spread over all three endings and
#: the collapse rule moved the screen to slide 1, which carried the same line.
#: 8 live traces, wrong / perceived: lead sheet 0.131 / 0.157 -> 0.130 /
#: 0.156, library slides unchanged, tunes learned in service 0.208 / 0.261 ->
#: 0.205 / 0.253 (4 and 8 s alike). 0: off.
HOLD_HEARD_LINE_S = 8.0
#: A step on to the next slide waits until the shown slide's last words have
#: been heard, or the next slide's opening has. The 2 s lookahead and a tempo
#: estimate that runs fast left slides whose last line had not started ("I am
#: unfulfilled without full communion": 13 s on the wrong slide). Measured on
#: 751 recorded moves (docs/roadmap/ERROR_HUNT_2026-11-12.md): held 76% of the steps
#: made before a slide's last words, and 8% of good steps. Real-time gate run:
#: early exits 20 -> 7, sung line hidden 0.23 -> 0.19, wrong-slide unchanged.
#: AUTO_PRESENTER_LAST_LINE=0 turns it off for comparison runs.
LEAVE_NEEDS_LAST_LINE = os.environ.get("AUTO_PRESENTER_LAST_LINE", "1") != "0"
#: A span over the slide's last `TAIL_WORDS` words scoring at least
#: `TAIL_LEVEL` within `TAIL_HEARD_S` counts as its last words heard.
TAIL_WORDS = 3
TAIL_LEVEL = -4.0
TAIL_HEARD_S = 6.0
#: A span ending in the next slide's first words (2nd to 6th) scoring at
#: least `OPENING_LEVEL` within `OPENING_HEARD_S` counts as its opening heard.
OPENING_LEVEL = -3.0
OPENING_HEARD_S = 3.5
#: The wait ends once the follower has wanted to step on for this long in all
#: (read-outs up to 1 s apart count), whatever was heard: a last line the
#: scorer can't make out must not pin the screen.
LEAVE_WAIT_MAX_S = 6.0
#: The rule needs the line scorer: with no scores this recently it stands aside.
SCORES_FRESH_S = 5.0
#: A slide whose words are the start of another's (a line with one more "we
#: live for you" the second time) is the same place to the ear. When the
#: belief sits on the twin that is out of order, or one step on from it, show
#: the copy reached in order. The band sang the longer one at the shorter
#: one's place and the screen skipped a slide (Build My Life, both recordings
#: of it; 32 library songs have such a pair). Real-time: that recording's
#: wrong-slide 0.183 -> 0.158. AUTO_PRESENTER_TWINS=0 turns it off.
TWINS_IN_ORDER = os.environ.get("AUTO_PRESENTER_TWINS", "1") != "0"
#: A read-out more than this far behind its checkpoint jumps it forward
#: rather than stepping through every frame between.
MAX_CATCH_UP_S = 30.0
#: Evidence-proposed jumps: a scorer step moves this share of the belief,
#: times (1 - its normalised entropy) ** JUMP_SHARPNESS, to where its own
#: evidence points (cells weighted by exp(evidence)). A step sure of one line
#: (entropy ~ 0) proposes a jump there; a confused one proposes nothing. 0: off.
#:
#: Why: jumps were only possible at a line's end, so a band breaking out of a
#: vamp mid-line could not be followed however clear the next line was
#: (medley: "what can i say", scored -0.7 and on one slide only, took 16 s).
#: A uniform jump from anywhere fixed that but leaked belief everywhere
#: (tuning songs 0.173 -> 0.182). Proposed by the evidence, weighted by how
#: unmistakable it is: 8 live traces, tuning 0.173 -> 0.171, held-out
#: 0.300 -> 0.266 (medley 0.454 -> 0.380, set 0.481 -> 0.461), flicker <= 3.
#: Flat from 2% to 10% (held-out 0.266-0.270).
TARGETED_JUMP = 0.05
JUMP_SHARPNESS = 1.0
#: Floor added to each copy's nearby belief when a jump picks among copies of
#: a repeated slide (`OnlineFollower._near_copies`); smaller favours the
#: near copy more. 0: off (even spread).
COPY_NEAR = 0.1
#: Showing one of several identical slides, another copy must hold this many
#: times its belief to take over (`OnlineFollower._copy_on_order`).
COPY_SWITCH_LEAD = 2.0
#: The next-line check: near the end of the line the band is on, ask a
#: closed-set scorer which of two or three likely next lines it has just
#: started (the written next one, the order prior's runner-up, the start of
#: the section, e.g. the chorus again), scoring "this line's last words +
#: each candidate's first words" on the last few seconds of audio. Words
#: reach the follower 3.5-5 s after they are sung; this answers ~1 s after
#: the band's first word (spike, 14 recordings, 1 s of the new line: 80%
#: right when the band follows the order, 61% when it doesn't, 77 ms a check).
#: Its answer is evidence on the candidates' opening cells, losers marked
#: down by NEXT_LINE_WEIGHT per unit of score, and with a clear winner a jump
#: of NEXT_LINE_JUMP of the belief onto the winner's opening. 0: off.
#:
#: Replayed on the live traces with the check run on each recording's audio
#: (library follower, small.en, a check at most every 0.75 s): lyric-video
#: Set 1 0.382 -> 0.350 perceived, the 9 approved goldens 0.161 -> 0.161
#: (none worse than 0.005) at weight 6, jumps off. Weight 3 0.355 / 0.162.
#: Jumps (0.3) reach 0.344 on Set 1 but cost the approved 0.169 (rec_20260818-215943
#: 0.107 -> 0.186); jumping only off-order was worse on both (0.367 / 0.172).
#: Not wired into the live server yet, so off.
NEXT_LINE_WEIGHT = 0.0
NEXT_LINE_JUMP = 0.0
#: The winner must lead the runner-up by this much (mean log-probability per
#: token) to propose a jump.
NEXT_LINE_JUMP_MARGIN = 0.5
#: Jump only to a line the order prior didn't expect next (a repeat, a
#: return): the expected one needs no jump, only the evidence.
NEXT_LINE_JUMP_OFF_ORDER = False
#: Words of each candidate's opening, and of the current line's tail.
NEXT_LINE_OPENING_WORDS = 3
NEXT_LINE_TAIL_WORDS = 4
#: Ask once this share of the current line's belief is past its midpoint.
NEXT_LINE_DUE = 0.5
#: When a new line wins, also mark down the line being left (by the
#: winner's lead over "still on this line"): marking down only the losing
#: candidates never says the band has left, so a right answer could not
#: move the screen before the words arrived.
NEXT_LINE_LEAVE = False
#: ...and only while a line has been heard this recently (seconds of
#: arrival): in an instrumental there is no next line to tell apart.
NEXT_LINE_HEARD_S = 8.0


@dataclass(frozen=True)
class NextLineQuery:
    """What the next-line check should score: `spans[i]` is the current
    line's tail followed by candidate i's opening; `slides[i]` every slide
    that opens with those words. The spans whose `slides` are empty are
    "still on this line" (runs of its own words, from anywhere in it): when
    one of them wins, the band hasn't started a new line yet and the answer
    says nothing."""

    anchor: int
    spans: list[str]
    slides: list[frozenset[int]]
    n_open_words: list[int]
    #: Index of the candidate the order prior expects next, or None.
    expected: int | None = None
    #: The line each span is asked from (the line being left).
    anchors: list[int] = field(default_factory=list)


#: The intro (leading blank) leaks into the song this fast, per frame...
INTRO_LEAVE = 0.01
#: ...mostly into its first line, but a band may start anywhere (a chorus
#: first is common), so this share spreads over every line's start; the
#: evidence then picks which.
INTRO_ANYWHERE = 0.3

#: **The band waits.** Bands hold a slide through instrumental stretches
#: longer than the sheet's gap between lines (Reckless Love: slide 8 held
#: 28 s, 15 of them instrumental), and a tempo cannot stand still (40 bpm at
#: the slowest), so the belief walked on into lines nobody sang and then
#: landed on the wrong copy of a shared line. Each segment gets a *wait*
#: state after its end: of the belief leaving the segment, `WAIT_ENTER` goes
#: there instead of to the next line; it holds (no tempo) and leaks to where
#: the segment would have gone at `WAIT_LEAVE` per frame. It shows the
#: segment's slide. Only a line's end can wait (after the sheet's own gap).
#: Wait states are not sung (`VOCAL_*`); for every other source they read
#: like the segment's last cell. None: no wait states. They exist only for a
#: follower with a vocal-activity source (`OnlineFollower.vocal_source`).
#:
#: The prior must be small and the leak slow: the wait is a hypothesis the
#: *evidence* of silence makes (2.6 nats a second against a sung cell), not
#: one the prior should push. Measured on the 8 live traces with offline
#: Demucs vocal activity 3 s late: enter 0.3 cost every line change
#: (tuning 0.173 -> 0.176); leave 0.02 per frame let ~45% of a correct wait
#: drain into the next line over the 3 s no vocal evidence has arrived for
#: yet (I Surrender All: 50/50 at "now" while 100% waiting at the checkpoint).
#: Result (enter 0.05, leave 0.005, HOLD_MIN_SHARE 0.65): tuning songs
#: 0.173 -> 0.150 (rl 0.257 -> 0.228, isa 0.202 -> 0.161, komh 0.099 ->
#: 0.078), held-out 0.300 -> 0.305 (since 0.063 -> 0.073, medley +0.010; set
#: and bml unchanged). Off until a live vocal-activity source exists.
WAIT_ENTER: float | None = None  # recommended 0.05 (with a vocal source)
WAIT_LEAVE = 0.005
#: Vocal activity (`push_vocal`, P(singing) per frame) against the sheet's
#: sung cells: the detector says "singing" with P `VOCAL_HIT` over a sung
#: cell and `VOCAL_FALSE` over an unsung one (gap, wait, intro). Asymmetric
#: on purpose: a voice over an unsung cell is common (ad-libs at a line's
#: end, talk over the band), so singing is weak evidence for a sung cell
#: (log 0.95/0.7) and silence strong evidence against one (log 0.05/0.3).
#: At 0.9/0.2 the "yeah, yeah" after Reckless Love's slide 8 pushed the belief
#: into line 9 before the silence was heard. Per-frame weight: frames of a
#: smoothed detector are far from independent.
VOCAL_WEIGHT = 0.15
VOCAL_HIT = 0.95
VOCAL_FALSE = 0.7


class SegmentTransition:
    """Advance within a segment at each tempo; at a segment's end, route to the
    start of the next by the order prior mixed with a small uniform jump. The
    intro cell holds and leaks slowly into the first line. With `wait_enter`,
    each segment also has a wait state (columns `P..P+n_seg-1`)."""

    def __init__(
        self,
        ref: SegmentReference,
        fps: float,
        p_jump: float,
        p_rate: float,
        wait_enter: float | np.ndarray | None = None,
        wait_leave: float = WAIT_LEAVE,
    ):
        self.ref = ref
        self.P = ref.P
        n_seg_all = len(ref.jump_targets)
        if wait_enter is None:
            self.n_wait = 0
        else:
            self.n_wait = n_seg_all
            enter = np.broadcast_to(np.asarray(wait_enter, np.float32), (n_seg_all,)).copy()
            enter[0] = 0.0  # the intro holds by itself
            self.wait_enter = enter[None, :]
            self.wait_leave = float(wait_leave)
        self.N = self.P + self.n_wait
        v = ref.rates / fps
        self.lo = np.floor(v).astype(int)
        self.f = (v - self.lo).astype(np.float32)
        self.p_rate = p_rate
        self.seg = np.asarray(ref.seg_of)
        n_seg = len(ref.jump_targets)
        prior = ref.succ.copy()
        # uniform jumps land on sung segments, never back into the intro
        # one landing place per written segment: a loop's further passes are
        # reached by singing the line again, not by a jump (with them, a jump
        # out of the medley's verse landed 90% on copies of its tag)
        written = getattr(ref, "written_cell", None)
        landing = np.ones(n_seg, bool)
        if written is not None:
            landing = np.asarray(written)[np.asarray(ref.jump_targets)]
        landing[0] = False
        jump = np.where(landing, 1.0 / max(1, int(landing.sum())), 0.0)
        succ = (1 - p_jump) * prior + p_jump * jump[None, :]
        succ[0] = prior[0]  # the intro only ever leaves to the first line
        self.succ = (succ / succ.sum(axis=1, keepdims=True)).astype(np.float32)
        self.starts = np.asarray(ref.jump_targets)
        self.intro = ref.intro_cell
        self.first = int(ref.jump_targets[ref.first_segment])
        sung_starts = self.starts[1:][landing[1:]]
        self.intro_leak = np.zeros(ref.P, np.float32)
        if len(sung_starts):
            self.intro_leak[sung_starts] += INTRO_ANYWHERE / len(sung_starts)
            self.intro_leak[self.first] += 1 - INTRO_ANYWHERE
        else:
            self.intro_leak[self.intro] = 1.0
        n_cells = ref.P
        self.shifts = []
        for k in range(1, int(self.lo.max()) + 2):
            w = (
                np.where(self.lo == k, 1 - self.f, 0.0) + np.where(self.lo + 1 == k, self.f, 0.0)
            ).astype(np.float32)
            if not w.any():
                continue
            stay = np.zeros(n_cells, bool)
            stay[: n_cells - k] = self.seg[k:] == self.seg[: n_cells - k]
            self.shifts.append((k, w[:, None], stay))
        self.w0 = np.where(self.lo == 0, 1 - self.f, 0.0).astype(np.float32)[:, None]
        tags = sorted(getattr(ref, "tag_restart", {}).items())
        self.tag_segs = np.array([g for g, _c in tags], int)
        self.tag_cells = np.array([c for _g, c in tags], int)

    def step(self, x: np.ndarray, hold: bool = False) -> np.ndarray:
        p = self.p_rate
        y = (1 - 2 * p) * x
        y[1:] += p * x[:-1]
        y[:-1] += p * x[1:]
        y[0] += p * x[0]
        y[-1] += p * x[-1]
        wait = y[:, self.P :] if self.n_wait else None
        x = y[:, : self.P]
        intro = x[:, self.intro].copy()
        x[:, self.intro] = 0.0
        out = self.w0 * x
        over = np.zeros((x.shape[0], len(self.starts)), np.float32)
        for k, w, stay in self.shifts:
            moved = w * x
            out[:, k:] += np.where(stay[None, : x.shape[1] - k], moved[:, : x.shape[1] - k], 0.0)
            leaving = np.where(~stay[None, :], moved, 0.0)
            # segments are contiguous runs of cells, in order
            over += np.add.reduceat(leaving, self.starts, axis=1)
        if wait is not None:
            # a segment's end: some of what leaves waits; the wait leaks on
            new_wait = wait * (1 - self.wait_leave) + over * self.wait_enter
            over = over * (1 - self.wait_enter) + wait * self.wait_leave
        if TAG_REPEAT > 0 and len(self.tag_segs):
            again = over[:, self.tag_segs] * TAG_REPEAT
            over[:, self.tag_segs] -= again
            out[:, self.tag_cells] += again
        out[:, self.starts] += over @ self.succ
        out[:, self.intro] += intro * (1 - INTRO_LEAVE)
        out += (intro * INTRO_LEAVE)[:, None] * self.intro_leak[None, :]
        if ANYWHERE_JUMP > 0:
            # the band can leave a line mid-way (break out of a vamp, jump to a
            # chorus): a little of every sung cell may go to any line's start,
            # so clear evidence for that line has something to pull on
            sung = out.copy()
            sung[:, self.intro] = 0.0
            moved = ANYWHERE_JUMP * sung
            out -= moved
            n_starts = max(1, len(self.starts) - 1)
            out[:, self.starts[1:]] += moved.sum(axis=1, keepdims=True) / n_starts
        if wait is not None:
            out = np.concatenate([out, new_wait.astype(np.float32)], axis=1)
        return out


@dataclass
class Readout:
    slide: int | None
    #: The shown slide's share of the projected belief (identical slides pooled).
    share: float
    #: Belief over cells at "now" (before the lookahead).
    marginal_now: np.ndarray
    #: Mean tempo, quarter notes per second.
    tempo_qps: float
    #: Mean speed through the reference, cells per second.
    cells_per_s: float = 0.0


@dataclass
class OnlineFollower:
    ref: SegmentReference
    #: slide -> slides with identical words (pooled at read-out).
    identical: dict[int, set[int]] = field(default_factory=dict)
    #: 2026-10-24, scored on the band's sung timing (`eval.sung_timing`: any
    #: switch inside the last line is free, late after the next first word is
    #: not), 8 trace replays: library references 0.8 s 0.248 -> 2.0 s 0.210,
    #: lead sheets 0.163 -> 0.155 (1.5 s 0.153, inside noise). Lateness that
    #: is left is recognition (off-order repeats, unsure lines), which no
    #: lookahead reaches: 4 s still leaves ~0.05 of it.
    lookahead_s: float = 2.0
    #: How far back a read-out re-filters: must cover the latest evidence's
    #: delay (scored spans up to ~6.5 s after the audio they describe).
    refilter_s: float = 8.0
    p_jump: float = 0.03
    p_rate: float = 0.02
    fps: float = FPS
    #: Wait states (`WAIT_ENTER`) only where something can say nobody is
    #: singing (a vocal-activity source feeding `push_vocal`): with no such
    #: evidence they are only a delayed copy of the belief, and cost time at
    #: every line change (Build My Life, no stems: 0.201 -> 0.209).
    vocal_source: bool = False

    def __post_init__(self) -> None:
        for a, group in self.identical.items():
            self.ref.canon[a] = min({a, *group})
        enter = WAIT_ENTER if self.vocal_source else None
        self.tr = SegmentTransition(
            self.ref,
            self.fps,
            self.p_jump,
            self.p_rate,
            wait_enter=enter,
            wait_leave=WAIT_LEAVE,
        )
        self.N = self.tr.N
        self.R = len(self.ref.rates)
        self._events: dict[int, list[tuple[float, np.ndarray, float]]] = {}
        self._chords: dict[int, tuple[float, np.ndarray]] = {}
        self._jumps: dict[int, list[tuple[float, np.ndarray, float]]] = {}
        self._melody: dict[int, tuple[float, np.ndarray]] = {}
        self._vocal: dict[int, dict[str, tuple[float, float]]] = {}
        # what every other source says about a wait state: its segment's last cell
        self._wait_src = np.asarray(self.ref.end_cells)[: self.tr.n_wait]
        sung = np.asarray(getattr(self.ref, "sung", np.zeros(self.ref.P, bool)), bool)
        self._sung = np.concatenate([sung, np.zeros(self.tr.n_wait, bool)])
        self.music_enabled = False
        self.late_evidence = 0
        #: frame -> ((chord frame, key), its evidence over cells)
        self._chord_ev: dict[int, tuple] = {}
        self.shown: int | None = None
        #: The least share of the projected belief a slide needs to replace
        #: the one shown (0 = whichever leads).
        self.min_share = MIN_SWITCH_SHARE
        self.collapse_share = COLLAPSE_SHARE
        self.lyric_hold_s = LYRIC_HOLD_S
        self.hold_min_share = HOLD_MIN_SHARE
        #: Arrival time of the latest scored step that heard a line of this song.
        self._heard_at: float | None = None
        self.talk = TalkGate(self.ref.lyric_pairs) if TALK_GATE else None
        #: The slide shown before the current one, and when the screen changed.
        self._seen_shown: int | None = None
        self._left: int | None = None
        self._shown_since = -1e9
        #: slide -> arrival of the latest scorer step that heard its last
        #: words / its opening (`note_span_scores`).
        self._tail_heard: dict[int, float] = {}
        self._scores_at = -1e9
        self._twin_map: dict[int, set[int]] | None = None
        self.twin_swaps = 0
        self._opening_heard: dict[int, float] = {}
        #: [shown slide, seconds the follower has wanted to step on from it,
        #: the last read-out that wanted to].
        self._leave_wanted: list | None = None
        self.leave_holds = 0
        self.reset_uniform()
        idx = np.asarray([-1 if x is None else int(x) for x in self.ref.slide_of])
        if self.tr.n_wait:
            idx = np.concatenate([idx, idx[self._wait_src]])
            idx[self.ref.P] = -1  # the intro's (unused) wait column
        self._slide_idx = idx
        self._n_slides = max(int(idx.max()), 0) + 1
        written = np.asarray(
            getattr(self.ref, "written_cell", np.ones(self.ref.P, bool)), bool
        )
        if self.tr.n_wait:
            written = np.concatenate([written, written[self._wait_src]])
        #: cells (and wait states) of a written pass, not a loop's further one
        self._written = written

    # ------------------------------------------------------------ anchoring

    def reset_uniform(self, frame: int = -1) -> None:
        self.alpha = np.zeros((self.R, self.N), np.float32)
        self.alpha[:, : self.ref.P] = 1.0 / (self.R * self.ref.P)
        self.ck_frame = frame

    def anchor(self, slide: int | None, t: float, mid_slide: bool = False) -> None:
        """The screen is on `slide` at audio time `t` (an operator change, or a
        song entered): collapse the belief onto that slide's start — the intro
        for the blank slide — and forget evidence about earlier audio.

        `mid_slide`: the band is somewhere inside the slide already (a song
        change heard from its words): spread the belief over the slide's sung
        cells instead, for the evidence to place. From its start the follower
        trailed the band by however long the change took to hear, for the rest
        of the slide (continuous mode showed it as a column lines behind)."""
        frame = int(t * self.fps)
        a = np.zeros((self.R, self.N), np.float32)
        cells = np.where(self.ref.slide_of == slide)[0] if slide is not None else np.array([], int)
        written = getattr(self.ref, "written_cell", None)
        if written is not None and len(cells):
            cells = cells[written[cells]]  # the band is on its first pass
        sung = cells[self._sung[cells]] if len(cells) else cells
        if slide is None or len(cells) == 0 or slide == self.ref.slide_of[self.ref.intro_cell]:
            a[:, self.ref.intro_cell] = 1.0
        elif mid_slide and len(sung):
            a[:, sung] = 1.0
        else:
            seg = self.ref.seg_of[cells[0]]
            a[:, int(self.ref.jump_targets[seg])] = 1.0
        self.alpha = a / a.sum()
        self.ck_frame = frame
        self.shown = slide
        for store in (
            self._events,
            self._chords,
            self._melody,
            self._jumps,
            self._vocal,
            self._chord_ev,
        ):
            for f in [f for f in store if f <= frame]:
                del store[f]

    # ------------------------------------------------------------ evidence

    def _check_late(self, t_audio: float, t_arrival: float) -> bool:
        if int(t_audio * self.fps) <= self.ck_frame:
            self.late_evidence += 1
            return False
        return True

    def push_word(self, t_audio: float, t_arrival: float, text: str, weight: float = 1.0) -> None:
        if self.talk is not None:
            self.talk.push(t_audio, t_arrival, text)
        vec = self.ref.word(text)
        if vec is None or not self._check_late(t_audio, t_arrival):
            return
        self._events.setdefault(int(t_audio * self.fps), []).append((t_arrival, vec, weight))

    def push_scored(
        self,
        t_audio: float,
        t_arrival: float,
        vec: np.ndarray,
        weight: float = SCORED_WEIGHT,
        *,
        heard: bool = True,
        entropy: float | None = None,
    ) -> None:
        """`heard`: the step's best span reached the null level, i.e. a line
        of this song was sung in it (`SegmentReference.heard`)."""
        if heard and (self._heard_at is None or t_arrival > self._heard_at):
            self._heard_at = t_arrival
        if self._heard_at is not None:
            # The null level says "no line is being sung", which before the
            # song means it hasn't started; once a line has been heard it
            # only means the words can't be made out (Awesome God: 94% of
            # steps below it), which is no reason to go back to the blank.
            # Replayed: the live set 0.701 -> 0.533, the medley 0.505 -> 0.488,
            # nothing worse.
            vec = vec.copy()
            vec[self.ref.intro_cell] = vec.min()
        if not self._check_late(t_audio, t_arrival):
            return
        self._events.setdefault(int(t_audio * self.fps), []).append(
            (t_arrival, vec.astype(np.float32), weight)
        )
        if TARGETED_JUMP > 0 and entropy is not None:
            q = TARGETED_JUMP * max(0.0, 1.0 - entropy) ** JUMP_SHARPNESS
            if q > 1e-4:
                d = np.exp(vec - vec.max()).astype(np.float64)
                d[self.ref.intro_cell] = 0.0
                written = getattr(self.ref, "written_cell", None)
                if written is not None:
                    d[~written] = 0.0  # land on the line, not on a later pass of it
                if d.sum() > 0:
                    self._jumps.setdefault(int(t_audio * self.fps), []).append(
                        (t_arrival, (d / d.sum()).astype(np.float32), q)
                    )

    def next_line_query(self, marginal_now: np.ndarray) -> NextLineQuery | None:
        """What to ask the next-line check now, or None (off, not near the
        end of a line, or nothing to tell apart). `marginal_now`: a read-out's
        belief at "now".

        Asked from up to three lines the band may be leaving: the belief's
        line now, the slide on screen, and the belief's line before it. By
        the time the band leaves the written order (the chorus again), the
        belief has usually walked on to the line the order expected, and
        asked only from there the check could never see where the band went
        (33 of Set 1's 54 off-order changes)."""
        if NEXT_LINE_WEIGHT <= 0 and NEXT_LINE_JUMP <= 0:
            return None
        wall = getattr(self, "_wall", None)
        if self._heard_at is None or wall is None or wall - self._heard_at > NEXT_LINE_HEARD_S:
            return None
        ref = self.ref
        m = np.asarray(marginal_now[: ref.P], np.float64)
        idx = self._slide_idx[: ref.P]
        ok = idx >= 0
        mass = np.bincount(idx[ok], weights=m[ok], minlength=self._n_slides)
        mode = int(np.argmax(mass))
        blank = int(ref.slide_of[ref.intro_cell])
        hist = self.__dict__.setdefault("_nl_modes", [])
        if not hist or hist[-1] != mode:
            hist.append(mode)
            del hist[:-2]
        words = ref.slide_words
        if mode == blank or not words[mode]:
            return None
        g = self._line_segment(mode, m)
        a, b = int(ref.jump_targets[g]), int(ref.end_cells[g])
        mine = m[a : b + 1]
        if mine.sum() <= 0 or mine[(b - a + 1) // 2 :].sum() / mine.sum() < NEXT_LINE_DUE:
            return None
        anchors = [mode]
        for x in (self.shown, hist[0] if len(hist) > 1 else None):
            if (
                x is not None
                and 0 <= x < len(words)
                and x != blank
                and words[x]
                and x not in anchors
            ):
                anchors.append(x)
        followed = set(ref.seg_slides[1:])
        n = NEXT_LINE_OPENING_WORDS
        spans: list[str] = []
        slides: list[frozenset[int]] = []
        n_open: list[int] = []
        of: list[int] = []
        expected = None
        for anchor in anchors:
            g = self._line_segment(anchor, m)
            cands = [
                ref.seg_slides[j]
                for j in np.argsort(-ref.succ[g])[:2]
                if j > 0 and ref.succ[g, j] > 0.02
            ]
            secs = ref.sections
            if secs and anchor < len(secs):
                st = anchor
                while st > 1 and secs[st - 1] == secs[anchor]:
                    st -= 1
                cands.append(st)
            openings: list[tuple[str, ...]] = []
            for c in cands:
                o = tuple(words[c][:n]) if c in followed else ()
                if o and o not in openings:
                    openings.append(o)
            aw, k = words[anchor], NEXT_LINE_TAIL_WORDS + n
            # still on this line: a run of its words ending at each of its
            # words (a slide of several lines may be mid-way, not at its end)
            for j in range(len(aw)):
                spans.append(" ".join(aw[max(0, j - k + 1) : j + 1]))
                slides.append(frozenset())
                n_open.append(0)
                of.append(anchor)
            tail = " ".join(aw[-NEXT_LINE_TAIL_WORDS:])
            for i, o in enumerate(openings):
                if anchor == mode and cands and i == 0 and o == tuple(words[cands[0]][:n]):
                    expected = len(spans)
                spans.append(f"{tail} {' '.join(o)}")
                slides.append(frozenset(s for s in followed if tuple(words[s][: len(o)]) == o))
                n_open.append(len(o))
                of.append(anchor)
        if not any(slides):
            return None
        return NextLineQuery(
            anchor=mode,
            spans=spans,
            slides=slides,
            n_open_words=n_open,
            expected=expected,
            anchors=of,
        )

    def _line_segment(self, slide: int, m: np.ndarray) -> int:
        """The segment of `slide` holding the most belief (its first if none)."""
        ref = self.ref
        cells = np.where(self._slide_idx[: ref.P] == slide)[0]
        w = m[cells]
        if w.sum() > 0:
            return int(np.argmax(np.bincount(ref.seg_of[cells], weights=w)))
        return int(ref.seg_of[cells[0]])

    def _opening_cells(self, slides: frozenset[int], n_words: int) -> np.ndarray:
        """Cells from each of `slides`' segment starts to just past its
        `n_words`-th word, on every pass."""
        key = (slides, n_words)
        cache = self.__dict__.setdefault("_opening_cache", {})
        if key not in cache:
            ref = self.ref
            mask = np.zeros(ref.P, bool)
            for g in range(1, len(ref.seg_slides)):
                sl = ref.seg_slides[g]
                a = int(ref.jump_targets[g])
                if sl not in slides or ref.copy_of[a] != a:
                    continue  # a loop's further pass: copied below
                b = int(ref.end_cells[g])
                c = ref.word_cell.get((sl, n_words - 1))
                end = min(b, c + 2) if c is not None and a <= c <= b else b
                mask[a : end + 1] = True
            mask |= mask[ref.copy_of]
            cache[key] = mask
        return cache[key]

    def push_next_line(
        self, t_audio: float, t_arrival: float, query: NextLineQuery, scores: np.ndarray
    ) -> None:
        """The next-line check's answer: `scores[i]` for `query.spans[i]`
        (mean log-probability per token), about audio up to `t_audio`."""
        s = np.asarray(scores, np.float64)
        if len(s) != len(query.spans) or not self._check_late(t_audio, t_arrival):
            return
        frame = int(t_audio * self.fps)
        order = np.argsort(-s)
        win = int(order[0])
        if not query.slides[win]:
            return  # still on this line
        margin = float(s[win] - s[order[1]])
        if NEXT_LINE_WEIGHT > 0:
            vec = np.zeros(self.ref.P, np.float32)
            for i in range(len(s)):
                if not query.slides[i]:
                    continue
                cells = self._opening_cells(query.slides[i], query.n_open_words[i])
                vec[cells] = np.minimum(vec[cells], s[i] - s[win])
            stay = max((s[i] for i in range(len(s)) if not query.slides[i]), default=None)
            if NEXT_LINE_LEAVE and stay is not None:
                frm = query.anchors[win] if query.anchors else query.anchor
                left = (self._slide_idx[: self.ref.P] == frm) & ~self._opening_cells(
                    query.slides[win], query.n_open_words[win]
                )
                vec[left] = np.minimum(vec[left], stay - s[win])
            self._events.setdefault(frame, []).append((t_arrival, vec, NEXT_LINE_WEIGHT))
        if (
            NEXT_LINE_JUMP > 0
            and margin >= NEXT_LINE_JUMP_MARGIN
            and not (NEXT_LINE_JUMP_OFF_ORDER and win == query.expected)
        ):
            d = self._opening_cells(query.slides[win], query.n_open_words[win]).astype(np.float32)
            if d.sum() > 0:
                d[~self._written[: self.ref.P]] = 0.0
            if d.sum() > 0:
                self._jumps.setdefault(frame, []).append((t_arrival, d / d.sum(), NEXT_LINE_JUMP))

    def jump_to(self, slide: int, t_audio: float, t_arrival: float, share: float) -> None:
        """Move `share` of the belief onto `slide`'s opening (and its identical
        copies'), as of audio time `t_audio`: the next-line check heard the
        band start it (`follower.next_line`)."""
        d = self._opening_cells(frozenset({slide, *self.identical.get(slide, ())}), 3).astype(
            np.float32
        )
        if d.sum() > 0:
            self._jumps.setdefault(int(t_audio * self.fps), []).append(
                (t_arrival, d / d.sum(), share)
            )

    def push_heard(self, t_arrival: float) -> None:
        """A line of this song was heard (a step that carried no position)."""
        if self._heard_at is None or t_arrival > self._heard_at:
            self._heard_at = t_arrival

    def push_chords(self, t_audio: float, t_arrival: float, probs: np.ndarray) -> None:
        """BTC probabilities (25) describing the audio frame at `t_audio`."""
        frame = int(t_audio * self.fps)
        if frame > self.ck_frame:
            self._chords[frame] = (t_arrival, np.asarray(probs, np.float32))

    def push_melody(self, t_audio: float, t_arrival: float, pcs: np.ndarray) -> None:
        frame = int(t_audio * self.fps)
        if frame > self.ck_frame:
            self._melody[frame] = (t_arrival, np.asarray(pcs, np.float32))

    def push_vocal(self, t_audio: float, t_arrival: float, p_sing: float) -> None:
        """P(singing) (vocal activity) in the audio frame at `t_audio`."""
        frame = int(t_audio * self.fps)
        if frame > self.ck_frame:
            self._vocal[frame] = (t_arrival, float(p_sing))

    def _evidence(self, frame: int, wall: float) -> np.ndarray:
        e = self._evidence_cells(frame, wall)
        if self.tr.n_wait:
            e = np.concatenate([e, e[self._wait_src]])
        voice = self._vocal.get(frame)
        if voice is not None and voice[0] <= wall + 1e-9:
            p = voice[1]
            l_sung = np.log(p * VOCAL_HIT + (1 - p) * (1 - VOCAL_HIT))
            l_unsung = np.log(p * VOCAL_FALSE + (1 - p) * (1 - VOCAL_FALSE))
            e = e + VOCAL_WEIGHT * np.where(self._sung, l_sung, l_unsung).astype(np.float32)
        return e

    def _evidence_cells(self, frame: int, wall: float) -> np.ndarray:
        e = np.zeros(self.ref.P, np.float32)
        if self.music_enabled:
            c = self._chords.get(frame)
            if c is not None and c[0] <= wall + 1e-9:
                # each read-out re-filters the last seconds: a frame's chord
                # evidence is the same every time until the key changes
                key = (id(c), self.ref.key_shift)
                hit = self._chord_ev.get(frame)
                if hit is None or hit[0] != key:
                    hit = (key, self.ref.chords(c[1])[0])
                    self._chord_ev[frame] = hit
                e += CHORD_WEIGHT * hit[1]
            m = self._melody.get(frame)
            if m is not None and m[0] <= wall + 1e-9:
                e += MELODY_WEIGHT * self.ref.melody(m[1])[0]
        for t_arr, vec, w in self._events.get(frame, ()):
            if t_arr <= wall + 1e-9:
                e = e + w * vec
        return e

    def _near_copies(self, a: np.ndarray, dist: np.ndarray) -> np.ndarray:
        """Favour the copy of a repeated slide near where the belief already is.

        A jump's target is often a line on several identical slides (a refrain
        sung four times). Spread evenly over every copy, it erases the
        follower's count of which repeat this is: I Surrender All lost it at
        the band's repeated "I surrender all" (0.6 on the right copy -> 0.14
        on each of four), so the next verse could be any of five and showed
        8.5 s late. A copy near the current belief (its own slide or a
        neighbour, plus `COPY_NEAR`) is preferred; with no belief nearby every
        copy keeps its share."""
        if COPY_NEAR <= 0:
            return dist
        idx = self._slide_idx
        ok = idx >= 0
        cells = a[:, : self.ref.P].sum(axis=0)
        mass = np.bincount(idx[ok], weights=cells[ok], minlength=self._n_slides)
        near = mass.copy()
        near[1:] += mass[:-1]
        near[:-1] += mass[1:]
        floor = np.full(len(idx), COPY_NEAR, np.float32)
        w = np.where(ok, near[np.maximum(idx, 0)], 0.0) + floor
        out = dist * w
        total = out.sum()
        return (out / total).astype(np.float32) if total > 0 else dist

    def _update(self, a: np.ndarray, frame: int, wall: float) -> np.ndarray:
        for t_arr, dist, q in self._jumps.get(frame, ()):
            if t_arr <= wall + 1e-9:
                # move q of the belief to where the step points, keeping each
                # tempo state's share
                dist = self._near_copies(a, dist)
                rate = a.sum(axis=1, keepdims=True)
                if self.tr.n_wait:
                    dist = np.concatenate([dist, np.zeros(self.tr.n_wait, np.float32)])
                a = (1 - q) * a + q * rate * dist[None, :]
        e = self._evidence(frame, wall)
        b = a * np.exp(e - e.max())[None, :]
        total = b.sum()
        if not np.isfinite(total) or total <= 0:
            # bad evidence (non-finite, or ruling out every state): keep the
            # belief this step had rather than lose it for good
            return a / a.sum()
        return b / total

    # ------------------------------------------------------------ read-out

    def readout(self, wall: float) -> Readout:
        """The belief at audio time `wall`, given everything that has arrived."""
        self._wall = wall
        now = int(wall * self.fps)
        k_back = int(np.ceil(self.refilter_s * self.fps))
        settle = now - k_back
        if settle - self.ck_frame > MAX_CATCH_UP_S * self.fps:
            # far behind (anchored long ago, no read-outs since): stepping
            # every frame would hold the lock for seconds; the time in
            # between carries no evidence, so jump the checkpoint instead
            self.ck_frame = settle - int(MAX_CATCH_UP_S * self.fps)
        while self.ck_frame < settle:
            self.ck_frame += 1
            self.alpha = self._update(self.tr.step(self.alpha), self.ck_frame, wall)
        # evidence older than the checkpoint can never be used again
        for store in (
            self._events,
            self._chords,
            self._melody,
            self._jumps,
            self._vocal,
            self._chord_ev,
        ):
            for f in [f for f in store if f <= self.ck_frame]:
                del store[f]
        a = self.alpha.copy()
        for f in range(self.ck_frame + 1, now + 1):
            a = self._update(self.tr.step(a), f, wall)
        rate_marg = a.sum(axis=1)
        marg_now = self._fold(a.sum(axis=0))
        #: share of the belief at "now" in wait states (diagnostics)
        self.wait_now = (
            float(a[:, self.ref.P :].sum() / max(a.sum(), 1e-12)) if self.tr.n_wait else 0.0
        )
        for _ in range(round(self.lookahead_s * self.fps)):
            a = self.tr.step(a)
        if self.shown != self._seen_shown:
            self._left, self._seen_shown, self._shown_since = self._seen_shown, self.shown, wall
        slide, share = self._choose(a.sum(axis=0), wall)
        if (
            slide is not None
            and slide != self.shown
            and slide == self._left
            and wall - self._shown_since < FLIP_BACK_S
            and share < FLIP_BACK_SHARE
        ):
            slide = self.shown
        slide = self._wait_for_last_line(slide, wall)
        slide = self._twin_in_order(slide)
        cells_per_s = float(rate_marg @ self.ref.rates / max(rate_marg.sum(), 1e-12))
        return Readout(slide, share, marg_now, cells_per_s * 0.5, cells_per_s)

    def note_span_scores(self, t_arrival: float, span_score: dict) -> None:
        """One scorer step's `(slide, word index) -> score`: which slides'
        last words, and which slides' openings, it heard."""
        words = self.ref.slide_words
        self._scores_at = t_arrival
        for (slide, w), score in span_score.items():
            n = len(words[slide]) if 0 <= slide < len(words) else 0
            if n == 0:
                continue
            if w >= n - TAIL_WORDS and score >= TAIL_LEVEL:
                self._tail_heard[slide] = t_arrival
            if 1 <= w <= 5 and score >= OPENING_LEVEL:
                self._opening_heard[slide] = t_arrival

    def _twins(self) -> dict[int, set[int]]:
        """Slides whose words are the start of another slide's, both ways."""
        if self._twin_map is None:
            words = [" ".join(w) for w in self.ref.slide_words]
            tw: dict[int, set[int]] = {}
            for x, wx in enumerate(words):
                for y, wy in enumerate(words):
                    if x != y and wx and wy.startswith(wx + " "):
                        tw.setdefault(x, set()).add(y)
                        tw.setdefault(y, set()).add(x)
            self._twin_map = tw
        return self._twin_map

    def _twin_in_order(self, slide: int | None) -> int | None:
        """`slide`, or the slide in order that it stands for: a slide and its
        longer twin are one place, and so is the step on from each
        (`TWINS_IN_ORDER`)."""
        shown = self.shown
        if not TWINS_IN_ORDER or slide is None or shown is None or slide in (shown, shown + 1):
            return slide
        tw = self._twins()
        if not tw:
            return slide
        words = self.ref.slide_words
        for off in (0, 1):  # on the twin, or one step on from it
            for t in sorted(tw.get(slide - off, ())):
                to = t + off
                if to in (shown, shown + 1) and to < len(words) and words[to]:
                    self.twin_swaps += 1
                    return to
        return slide

    def _wait_for_last_line(self, slide: int | None, wall: float) -> int | None:
        """`slide`, or the shown slide while a step on from it should wait
        (`LEAVE_NEEDS_LAST_LINE`)."""
        shown = self.shown
        words = self.ref.slide_words
        if (
            not LEAVE_NEEDS_LAST_LINE
            or wall - self._scores_at > SCORES_FRESH_S  # no line scorer running
            or slide is None
            or shown is None
            or slide != shown + 1
            or not (0 <= shown < len(words) and words[shown])
            or slide >= len(words)
            or words[slide] == words[shown]
        ):
            # a belief that wavers between this slide and the next must not
            # restart the wait each time: only a new slide on screen does
            if self._leave_wanted is not None and self._leave_wanted[0] != shown:
                self._leave_wanted = None
            return slide
        if self._leave_wanted is None or self._leave_wanted[0] != shown:
            self._leave_wanted = [shown, 0.0, wall]
        else:
            self._leave_wanted[1] += min(max(wall - self._leave_wanted[2], 0.0), 1.0)
            self._leave_wanted[2] = wall
        # a copy of the shown slide ending just now counts: the same words
        tail = max(
            (self._tail_heard.get(s, -1e9) for s in (shown, *self.identical.get(shown, ()))),
            default=-1e9,
        )
        if (
            wall - tail <= TAIL_HEARD_S
            or wall - self._opening_heard.get(slide, -1e9) <= OPENING_HEARD_S
            or self._leave_wanted[1] >= LEAVE_WAIT_MAX_S
        ):
            return slide
        self.leave_holds += 1
        return shown

    def _fold(self, m: np.ndarray) -> np.ndarray:
        """A belief over cells and wait states -> over cells (a wait counts
        at its segment's last cell)."""
        if not self.tr.n_wait:
            return m
        out = m[: self.ref.P].copy()
        np.add.at(out, self._wait_src, m[self.ref.P :])
        return out

    @property
    def waiting(self) -> float:
        """Share of the belief (at the checkpoint) in wait states."""
        if not self.tr.n_wait:
            return 0.0
        return float(self.alpha[:, self.ref.P :].sum() / max(self.alpha.sum(), 1e-12))

    def _choose(self, m: np.ndarray, wall: float) -> tuple[int | None, float]:
        """The slide to show from the projected belief `m`."""
        if self.talk is not None and (
            self.talk.talking(wall)
            or (BLANK_NEEDS_LYRICS and self._starting() and not self.talk.lyrics_heard(wall))
        ):
            _slide, share = self._pooled_slide(m)
            return self.shown, share
        if not self._holding(wall):
            return self._pooled_slide(m)
        if self.hold_min_share is None:
            slide, share = self._pooled_slide(m)
            return (self.shown, share) if self.shown is not None else (slide, share)
        # nothing of the song heard lately: the music can still move the
        # screen, but only on a clear majority
        if FIRST_LINE_UNHELD and self._starting():
            slide, share = self._pooled_slide(m)
            if slide == self._first_slide:
                return slide, share
        keep, keep_c = self.min_share, self.collapse_share
        self.min_share = max(keep, self.hold_min_share)
        self.collapse_share = 0.0
        try:
            return self._pooled_slide(m)
        finally:
            self.min_share, self.collapse_share = keep, keep_c

    @property
    def _first_slide(self) -> int | None:
        seg = self.ref.first_segment
        return int(self.ref.seg_slides[seg]) if 0 < seg < len(self.ref.seg_slides) else None

    def _starting(self) -> bool:
        """The blank is up (the song not yet under way)."""
        blank = self.ref.slide_of[self.ref.intro_cell]
        return self.shown is None or self.shown == blank

    def _holding(self, wall: float) -> bool:
        """No line of this song heard lately: keep the screen where it is."""
        if self.lyric_hold_s is None:
            return False
        return self._heard_at is None or wall - self._heard_at > self.lyric_hold_s

    def note_heard_line(self, t_arrival: float, slides) -> None:
        """The scorer's latest step heard a line carried by `slides`."""
        self._heard_line = (float(t_arrival), frozenset(int(s) for s in slides))

    def _shown_carries_heard_line(self) -> bool:
        h = getattr(self, "_heard_line", None)
        if HOLD_HEARD_LINE_S <= 0 or h is None or self.shown is None:
            return False
        wall = getattr(self, "_wall", None)
        if wall is None or wall - h[0] > HOLD_HEARD_LINE_S:
            return False
        mine = {self.shown, *self.identical.get(self.shown, ())}
        return bool(mine & h[1])

    def _collapsed(self, shown: float, best: float) -> bool:
        """The shown slide has lost the belief to a clear leader."""
        if self._shown_carries_heard_line():
            return False
        return (
            self.collapse_share > 0
            and shown < self.collapse_share
            and best >= COLLAPSE_LEAD * max(shown, 1e-6)
        )

    def _copy_on_order(self, best: int, m: np.ndarray) -> int:
        """Which copy of `best`'s words to show: the one the belief is on.

        Pooling makes every copy of a repeated slide score the same, and
        `argmax` then returns the first. Reckless Love's chorus ends with its
        opening line (slides 6 and 9); singing slide 9 showed slide 6 every
        time, though the belief - which only the song's order can split
        between identical words - sat on 9.

        Only written passes count: a loop's further passes (a chorus sung
        more times than written) all hang off the last copy, and say only
        that the band is repeating, not which copy. With no written belief
        to go on, or another copy short of `COPY_SWITCH_LEAD` times the shown
        one's, the screen stays."""
        group = [best, *(b for b in self.identical.get(best, ()) if b < self._n_slides)]
        if len(group) == 1:
            return best
        ok = (self._slide_idx >= 0) & self._written
        mass = np.bincount(self._slide_idx[ok], weights=m[ok], minlength=self._n_slides)
        lead = max(group, key=lambda s: mass[s])
        if self.shown in group and (
            mass[lead] <= 0 or mass[self.shown] * COPY_SWITCH_LEAD >= mass[lead]
        ):
            return self.shown
        return lead if mass[lead] > 0 else best

    def _pooled_slide(self, m: np.ndarray) -> tuple[int | None, float]:
        """The slide with the most belief, identical-word slides pooled; a tie
        keeps the slide already shown (two identical slides read exactly
        equal, and floating-point order would otherwise flip between them)."""
        idx = self._slide_idx
        ok = idx >= 0
        mass = np.bincount(idx[ok], weights=m[ok], minlength=self._n_slides)
        pooled = mass.copy()
        for a in range(self._n_slides):
            for b in self.identical.get(a, ()):
                if b != a and b < self._n_slides:
                    pooled[a] += mass[b]
        if pooled.max() <= 0:
            return None, 0.0
        best = self._copy_on_order(int(np.argmax(pooled)), m)
        total = max(m.sum(), 1e-12)
        if (
            self.shown is not None
            and self.shown < self._n_slides
            # a copy of the shown slide was already settled by `_copy_on_order`
            and best not in self.identical.get(self.shown, ())
            and (
                pooled[self.shown] >= pooled[best] - 1e-9
                or (
                    pooled[best] < self.min_share * total
                    and not self._collapsed(pooled[self.shown] / total, pooled[best] / total)
                )
            )
        ):
            best = self.shown
        return best, float(pooled[best] / max(m.sum(), 1e-12))
