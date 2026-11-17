"""The score follower in a live session: one `OnlineFollower` per setlist song
it has a reference for (a lead sheet, or the library slides alone — see
`build_live_followers`), fed from three threads, read out on the audio clock.

Who calls what:

* **capture thread** (frame fan-out) — `on_frame`: advances the audio
  clock, runs this session's own chord analyser (BTC; the debug panel's
  analyser is separate and may be off), and every `READOUT_S` of audio reads
  the current song's follower out. A changed slide comes back as a
  `MatcherDecision` for the caller to post to the event loop.
* **ASR thread** — `side_pass`: the span scorer, run by
  `SlidingWindowWrapper` *before* each transcription step it shares the GPU
  with. The scoring itself runs outside the lock.
* **event loop** — `on_words` (Whisper's words, after the doubtful-word
  filter, i.e. what the matcher gets), `enter` (a song or slide became the
  screen: an AI song change or an operator override).

The current song's follower is the only one fed or read: which song is
playing is still the production matcher's call (music could not tell songs
apart reliably from the mix — see Plan/MUSIC_ONLY_MATCHING.md).

**Key.** Bands rarely play a lead sheet in its printed key. Each song's key is
estimated from the chords heard since it became current (trailing 60 s,
against the sheet's unshifted chords), first after 30 s, then every 30 s, so
a key lift is followed. Until then the follower runs on words alone.

**Overload.** The scorer is the expensive part. `set_load(level)` steps it
from every 2 s (0) to every 4 s (1, measured 0.192 against 0.181) to off
(2, words and chords only: 0.220), driven by pipeline health.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np

from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame
from auto_presenter.follower import next_line, sheet
from auto_presenter.follower.online import SCORED_WEIGHT, OnlineFollower, Readout
from auto_presenter.follower.reference import SegmentReference, sheet_spec
from auto_presenter.follower.scorer import WINDOW_S, Scorer, spans_for
from auto_presenter.follower.spec import ReferenceSpec, SegmentSpec, library_spec
from auto_presenter.learning.profile import LearnedSong, learned_spec
from auto_presenter.matcher.types import MatcherDecision
from auto_presenter.music.analyser import MusicUpdate

log = logging.getLogger(__name__)

#: How often the current song's follower is read out, seconds of audio.
READOUT_S = 0.5
#: Chords describe the audio this far before the analyser's frame end (measured, BTC online).
CHORD_LATENCY_S = 0.74
#: Key estimation: first after this many seconds of chords in the song, then this often.
KEY_FIRST_S = 30.0
KEY_EVERY_S = 30.0
KEY_WINDOW_S = 60.0
#: A song engages the follower only when its sheet places this share of its sung slides.
MIN_COVERAGE = 0.8
#: Scorer cadence per load level (seconds between steps; None = off).
SCORER_STEP = {0: 2.0, 1: 4.0, 2: None}


@dataclass
class SongFollower:
    item_idx: int
    ref: SegmentReference
    follower: OnlineFollower
    #: (slide, word index) -> span text, and the distinct spans in a fixed order.
    owner: dict[tuple[int, int], str]
    spans: list[str]
    entered_at: float = 0.0
    #: Recent chord frames (audio time, probs) for the key estimate.
    chord_log: list[tuple[float, np.ndarray]] = field(default_factory=list)
    key_checked_at: float = -1e9
    #: Per cell, (slide, word) — built on first use (`flow_evidence`).
    cell_words: tuple[np.ndarray, np.ndarray] | None = None
    #: What the reference was built from, to rebuild it with learned slides
    #: (`LiveFollowers.apply_learned`).
    spec: ReferenceSpec | None = None
    slides: list[list[str]] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    ident: dict[int, set[int]] = field(default_factory=dict)
    #: slide -> " its words ", for which slides carry a heard line.
    text: dict[int, str] = field(default_factory=dict)
    #: Recent raw evidence, re-pushed onto a rebuilt reference:
    #: (kind, audio time, arrival time, payload).
    raw: deque = field(default_factory=deque)
    #: The next-line check (`follower.next_line`), when the session runs it:
    #: its state (only the check's thread touches it), what it reads from the
    #: latest read-out (belief per slide, the slide read out), and a slide it
    #: heard the band start, for the next read-out to show.
    check: next_line.NextLineCheck | None = None
    check_mass: np.ndarray | None = None
    check_slide: int | None = None
    check_flip: tuple[int, float] | None = None
    #: Bumped when the song is entered: the check starts afresh.
    check_gen: int = 0
    check_seen_gen: int = 0
    check_last_t: float | None = None


@dataclass(frozen=True)
class FlowEvidence:
    """Where the followed song is, for continuous mode (`decision.flow`):
    the belief at "now" over the slides' words, as the follower counts them."""

    item_idx: int
    t: float
    #: (slide, word index among that slide's follower words) -> belief
    mass: dict[tuple[int, int], float]
    #: slide -> how many words the follower counts in it
    words: dict[int, int]
    #: How fast the band is moving through the words, from the tempo belief.
    rate_wps: float


#: Above this the rate is not singing (`decision.flow.FlowConfig.max_rate_wps`).
MAX_RATE_WPS = 4.0
#: Continuous mode reads the position inside the slide the follower would
#: show (`Readout.slide`: half the belief to replace the shown one, the lyric
#: hold), not wherever most of the belief is. Without it the column cut to
#: the song's first line and back every ~10 s through Since Your Love's
#: three-minute closing vamp (24 of its 28 jumps came straight back), each
#: time the belief wrapped past the last slide; the slide on screen never
#: moved. False: the whole belief (2026-10-08 behaviour).
FLOW_ON_SHOWN_SLIDE = True
#: Continuous mode holds the column at this quantile of the position belief
#: inside the shown slide, not at its peak. The sung line stays readable from
#: ~2 lines above the column to ~3.5 below, so a column ahead of the band
#: loses it twice as fast as one behind; and a wide belief (no lead sheet: no
#: rhythm, position from tempo alone) is ahead half the time. 8 live traces
#: (flow_replay.py, 7 lines), library / lead sheet: peak 61.9 / 70.8%, median
#: 62.5 / 70.7, 0.3 64.0 / 73.1, 0.2 66.0 / 74.7, 0.1 67.1 / 76.0; on the sung
#: words (scroll_error) the column sits 1-2 words further behind, within 8
#: words of the band as often or more. None: the peak (`FlowFollower`).
FLOW_QUANTILE: float | None = 0.1


def _cell_words(ref: SegmentReference) -> tuple[np.ndarray, np.ndarray]:
    """Per cell, the slide and the word sung at or before it (-1: none)."""
    slide = np.asarray([-1 if x is None else int(x) for x in ref.slide_of])
    word = np.full(ref.P, -1)
    by_slide: dict[int, list[tuple[int, int]]] = {}
    for (si, j), c in ref.word_cell.items():
        by_slide.setdefault(si, []).append((c, j))
    for si, marks in by_slide.items():
        marks.sort()
        cells = np.where(slide == si)[0]
        starts = np.asarray([c for c, _ in marks])
        js = np.asarray([j for _, j in marks])
        k = np.searchsorted(starts, cells, side="right") - 1
        # cells before the slide's first sung word count as its first word
        word[cells] = np.where(k >= 0, js[np.maximum(k, 0)], js[0])
    copy_of = getattr(ref, "copy_of", None)
    if copy_of is not None:
        word = word[copy_of]  # a loop's further passes: the words they repeat
    return slide, word


def flow_evidence(sf: SongFollower, r: Readout, t: float) -> FlowEvidence | None:
    """A read-out as a position over words, inside the slide the read-out
    shows (`FLOW_ON_SHOWN_SLIDE`). None while there is no such word to place
    (the intro, before the song starts; the belief gone from that slide)."""
    if sf.cell_words is None:
        sf.cell_words = _cell_words(sf.ref)
    slide, word = sf.cell_words
    m = np.asarray(r.marginal_now[: len(word)], float)
    ok = word >= 0
    if FLOW_ON_SHOWN_SLIDE:
        if r.slide is None:
            return None
        # the belief has left the slide the follower holds on screen: no
        # update, so the column runs on to that slide's end and waits there
        ok &= slide == int(r.slide)
    if m[ok].sum() <= 1e-9:
        return None
    mass: dict[tuple[int, int], float] = {}
    for si, j, v in zip(slide[ok], word[ok], m[ok], strict=True):
        if v > 0:
            key = (int(si), int(j))
            mass[key] = mass.get(key, 0.0) + float(v)
    if FLOW_QUANTILE is not None and FLOW_ON_SHOWN_SLIDE and mass:
        # one slide's words: the position the quantile of the belief reaches
        order = sorted(mass)
        cum = np.cumsum([mass[k] for k in order])
        k = int(np.searchsorted(cum, FLOW_QUANTILE * cum[-1]))
        mass = {order[min(k, len(order) - 1)]: float(cum[-1])}
    words: dict[int, int] = {}
    for si, j in sf.ref.word_cell:
        words[si] = max(words.get(si, 0), j + 1)
    # words per cell over the slide the belief is mostly on, times cells/s
    here = int(slide[int(np.argmax(np.where(ok, m, -1.0)))])
    sung = np.asarray(getattr(sf.ref, "sung", np.ones(sf.ref.P, bool)), bool)
    # one pass of the slide: a loop's further passes repeat the same words
    written = np.asarray(getattr(sf.ref, "written_cell", np.ones(sf.ref.P, bool)), bool)
    mine = (slide == here) & written
    n_cells = int((mine & sung).sum()) or int(mine.sum())
    rate = r.cells_per_s * words.get(here, 0) / max(1, n_cells)
    return FlowEvidence(sf.item_idx, t, mass, words, float(min(MAX_RATE_WPS, max(0.0, rate))))


#: The next-line check leaves a slide this long after the screen changed.
CHECK_SETTLE_S = 2.0
#: A song's first words come off the blank when the transcript brings two of
#: them, a median 5.3 s after they are sung on 20 public songs; the line
#: scorer has usually named the line seconds before. One scorer pass that
#: hears a line this well, this far ahead of every other line, while the band
#: plays, counts as the song's words heard. Real time, 2026-11-14: first
#: words up at 3.9 s on the public songs, sung line hidden 0.226 -> 0.205,
#: wrong-slide 0.390 -> 0.377; no change on the church recordings, where the
#: transcript is as quick (the rule fired once in nine); talk benchmark still
#: 0 changes in 265 min. AUTO_PRESENTER_START_ON_LINE=0 turns it off.
START_ON_CLEAR_LINE = os.environ.get("AUTO_PRESENTER_START_ON_LINE", "1") != "0"
START_LINE_LEVEL = -2.0
START_LINE_LEAD = 1.0


def clear_line(slide_words: list, span_score: dict) -> bool:
    """Did this scorer pass hear one line clearly ahead of every other?
    Slides with the same words are one line."""
    best: dict[tuple, float] = {}
    for (slide, w), score in span_score.items():
        if w > 0 and 0 <= slide < len(slide_words) and slide_words[slide]:
            key = tuple(slide_words[slide])
            best[key] = max(best.get(key, -math.inf), score)
    if len(best) < 2:
        return False
    top = sorted(best.values(), reverse=True)
    return top[0] >= START_LINE_LEVEL and top[0] - top[1] >= START_LINE_LEAD


@dataclass
class LiveFollowers:
    songs: dict[int, SongFollower]
    scorer: Scorer | None = None
    chord_analyser: object | None = None

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self.current: int | None = None
        self.audio_now = 0.0
        self._next_readout = READOUT_S
        self._last_scored_t = -1e9
        self.load_level = 0
        #: The latest read-out as a position over words, until taken.
        self._flow: FlowEvidence | None = None
        self.scorer_steps = 0
        self.scorer_ms: list[float] = []
        #: spans in the latest scorer pass (pipeline health)
        self.scorer_spans = 0
        #: In-service learning (`follower.learn`), when the session runs it.
        self.learner = None
        self._learn_feed: queue.SimpleQueue = queue.SimpleQueue()
        self._align_jobs: queue.SimpleQueue = queue.SimpleQueue()
        self.learn_ms: list[float] = []
        self.rebuilds = 0
        #: No longer the session's followers (`detach`).
        self._detached = False
        self._learn_thread: threading.Thread | None = None
        #: The next-line check's thread (`start_next_line`), when it runs.
        self.listener: next_line.PhoneListener | None = None
        #: Whether a band is playing (`music.band`), read on the capture
        #: thread; the check moves the screen only while it says yes. Talk
        #: has phones enough to read as any line: the check alone moved the
        #: screen 17 times in 16 min of meetings (2026-11-09), where the
        #: talking score still read "singing" in a clip's first seconds.
        self.band_playing: Callable[[], bool] | None = None
        self.next_line_flips = 0
        self.next_line_held = 0
        # Diagnostics: every piece of evidence, read-out and song entry, as
        # JSON lines, so a live run can be replayed offline exactly
        # (research/spikes/music_only/diag_live_trace.py). Off unless asked for.
        path = os.environ.get("AUTO_PRESENTER_FOLLOWER_TRACE")
        self._trace = None
        if path:
            try:
                self._trace = open(path, "a", buffering=1)
            except OSError as exc:
                log.warning("follower trace %s not written: %s", path, exc)

    def _log(self, **row) -> None:
        if self._trace is not None:
            self._trace.write(json.dumps(row, separators=(",", ":")) + "\n")

    @property
    def active(self) -> SongFollower | None:
        return self.songs.get(self.current) if self.current is not None else None

    def has(self, item_idx: int) -> bool:
        return item_idx in self.songs

    # ------------------------------------------------------------ event loop

    def enter(
        self,
        item_idx: int,
        slide_idx: int | None,
        t: float | None = None,
        mid_slide: bool = False,
    ) -> None:
        """`item_idx`/`slide_idx` is now on screen: follow that song from there.
        `mid_slide`: the change was heard from the band's words, so they are
        somewhere inside the slide (`OnlineFollower.anchor`); an operator's
        choice anchors at its start. Replayed on the two multi-song live
        traces: wrong-slide 0.145 -> 0.135, and continuous mode's column
        within 8 words of the band 80% -> 82% of the medley."""
        with self._lock:
            t = self.audio_now if t is None else t
            if item_idx != self.current:
                self.current = item_idx
                sf = self.songs.get(item_idx)
                if sf is not None:
                    sf.entered_at = t
                    sf.chord_log.clear()
                    sf.check_gen += 1
                    sf.check_flip = sf.check_mass = None
                    sf.key_checked_at = -1e9
                    sf.follower.music_enabled = False
            sf = self.active
            if sf is not None:
                sf.follower.anchor(slide_idx, t, mid_slide=mid_slide)
            self._log(e="enter", item=item_idx, slide=slide_idx, t=t)

    def on_words(self, words, arrival: float | None = None) -> None:
        """Whisper's words (`asr.types.Word`), as the matcher received them."""
        with self._lock:
            sf = self.active
            if sf is None:
                return
            arr = max(self.audio_now, arrival or 0.0)
            for w in words:
                if w.t_start is None:
                    continue
                t_arr = max(arr, float(w.t_end or w.t_start))
                sf.follower.push_word(float(w.t_start), t_arr, w.text)
                self._keep(sf, ("word", float(w.t_start), t_arr, w.text))
                if self.learner is not None:
                    self._learn_feed.put(
                        ("words", sf.item_idx, [(float(w.t_start), t_arr, w.text)])
                    )
                self._log(e="word", item=sf.item_idx, t=float(w.t_start), arr=t_arr, text=w.text)

    def note_shown(self, item_idx: int, slide_idx: int | None) -> None:
        """What the screen actually shows, when a follower decision did not
        reach it (held while nobody sings, or stale): without this the
        follower believes its slide is up and never re-issues it."""
        with self._lock:
            sf = self.songs.get(item_idx)
            if sf is not None and item_idx == self.current:
                sf.follower.shown = slide_idx
                self._log(e="shown", item=item_idx, slide=slide_idx, t=self.audio_now)

    def set_load(self, level: int) -> None:
        with self._lock:
            if level != self.load_level:
                log.info(
                    "score follower load level %d -> %d (scorer %s)",
                    self.load_level,
                    level,
                    f"every {SCORER_STEP[level]:.0f} s" if SCORER_STEP[level] else "off",
                )
            self.load_level = level
            if self.listener is not None:
                # overloaded: words and chords only, the check too
                self.listener.paused = level >= 2

    # ------------------------------------------------------------ next-line check

    def start_next_line(self, encode, head: next_line.PhoneHead) -> None:
        """Run the next-line check for every followed song: `encode` is
        Whisper's encoder (`next_line.mlx_encoder`), `head` the phone model."""
        with self._lock:
            for sf in self.songs.values():
                sf.check = next_line.NextLineCheck(sf.ref.slide_words)
        self.listener = next_line.PhoneListener(encode, head, self._on_phones)

    def _on_phones(self, t_end: float, logits: np.ndarray) -> None:
        """The check's thread: one tick's phones, read against the current song."""
        with self._lock:
            sf = self.active
            if self._detached or sf is None or sf.check is None or sf.check_mass is None:
                return
            check, gen = sf.check, sf.check_gen
            if sf.check_seen_gen != gen:
                check.reset()
                sf.check_seen_gen, sf.check_last_t = gen, None
            mass, shown, slide = sf.check_mass, sf.follower.shown, sf.check_slide
            last = sf.check_last_t
            sf.check_last_t = t_end
        dt = next_line.TICK_S if last is None else min(max(t_end - last, 0.0), 1.0)
        check.step(t_end, logits, mass, shown, slide, dt=dt)
        flip = check.take_flip()
        if flip is None:
            return
        with self._lock:
            if sf is self.active and sf.check_gen == gen:
                sf.check_flip = flip

    def _apply_flip(self, sf: SongFollower, r: Readout) -> None:
        """A slide the check heard the band start: show it, and move a share
        of the follower's belief to its opening."""
        flip, sf.check_flip = sf.check_flip, None
        if flip is None:
            return
        s, tt = flip
        words = sf.ref.slide_words
        shown = sf.follower.shown
        # Not off a blank or title slide (the song's start is the words' and
        # the opening watch's call), and not without a band. Nor back to the
        # slide before the one shown: the follower moves on during a line's
        # last bar on purpose, and the check, still hearing that line, put it
        # back within 2 s (115 of 288 flickers on 20 songs, 2026-11-09).
        if (
            shown is None
            or not words[shown]
            or (self.band_playing is not None and not self.band_playing())
            or (s == shown - 1 and words[s] != words[shown])
            # nor within 2 s of any change: two changes that close read as a
            # flicker, which costs the room more than a moment's wait (still
            # 200 flickers on 20 songs with the rules above, 2026-11-09)
            or self.audio_now - sf.follower._shown_since < CHECK_SETTLE_S
        ):
            self._log(e="next_line_held", item=sf.item_idx, t=tt, slide=s, shown=shown)
            self.next_line_held += 1
            return
        if r.slide is None or words[r.slide] != words[s]:
            sf.follower.jump_to(
                s, tt - next_line.JUMP_BACK_S, max(self.audio_now, tt), next_line.JUMP
            )
        self._log(e="next_line", item=sf.item_idx, t=tt, slide=s, was=r.slide, at=self.audio_now)
        r.slide = s
        self.next_line_flips += 1

    # ------------------------------------------------------------ ASR thread

    def side_pass(
        self, buffer: list[AudioFrame], t_end: float, extra: list[str] | None = None
    ) -> dict[str, float] | None:
        """Score the current song's spans against the trailing window (GPU).

        `extra` spans (the other songs' openings) ride along in the same pass,
        sharing the window's encoding; returns every span's score, or None
        when this tick did not score. Then at most one of the look-back's
        alignments, on the same GPU queue (`align_words`)."""
        try:
            return self._score_pass(buffer, t_end, extra)
        finally:
            self._run_align_job()

    def _score_pass(
        self, buffer: list[AudioFrame], t_end: float, extra: list[str] | None
    ) -> dict[str, float] | None:
        with self._lock:
            sf = self.active
            step = SCORER_STEP[self.load_level]
            if (
                sf is None
                or self.scorer is None
                or step is None
                or t_end - self._last_scored_t < step - 1e-6
            ):
                return None
            self._last_scored_t = t_end
            item, spans = sf.item_idx, sf.spans
        if not buffer or t_end - buffer[0].t_start < WINDOW_S - 0.5:
            return None
        own = set(spans)
        all_spans = spans + [x for x in dict.fromkeys(extra or ()) if x not in own]
        self.scorer_spans = len(all_spans)
        audio = np.concatenate([f.samples for f in buffer]).astype(np.float32)[
            -int(WINDOW_S * SAMPLE_RATE) :
        ]
        started = time.monotonic()
        result = self.scorer.score(audio, all_spans, place_among=len(spans))
        elapsed = time.monotonic() - started
        if result is None:
            return None
        every = {sp: float(sc) for sp, sc in zip(all_spans, result.scores, strict=True)}
        result.scores = result.scores[: len(spans)]
        win_start = t_end - len(audio) / SAMPLE_RATE
        with self._lock:
            self.scorer_steps += 1
            self.scorer_ms = [*self.scorer_ms, elapsed * 1000][-60:]
            sf = self.songs.get(item)
            if sf is None or item != self.current:
                return every  # the song changed while scoring
            by_text = {sp: float(s) for sp, s in zip(spans, result.scores, strict=True)}
            span_score = {sw: by_text[sp] for sw, sp in sf.owner.items()}
            self._push_scored(sf, win_start + result.place_s, t_end + elapsed, span_score)
            self._keep(sf, ("scored", win_start + result.place_s, t_end + elapsed, span_score))
            self._log(
                e="scored",
                item=item,
                t=win_start + result.place_s,
                arr=t_end + elapsed,
                win_end=t_end,
                place=result.place_s,
                scores=[round(float(x), 4) for x in result.scores],
            )
        return every

    def _push_scored(self, sf: SongFollower, t: float, arr: float, span_score: dict) -> None:
        # One NaN (half-precision Whisper on near-silent audio has returned
        # them) spread through the belief, which then read out slide 0 and
        # never recovered. A pass with a non-finite score is dropped whole.
        if not all(math.isfinite(v) for v in span_score.values()):
            log.warning("score follower: dropped a scorer pass with a non-finite score")
            return
        sf.follower.note_span_scores(arr, span_score)
        talk = sf.follower.talk
        if (
            START_ON_CLEAR_LINE
            and talk is not None
            and not talk.lyrics_heard(arr)
            and (self.band_playing is None or self.band_playing())
            and clear_line(sf.ref.slide_words, span_score)
        ):
            self._log(e="start_on_line", item=sf.item_idx, t=arr)
            talk.note_lyrics(arr)
        vec = sf.ref.scored(span_score)
        heard = sf.ref.heard(span_score)
        if vec is not None:
            sf.follower.push_scored(
                t,
                arr,
                vec,
                weight=SCORED_WEIGHT * sf.ref.reliability(span_score),
                heard=heard,
                entropy=sf.ref.step_entropy(span_score),
            )
        elif heard:
            sf.follower.push_heard(arr)
        lines = {k: v for k, v in span_score.items() if k[1] > 0}  # not one-word spans
        if heard and lines:
            best = sf.owner.get(max(lines, key=lines.__getitem__), "")
            sf.follower.note_heard_line(
                arr, {sl for sl, ws in sf.text.items() if best and f" {best} " in ws}
            )

    def _keep(self, sf: SongFollower, item: tuple) -> None:
        """Raw evidence for a rebuild, the re-filter window's worth."""
        sf.raw.append(item)
        horizon = self.audio_now - sf.follower.refilter_s - 2.0
        while sf.raw and sf.raw[0][1] < horizon:
            sf.raw.popleft()

    # ------------------------------------------------------------ capture thread

    def on_frame(
        self, frame: AudioFrame, update: MusicUpdate | None = None
    ) -> MatcherDecision | None:
        """Advance the audio clock; feed chords; read out when due. `update` is
        the session's analyser output for this frame, when the session runs
        one; otherwise the followers' own analyser (if any) is fed."""
        if update is None and self.chord_analyser is not None:
            try:
                update = self.chord_analyser.feed(frame)
            except Exception:
                log.exception("follower chord analyser failed; carrying on without chords")
                self.chord_analyser = None
        listener = self.listener
        if listener is not None:
            listener.feed(frame.samples, float(frame.t_end))
        with self._lock:
            self.audio_now = float(frame.t_end)
            sf = self.active
            if sf is None:
                return None
            probs = getattr(update, "chord_probs", None) if update is not None else None
            if self.learner is not None and sf.item_idx in self.learner.songs:
                self._learn_feed.put(("audio", sf.item_idx, (float(frame.t_start), frame.samples)))
                if probs is not None:
                    self._learn_feed.put(
                        (
                            "chords",
                            sf.item_idx,
                            (self.audio_now - CHORD_LATENCY_S, np.asarray(probs, np.float32)),
                        )
                    )
            # a reference with no chords (library slides alone) has nothing
            # to compare them with, nor a key to find
            if probs is not None and sf.ref.has_chords:
                t_audio = self.audio_now - CHORD_LATENCY_S
                # one frame of chord evidence per 0.1 s of audio the frame covers
                n = max(1, round(frame.duration_seconds * 10))
                for i in range(n):
                    sf.follower.push_chords(t_audio - (n - 1 - i) * 0.1, self.audio_now, probs)
                    self._keep(sf, ("chord", t_audio - (n - 1 - i) * 0.1, self.audio_now, probs))
                self._log(
                    e="chords",
                    item=sf.item_idx,
                    t=t_audio,
                    arr=self.audio_now,
                    n=n,
                    probs=[round(float(x), 4) for x in probs],
                )
                sf.chord_log.append((t_audio, np.asarray(probs, np.float32)))
                self._maybe_set_key(sf)
            if self.audio_now + 1e-9 < self._next_readout:
                return None
            self._next_readout = self.audio_now + READOUT_S
            r = sf.follower.readout(self.audio_now)
            if sf.check is not None:
                sf.check_mass = next_line.slide_mass(
                    r.marginal_now, sf.follower._slide_idx[: sf.ref.P], sf.ref.slide_words
                )
                sf.check_slide = r.slide
                self._apply_flip(sf, r)
            self._log(
                e="readout",
                item=sf.item_idx,
                t=self.audio_now,
                slide=r.slide,
                share=round(r.share, 4),
                tempo=round(r.tempo_qps, 3),
            )
            try:
                self._flow = flow_evidence(sf, r, self.audio_now)
            except Exception:
                log.exception("follower position for continuous mode failed")
                self._flow = None
            return self._decision(sf, r)

    def take_over(self, old: LiveFollowers) -> None:
        """Continue `old`'s session: its clock, scorer pace and load level,
        and what its learner learned of songs still in the setlist."""
        with old._lock:
            audio_now, last_scored, level = old.audio_now, old._last_scored_t, old.load_level
        with self._lock:
            self.audio_now = audio_now
            self._last_scored_t = last_scored
            self.load_level = level
        old_learner, new_learner = old.learner, self.learner
        old.detach()
        if old_learner is not None and new_learner is not None:
            # a setlist edit mid-service: an unchanged song keeps its learning
            # (handed over through the feed: only the learner's thread
            # touches its songs)
            # each old song to one new item: the same place if it stayed,
            # else the first place with the same slides; handed over only
            # once the old learner's thread is done with it
            old_songs = dict(old_learner.songs)
            used: set[int] = set()
            thread = old._learn_thread
            for item, sg in sorted(new_learner.songs.items()):
                same = old_songs.get(item)
                if same is not None and same.slides == sg.slides and item not in used:
                    pick = item
                else:
                    pick = next(
                        (
                            i
                            for i, o in sorted(old_songs.items())
                            if i not in used and o.slides == sg.slides
                        ),
                        None,
                    )
                if pick is not None:
                    used.add(pick)
                    self._learn_feed.put(("adopt", item, (thread, old_songs[pick])))

    # ------------------------------------------------------------ in-service learning

    def start_learning(self, learner, alive: Callable[[], bool]) -> None:
        """Run `learner` (`follower.learn.ServiceLearner`) on its own thread
        while `alive()` (the session still uses these followers)."""
        self.learner = learner
        self._learn_thread = threading.Thread(
            target=self._learn_loop, args=(alive,), name="service-learner", daemon=True
        )
        self._learn_thread.start()

    def align_words(self, audio: np.ndarray, words: list[str], timeout: float = 30.0):
        """Forced alignment, run on the ASR thread (`side_pass`), so the GPU
        stays one queue. Called from the learner's thread; None on timeout."""
        if (
            self.scorer is None
            or not hasattr(self.scorer, "word_starts")
            or self._detached
            or self.load_level >= 1  # it wouldn't run before the wait ran out
        ):
            return None
        done, cancelled = threading.Event(), threading.Event()
        box: list = []
        self._align_jobs.put((audio, words, box, done, cancelled))
        if done.wait(timeout) and box:
            return box[0]
        # nobody ran it (the AI switched off, the pipeline behind): it must
        # not run later for nobody, holding up Whisper when the AI is back
        cancelled.set()
        return None

    def detach(self) -> None:
        """These followers are no longer the session's (stopped, replaced):
        the learner stops at its next step, nothing waits on alignments."""
        self._detached = True
        if self.listener is not None:
            self.listener.stop()
        while True:
            try:
                *_job, done, cancelled = self._align_jobs.get_nowait()
            except queue.Empty:
                break
            cancelled.set()
            done.set()

    def _run_align_job(self) -> None:
        if self.load_level >= 1:
            # behind: the look-back can wait, Whisper can't; but don't let
            # jobs nobody waits for any more pile up (each holds its audio)
            self._drop_cancelled_jobs()
            return
        while True:
            try:
                audio, words, box, done, cancelled = self._align_jobs.get_nowait()
            except queue.Empty:
                return
            if not cancelled.is_set():
                break
        started = time.monotonic()
        try:
            box.append(self.scorer.word_starts(audio, words))  # type: ignore[union-attr]
            log.info(
                "look-back aligned %d words over %.1f s of audio in %.0f ms",
                len(words),
                len(audio) / SAMPLE_RATE,
                (time.monotonic() - started) * 1000,
            )
        except Exception:
            log.exception("look-back alignment failed")
            box.append(None)
        finally:
            done.set()

    def _drop_cancelled_jobs(self) -> None:
        keep = []
        while True:
            try:
                job = self._align_jobs.get_nowait()
            except queue.Empty:
                break
            if not job[-1].is_set():
                keep.append(job)
        for job in keep:
            self._align_jobs.put(job)

    def _learn_loop(self, alive: Callable[[], bool]) -> None:
        from auto_presenter.follower.learn import TICK_S

        last: dict[int, float] = {}

        def running() -> bool:
            return not self._detached and alive()

        while running():
            time.sleep(0.5)
            learner = self.learner
            if learner is None:
                return
            learner.alive = running
            try:
                while True:
                    kind, item, payload = self._learn_feed.get_nowait()
                    if kind == "words":
                        learner.on_words(item, payload)
                    elif kind == "audio":
                        learner.on_audio(item, *payload)
                    elif kind == "adopt":
                        old_thread, song = payload
                        if old_thread is not None:
                            old_thread.join(timeout=150.0)  # its slow calls end
                        learner.adopt(item, song)
                    else:
                        learner.on_chords(item, *payload)
            except queue.Empty:
                pass
            except Exception:
                log.exception("in-service learning feed failed; learning stops")
                self.learner = None
                return
            item, now = self.current, self.audio_now
            if item is None or item not in learner.songs or now - last.get(item, -1e9) < TICK_S:
                continue
            last[item] = now
            started = time.monotonic()
            try:
                known = learner.tick(item, now)
            except Exception:
                log.exception("in-service learning failed; this song keeps what it has")
                continue
            self.learn_ms = [*self.learn_ms, (time.monotonic() - started) * 1000][-60:]
            if known is not None:
                self.apply_learned(item, known)

    def apply_learned(self, item: int, known: dict[int, SegmentSpec]) -> None:
        """Rebuild `item`'s reference with the learned slides; carry the belief
        across by each slide's share, re-push the recent raw evidence."""
        with self._lock:
            sf = self.songs.get(item)
            if sf is None or sf.spec is None:
                return
            spec = replace(sf.spec, segments=[known.get(g.slide, g) for g in sf.spec.segments])
            started = time.monotonic()
            ref = SegmentReference(spec, sf.slides, sf.sections)
            old = sf.follower
            nf = OnlineFollower(ref, identical=sf.ident)
            nf.alpha, nf.ck_frame = carry(old, ref), old.ck_frame
            nf.shown, nf._heard_at = old.shown, old._heard_at
            if old.talk is not None and nf.talk is not None:
                old.talk.pairs = nf.talk.pairs
                nf.talk = old.talk  # the talk heard so far, judged on the new lyrics
            nf.music_enabled = old.music_enabled or ref.has_chords
            if hasattr(old, "_heard_line"):
                nf._heard_line = old._heard_line
            sf.ref, sf.follower, sf.cell_words = ref, nf, None
            for kind, t, arr, payload in list(sf.raw):
                if int(t * nf.fps) <= nf.ck_frame:
                    continue
                if kind == "word":
                    nf.push_word(t, arr, payload)
                elif kind == "scored":
                    self._push_scored(sf, t, arr, payload)
                elif kind == "chord" and ref.has_chords:
                    nf.push_chords(t, arr, payload)
            self.rebuilds += 1
            ms = (time.monotonic() - started) * 1000
            log.info(
                "item %d: learned slides %s (rebuild %d, %.0f ms; look-back median %.0f ms)",
                item,
                sorted(known),
                self.rebuilds,
                ms,
                float(np.median(self.learn_ms)) if self.learn_ms else 0.0,
            )
            self._log(
                e="learned",
                item=item,
                t=self.audio_now,
                slides=sorted(known),
                ms=round(ms, 1),
            )

    def take_flow(self) -> FlowEvidence | None:
        """The latest position for continuous mode, once."""
        with self._lock:
            flow, self._flow = self._flow, None
            return flow

    def _maybe_set_key(self, sf: SongFollower) -> None:
        if sf.ref.source.startswith("library"):
            # chords learned in the service are in the band's key already:
            # no key to find, so no log to keep (it grew for the whole song)
            sf.follower.music_enabled = True
            sf.chord_log.clear()
            return
        heard_s = self.audio_now - sf.entered_at
        if heard_s < KEY_FIRST_S or self.audio_now - sf.key_checked_at < KEY_EVERY_S:
            return
        sf.key_checked_at = self.audio_now
        cutoff = self.audio_now - KEY_WINDOW_S
        sf.chord_log = [(t, p) for t, p in sf.chord_log if t >= cutoff]
        if len(sf.chord_log) < 20:
            return
        heard = np.stack([p for _, p in sf.chord_log])
        k, corr = sheet.estimate_key_shift(heard, sf.ref.chord_sheet)
        if k != sf.ref.key_shift or not sf.follower.music_enabled:
            log.info(
                "item %d: key %+d semitones from the sheet (correlation %.2f)",
                sf.item_idx,
                k,
                corr[k],
            )
        sf.ref.set_key(k)
        sf.follower.music_enabled = True
        self._log(e="key", item=sf.item_idx, k=int(k), t=self.audio_now)

    def _decision(self, sf: SongFollower, r: Readout) -> MatcherDecision | None:
        if r.slide is None or r.slide == sf.follower.shown:
            return None
        sf.follower.shown = r.slide
        return MatcherDecision(
            item_idx=sf.item_idx,
            slide_idx=int(r.slide),
            t_seconds=self.audio_now,
            confidence=float(r.share),
        )


def carry(old: OnlineFollower, new_ref: SegmentReference) -> np.ndarray:
    """The old belief (rates x cells) on a rebuilt reference's cells, by each
    slide's share: a place 40% through a slide stays 40% through it. A slide
    with several segments (a loop's passes, a roadmap's copies) maps pass to
    pass, in order, so a rebuild keeps the count."""
    o, n = old.ref, new_ref
    a = np.zeros((len(n.rates), n.P), np.float32)
    a[:, n.intro_cell] += old.alpha[:, o.intro_cell]

    def passes(ref, s):
        segs = sorted(
            {int(g) for g in ref.seg_of[ref.slide_of == s]} - {int(ref.seg_of[ref.intro_cell])}
        )
        return [np.where(ref.seg_of == g)[0] for g in segs]

    for s in {int(x) for x in o.slide_of if x is not None and x >= 0}:
        old_passes, new_passes = passes(o, s), passes(n, s)
        if not old_passes or not new_passes:
            continue
        for i, oc in enumerate(old_passes):
            nc = new_passes[min(i, len(new_passes) - 1)]
            k = np.round(np.arange(len(oc)) * (len(nc) - 1) / max(1, len(oc) - 1)).astype(int)
            np.add.at(a, (slice(None), nc[k]), old.alpha[:, oc])
    return a / max(float(a.sum()), 1e-12)


def build_live_followers(
    items: list[tuple[int, list[list[str]], list[str], dict | None]],
    identical: dict[tuple[int, int], set[int]] | None = None,
    scorer: Scorer | None = None,
    chord_analyser: object | None = None,
    without_sheet: bool = False,
    learned: dict[int, LearnedSong] | None = None,
) -> LiveFollowers:
    """`items`: (item_idx, slides, slide_sections, stored lead-sheet score or None).

    A song with a lead sheet that places at least `MIN_COVERAGE` of its sung
    slides is followed by the sheet. Any other song is followed by its library
    slides alone (`spec.library_spec`) when `without_sheet`, and is otherwise
    left to the production matcher. Either way, what past services taught
    about the song (`learned`, by item) is laid on top (`profile.learned_spec`)."""
    from auto_presenter.follower import score as sc

    songs: dict[int, SongFollower] = {}
    for item_idx, slides, sections, data in items:
        spec = None
        if data:
            try:
                spec = sheet_spec(sc.from_extract(data), slides, sections)
            except Exception:
                log.exception("item %d: lead sheet could not be built into a reference", item_idx)
            n_sung = sum(1 for sw in slides if sw)
            if spec is not None and len(spec.covered) < MIN_COVERAGE * n_sung:
                log.info(
                    "item %d: lead sheet places only %d of %d sung slides",
                    item_idx,
                    len(spec.covered),
                    n_sung,
                )
                spec = None
        if spec is None and without_sheet:
            spec = library_spec(slides)
            if not spec.segments:
                spec = None
        if spec is None:
            log.info("item %d: no reference; the matcher follows this song", item_idx)
            continue
        song = (learned or {}).get(item_idx)
        if song is not None:
            spec = learned_spec(spec, song)
        ref = SegmentReference(spec, slides, sections)
        ident = {sl: set((identical or {}).get((item_idx, sl), ())) for sl in range(len(slides))}
        owner = spans_for(slides)
        words = {}
        for (sl, j), sp in owner.items():
            words.setdefault(sl, {})[j] = sp.split()[-1]
        songs[item_idx] = SongFollower(
            item_idx=item_idx,
            ref=ref,
            follower=OnlineFollower(ref, identical=ident),
            owner=owner,
            spans=sorted(set(owner.values())),
            spec=spec,
            slides=slides,
            sections=sections,
            ident=ident,
            text={sl: " " + " ".join(w[k] for k in sorted(w)) + " " for sl, w in words.items()},
        )
        log.info(
            "item %d: score follower ready from its %s (%d cells, %d spans)",
            item_idx,
            ref.source.replace("_", " "),
            ref.P,
            len(songs[item_idx].spans),
        )
    return LiveFollowers(songs=songs, scorer=scorer, chord_analyser=chord_analyser)
