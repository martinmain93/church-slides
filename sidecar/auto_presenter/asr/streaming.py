"""Sliding-window streaming wrapper.

`SlidingWindowWrapper` turns *any* batch `ASRBackend` (Whisper, mock, …)
into a streaming one. Approach:

- Keep a rolling buffer of the last `window_seconds` of audio (default 6 s).
- Every `step_seconds` (default 1 s), run the wrapped backend on the buffer.
- Split the resulting words into two groups by timestamp:
    - **stable** — `t_end ≤ buffer_end − stable_margin_seconds`. Whisper
      had ≥ `stable_margin_seconds` of right-context to nail this region
      down, so we commit it as `is_final=True`.
    - **in flux** — closer to the buffer edge; emit as `is_final=False`
      (a soft hint the matcher can use as evidence without committing).
- Suppress anything already emitted, by commit watermark *and* by token
  identity — see `_already_committed`, which documents why both are needed
  and what each one catches. Whisper may slightly revise the *text* of a
  once-committed word on a later pass; we deliberately don't change our
  mind, on the theory that the matcher prefers a stable transcript over an
  oscillating one.
- At end-of-stream, flush whatever remains in the buffer as one final
  event so the matcher sees the trailing words.

This is the simplest design that gives the matcher (a) anticipation
runway (it can react to in-flux partials) and (b) stability (committed
words don't move). The whisper-streaming literature has fancier
stability-voting schemes; we'll upgrade if eval data shows we need it.

Concretely: a 6 s window + 1 s step means the wrapped backend runs at
6× real-time-factor on average. That's fine for Whisper-tiny on a
modern laptop CPU; budget it carefully when scaling to larger models.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from auto_presenter.asr.backend import ASRBackend
from auto_presenter.asr.junk import JunkFilter
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import AudioFrame

log = logging.getLogger(__name__)

#: Speech passes that may fail in a row before the session gives up.
MAX_FAILED_PASSES = 5


def _normalize(text: str) -> str:
    """Casefold and strip punctuation so "Jesus," and "jesus" compare equal."""
    return "".join(c for c in text.lower() if c.isalnum() or c == "'")


def _ends_a_segment(last: Word, segment_ends: set[float]) -> bool:
    """Did this run of words stop where a backend segment stopped?

    Compared with a tolerance because the end time makes a round trip through
    the backend's own arithmetic; an exact float match would be true by luck.
    """
    return any(abs(last.t_end - t) <= 1e-6 for t in segment_ends)


@dataclass
class SlidingWindowWrapper:
    """Compose a batch `ASRBackend` into a streaming one."""

    backend: ASRBackend
    window_seconds: float = 6.0
    step_seconds: float = 1.0
    #: Words within this many seconds of the right edge of the buffer are
    #: considered "in flux" — emitted as partials only.
    stable_margin_seconds: float = 1.5
    #: Optional measurement hook, called once per emitted event with
    #: `(event, available_at)` — the point on the audio timeline at which that
    #: event could first have *acted*, as opposed to when its words were sung.
    #:
    #: The two differ by the whole reason this wrapper exists. A word sung at
    #: t=100 is not reported until the window holds
    #: `stable_margin_seconds` of right context past it and a pass has run, so
    #: it lands nearer t=103. `TranscriptEvent` carries only the first number,
    #: and every decision downstream is stamped with it — which is why the
    #: harness reported anticipation the room never saw. This reports the
    #: second number too.
    #:
    #: Measurement only, and `None` in production: nothing here changes what
    #: is emitted or when, and in particular it does not touch
    #: `_adaptive_step`, which is deliberately measured across the *consumer*
    #: as well as the pass.
    emission_observer: Callable[[TranscriptEvent, float], None] | None = None
    #: Optional context-manager factory called around each backend pass, used
    #: to attribute its cost. Kept as a plain callable so this module doesn't
    #: depend on the pipeline package.
    pass_timer: Callable[[], AbstractContextManager[object]] | None = None
    #: Safety factor for adaptive pacing. The wrapper will not start another
    #: backend pass until at least `last_pass_duration * this` seconds of
    #: audio have accumulated, so it always consumes audio faster than it
    #: arrives. 1.5 leaves a third of the time for everything else.
    pace_safety: float = 1.5
    #: Two same-text words closer than this are treated as one token the
    #: backend re-estimated, not as the singer repeating themselves. Sung
    #: repeats ("so so good") sit around 0.3–0.5 s apart, re-estimates
    #: overlap or nearly touch, so the line is drawn between them.
    repeat_gap_seconds: float = 0.25
    #: Words Whisper itself marks as not-words — see `asr.junk`.
    junk: JunkFilter = field(default_factory=JunkFilter)
    #: Optional work that shares this thread — and so the GPU — with the
    #: backend, run *first* every `side_pass_seconds` of audio: the score
    #: follower's span scorer (`follower.scorer`). Serial, scorer first, was
    #: measured to keep the scorer at its standalone latency (0.17 s against
    #: 0.37 s on two threads) at no cost to transcription. Called with the
    #: rolling buffer and its end time; exceptions are logged and swallowed.
    side_pass: Callable[[list[AudioFrame], float], None] | None = None
    side_pass_seconds: float = 2.0

    #: Internal state, reset every `transcribe()` call.
    _last_run_t: float = field(default=float("-inf"), init=False)
    #: Audio-seconds required between passes, raised when the backend is slow.
    _adaptive_step: float = field(default=0.0, init=False)
    _committed_until_t: float = field(default=float("-inf"), init=False)
    _last_side_t: float = field(default=float("-inf"), init=False)
    #: Wall seconds the side pass has used since the last backend pass. It
    #: runs on this thread, so it counts against the pacing like the pass does.
    _side_debt: float = field(default=0.0, init=False)
    _segment_id: int = field(default=0, init=False)
    #: (normalized text, t_start, t_end) for recently committed words, used
    #: to recognise a re-estimate of a word we already emitted.
    _recent: list[tuple[str, float, float]] = field(
        default_factory=list, init=False
    )

    def __post_init__(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if self.step_seconds <= 0:
            raise ValueError("step_seconds must be > 0")
        if self.stable_margin_seconds < 0:
            raise ValueError("stable_margin_seconds must be >= 0")
        if self.stable_margin_seconds >= self.window_seconds:
            raise ValueError(
                "stable_margin_seconds must be < window_seconds, "
                "otherwise the stable region is empty"
            )

    def name(self) -> str:
        return f"streaming({self.backend.name()})"

    def transcribe(
        self, frames: Iterable[AudioFrame]
    ) -> Iterator[TranscriptEvent]:
        """Stream `TranscriptEvent`s as the underlying backend produces them."""
        self._reset()
        buffer: list[AudioFrame] = []
        last_frame: AudioFrame | None = None

        for frame in frames:
            buffer.append(frame)
            last_frame = frame
            # Trim the rolling buffer.
            while buffer and frame.t_end - buffer[0].t_start > self.window_seconds:
                buffer.pop(0)

            if (
                self.side_pass is not None
                and frame.t_end - self._last_side_t >= self.side_pass_seconds
            ):
                self._last_side_t = frame.t_end
                side_started = time.monotonic()
                try:
                    self.side_pass(buffer, frame.t_end)
                except Exception:
                    log.exception("side pass failed; transcription carries on")
                self._side_debt += time.monotonic() - side_started

            # Step gate — adaptive, not fixed.
            #
            # A fixed step makes this pipeline open-loop: it asks the backend
            # for a pass every `step_seconds` of audio whether or not the
            # backend can finish one in that time. When it can't, the only
            # relief valve left is the capture queue discarding audio, which
            # is the worst available outcome — the matcher gets a transcript
            # with holes in it and nobody upstream knows.
            #
            # Observed live: `base.en` on a 6s window runs at ~0.46s per pass
            # on an idle machine but ~1.0s inside the app, where the UI and
            # audio threads compete. At a 1s step that is a real-time factor
            # of 1.0 — exactly marginal, so lag accumulated for a minute and
            # then 102 chunks of audio were dropped at once.
            #
            # Pacing off the backend's own measured speed closes the loop:
            # a pass that took 1.0s won't be followed by another until 1.5s of
            # audio have arrived. The cost is a less frequent belief update,
            # which is a far better trade than losing the audio outright —
            # the window still covers the recent past either way.
            #
            # A hosted backend can also set a floor of its own
            # (`min_step_seconds`): a refused request comes back in a tenth
            # of a second, so its pace never slows this loop, and it would
            # otherwise be asked once a second however few it may make.
            required = max(
                self.step_seconds,
                self._adaptive_step,
                getattr(self.backend, "min_step_seconds", 0.0),
            )
            if frame.t_end - self._last_run_t < required:
                continue
            self._last_run_t = frame.t_end

            started = time.monotonic()
            yield from self._run_and_emit(buffer, buffer_end=frame.t_end)
            self._adaptive_step = (time.monotonic() - started + self._side_debt) * self.pace_safety
            self._side_debt = 0.0

        # End-of-stream flush: one last pass over whatever's in the buffer,
        # committing every remaining word past `_committed_until_t`. This
        # is what gets the trailing words of the recording into the matcher.
        if buffer and last_frame is not None:
            yield from self._flush(buffer, buffer_end=last_frame.t_end)

    # ----------------------------------------------------------- internals

    def _reset(self) -> None:
        self._last_pass_seconds = 0.0
        self._failed_passes = 0
        self._last_run_t = float("-inf")
        self._last_side_t = float("-inf")
        self._side_debt = 0.0
        self._adaptive_step = 0.0
        self._committed_until_t = float("-inf")
        self._segment_id = 0
        self._recent = []

    def _run_and_emit(
        self, buffer: list[AudioFrame], *, buffer_end: float
    ) -> Iterator[TranscriptEvent]:
        words, segment_ends = self._collect_words(buffer)
        if not words:
            return

        stable_cutoff = buffer_end - self.stable_margin_seconds
        committed_new: list[Word] = []
        partial: list[Word] = []

        for w in words:
            if self._already_committed(w):
                continue
            if w.t_end <= stable_cutoff:
                committed_new.append(w)
            else:
                partial.append(w)

        committed_new.sort(key=lambda w: w.t_start)
        partial.sort(key=lambda w: w.t_start)

        # When this pass's findings could first have been acted on: the audio
        # position that triggered it, plus however long the backend took.
        available_at = buffer_end + self._last_pass_seconds

        if committed_new:
            self._committed_until_t = committed_new[-1].t_end
            self._remember(committed_new)
            event = TranscriptEvent(
                words=committed_new,
                is_final=True,
                segment_id=self._segment_id,
                # This run ends where the backend's own segment ended, so a
                # consumer waiting for a whole sentence has one.
                ends_segment=_ends_a_segment(committed_new[-1], segment_ends),
            )
            if self.emission_observer is not None:
                self.emission_observer(event, available_at)
            yield event
            self._segment_id += 1

        if partial:
            event = TranscriptEvent(
                words=partial,
                is_final=False,
                segment_id=self._segment_id,
            )
            if self.emission_observer is not None:
                self.emission_observer(event, available_at)
            yield event

    def _already_committed(self, w: Word) -> bool:
        """Whether `w` is something we've already emitted.

        Two tests, because the backend fails to be repeatable in two
        different ways. Both were measured against real captured passes, not
        assumed — the wrapper was emitting 19–30% immediate repeats ("Before
        Before I spoke a a word") where the same model reading the same audio
        in one pass emits 0.6–1.5%. Downstream, the particle filter advances
        position per observed word, so the inflated stream made it race ahead
        of the song.

        1. **Region** — is the word's midpoint behind the commit watermark?
           The watermark is a previously-committed word's `t_end`, so testing
           a candidate's own `t_end` against it puts the comparison exactly on
           the boundary, and a re-estimate a few tens of milliseconds later
           slips straight through. The midpoint sits half a word from the
           boundary instead, which absorbs that.

        2. **Identity** — is this the same token, re-timed? Sustained sung
           notes get re-estimated by up to a second between passes, far more
           than half a word, and Whisper sometimes emits zero-duration words
           where a midpoint has no margin at all. Both slip past test 1. A
           re-estimate has the same text and overlaps (or nearly touches) an
           instance we already committed; genuine repetition ("so so good to
           me") is separated by a real pause. `repeat_gap_seconds` is where
           that line is drawn — verified against the residual duplicates,
           which are all genuine sung repeats.

        Only words still reachable by the rolling buffer can be re-estimated,
        so the memory is pruned to twice the window. Anything older can only
        be the singer actually repeating themselves.
        """
        if (w.t_start + w.t_end) / 2 <= self._committed_until_t:
            return True
        text = _normalize(w.text)
        if not text:
            return False
        gap = self.repeat_gap_seconds
        for prev_text, t_start, t_end in self._recent:
            if prev_text != text:
                continue
            overlaps = w.t_start < t_end and t_start < w.t_end
            adjacent = (w.t_start - t_end) < gap and (t_start - w.t_end) < gap
            if overlaps or adjacent:
                return True
        return False

    def _remember(self, words: list[Word]) -> None:
        """Record committed words, dropping any the buffer can no longer see."""
        for w in words:
            self._recent.append((_normalize(w.text), w.t_start, w.t_end))
        horizon = self._committed_until_t - 2 * self.window_seconds
        self._recent = [r for r in self._recent if r[2] >= horizon]

    def _flush(
        self, buffer: list[AudioFrame], *, buffer_end: float
    ) -> Iterator[TranscriptEvent]:
        words, segment_ends = self._collect_words(buffer)
        tail = [w for w in words if not self._already_committed(w)]
        if not tail:
            return
        tail.sort(key=lambda w: w.t_start)
        self._committed_until_t = tail[-1].t_end
        self._remember(tail)
        event = TranscriptEvent(
            words=tail,
            is_final=True,
            segment_id=self._segment_id,
            # End of stream: whatever the backend last said is finished, so
            # the tail always closes a segment even when the audio stopped
            # mid-sentence. A consumer waiting for a boundary would otherwise
            # wait forever on the last utterance of a service.
            ends_segment=True,
        )
        # The trailing words land here, not in `_run_and_emit`, and they are a
        # real part of the transcript — an observer that missed them would
        # under-report the tail of every recording.
        if self.emission_observer is not None:
            self.emission_observer(event, buffer_end + self._last_pass_seconds)
        yield event
        self._segment_id += 1

    def _collect_words(
        self, buffer: list[AudioFrame]
    ) -> tuple[list[Word], set[float]]:
        """Run one backend pass. Returns its words *and* its segment ends.

        The flattening here used to be total — `[w for e in events for w in
        e.words]` — which discarded the one thing the backend knows and this
        wrapper cannot reconstruct: where a sentence stopped. Whisper returns
        segments with punctuation; re-chunking by the stability clock then
        shreds them, and no consumer could tell a sentence boundary from a
        one-second pass boundary.

        So the words still come back flat — the matcher wants them the moment
        they are stable, in whatever quantity — and the boundaries come back
        beside them.
        """
        started = time.monotonic()
        try:
            if self.pass_timer is None:
                events = list(self.backend.transcribe(iter(buffer)))
            else:
                with self.pass_timer():
                    events = list(self.backend.transcribe(iter(buffer)))
        except Exception:
            # One failed pass is a gap, not the end of the service: MLX and
            # Groq already caught theirs, faster-whisper (Intel, Windows) did
            # not, so a single error stopped live AI for good. Only several
            # in a row end the session.
            self._failed_passes += 1
            if self._failed_passes >= MAX_FAILED_PASSES:
                raise
            log.exception(
                "speech pass failed (%d in a row); skipping it", self._failed_passes
            )
            self._last_pass_seconds = time.monotonic() - started
            return [], set()
        self._failed_passes = 0
        # How long the backend itself took, isolated from whatever the
        # consumer does with the events. `_adaptive_step` deliberately
        # measures the wider loop; `available_at` must not.
        self._last_pass_seconds = time.monotonic() - started
        # Junk goes before anything reads it — the stability clock, dedup and
        # the segment ends alike — and a segment's end is its last *kept* word,
        # or a dropped trailing "!" would take the sentence boundary with it.
        words: list[Word] = []
        segment_ends: set[float] = set()
        for e in events:
            kept = self.junk.keep(e.words)
            words.extend(kept)
            if kept:
                segment_ends.add(kept[-1].t_end)
        return words, segment_ends
