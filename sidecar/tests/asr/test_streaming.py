"""SlidingWindowWrapper tests.

We drive the wrapper with a `StubBackend` that returns a configurable
transcript on each call. That lets us exercise the buffering, step
pacing, stable/partial partitioning, and end-of-stream flush logic
without involving a real model.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import AudioFrame, SAMPLE_RATE


# --- helpers -----------------------------------------------------------------


def frame(t_start: float, t_end: float) -> AudioFrame:
    n = max(1, int((t_end - t_start) * SAMPLE_RATE))
    return AudioFrame(
        samples=np.zeros(n, dtype=np.float32),
        t_start=t_start,
        t_end=t_end,
    )


def frames_at(rate_hz: int, duration_s: float) -> list[AudioFrame]:
    """Generate frames at `rate_hz` frames per second covering `duration_s`."""
    step = 1.0 / rate_hz
    return [frame(i * step, (i + 1) * step) for i in range(int(duration_s * rate_hz))]


class StubBackend:
    """Configurable backend for streaming tests.

    `transcribe_fn` receives the current rolling buffer and returns the
    words the backend "transcribes" for it. The stub records the
    buffer span on every call so tests can assert call cadence.
    """

    def __init__(
        self,
        transcribe_fn: Callable[[list[AudioFrame]], list[Word]],
        *,
        name: str = "stub",
    ) -> None:
        self.transcribe_fn = transcribe_fn
        self._name = name
        self.call_count = 0
        self.last_buffer_span: tuple[float, float] | None = None

    def name(self) -> str:
        return self._name

    def transcribe(self, frames_iter):
        chunks = list(frames_iter)
        self.call_count += 1
        if chunks:
            self.last_buffer_span = (chunks[0].t_start, chunks[-1].t_end)
        words = self.transcribe_fn(chunks)
        if not words:
            return iter(())
        return iter([TranscriptEvent(words=words, is_final=True, segment_id=0)])


class SegmentedBackend:
    """A backend that returns *several* segments, as Whisper actually does.

    `StubBackend` wraps everything in one event, so it cannot express a
    sentence boundary at all — which is why nothing caught the wrapper
    discarding them.
    """

    def __init__(self, segments: list[list[Word]], *, name: str = "segmented") -> None:
        self.segments = segments
        self._name = name
        self.call_count = 0

    def name(self) -> str:
        return self._name

    def transcribe(self, frames_iter):
        list(frames_iter)
        self.call_count += 1
        return iter([
            TranscriptEvent(words=list(seg), is_final=True, segment_id=i)
            for i, seg in enumerate(self.segments)
            if seg
        ])


def word(text: str, t_start: float, t_end: float, conf: float = 0.9) -> Word:
    return Word(text=text, t_start=t_start, t_end=t_end, confidence=conf)


# --- construction validation -------------------------------------------------


def test_rejects_invalid_window_seconds():
    with pytest.raises(ValueError, match="window_seconds"):
        SlidingWindowWrapper(StubBackend(lambda _: []), window_seconds=0)


def test_rejects_invalid_step_seconds():
    with pytest.raises(ValueError, match="step_seconds"):
        SlidingWindowWrapper(StubBackend(lambda _: []), step_seconds=0)


def test_rejects_margin_larger_than_window():
    with pytest.raises(ValueError, match="stable_margin_seconds"):
        SlidingWindowWrapper(
            StubBackend(lambda _: []),
            window_seconds=2.0,
            stable_margin_seconds=2.5,
        )


# --- empty / no-op cases -----------------------------------------------------


def test_empty_frame_stream_yields_nothing():
    backend = StubBackend(lambda _: [])
    wrapper = SlidingWindowWrapper(backend)
    events = list(wrapper.transcribe(iter([])))
    assert events == []
    assert backend.call_count == 0


def test_backend_with_no_words_yields_nothing():
    backend = StubBackend(lambda _: [])
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=2.0, step_seconds=1.0, stable_margin_seconds=0.5
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 3.0))))
    assert events == []
    # Backend should have been invoked at least once during the stream.
    assert backend.call_count >= 1


# --- step pacing -------------------------------------------------------------


def test_backend_runs_at_step_cadence():
    """Buffer step is 1 s; over 5 s of input we should see ~5 runs,
    not one per frame (frames here arrive at 10 Hz = 50 frames)."""
    backend = StubBackend(lambda _: [])
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=3.0, step_seconds=1.0, stable_margin_seconds=0.5
    )
    list(wrapper.transcribe(iter(frames_at(10, 5.0))))
    # First run fires when the elapsed time since -inf exceeds step_seconds,
    # which is the very first frame. Then once per second of audio after.
    # End-of-stream flush adds one more call. Allow some slack.
    assert 5 <= backend.call_count <= 7


def test_smaller_step_runs_more_often():
    backend = StubBackend(lambda _: [])
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=3.0, step_seconds=0.25, stable_margin_seconds=0.5
    )
    list(wrapper.transcribe(iter(frames_at(10, 2.0))))
    # ~ 2.0 / 0.25 = 8 step events + the end-of-stream flush.
    assert 7 <= backend.call_count <= 10


def test_a_backend_can_set_a_slower_floor_than_the_step():
    """A hosted backend's rate limit (`min_step_seconds`) holds even though
    its refusals come back too fast to slow the adaptive pacing."""
    backend = StubBackend(lambda _: [])
    backend.min_step_seconds = 3.0
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=6.0, step_seconds=1.0, stable_margin_seconds=1.5
    )
    list(wrapper.transcribe(iter(frames_at(10, 30.0))))
    # ~30 / 3 = 10 passes + the end-of-stream flush, not ~30.
    assert 9 <= backend.call_count <= 12


# --- partial / final partitioning -------------------------------------------


def test_words_well_into_stable_zone_are_committed():
    """Backend returns 'amazing' at t=0.5 (1.5 s before buffer end at t=2.0).
    With stable_margin=1.0, the cutoff is 1.0; 'amazing' ends at t=1.0 so
    sits exactly on the boundary and commits."""
    captured = [word("amazing", 0.5, 1.0)]
    backend = StubBackend(lambda _buf: list(captured))
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=3.0, step_seconds=1.0, stable_margin_seconds=1.0
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 2.0))))
    final = [e for e in events if e.is_final]
    assert any(
        any(w.text == "amazing" for w in e.words) for e in final
    ), f"expected 'amazing' in finals, got: {[[w.text for w in e.words] for e in events]}"


def test_words_in_flux_emit_as_partials():
    """Backend returns 'grace' at t=1.5–1.9, very close to buffer end at 2.0.
    With stable_margin=1.0, cutoff is 1.0; word at t_end=1.9 is in flux."""
    captured = [word("grace", 1.5, 1.9)]
    backend = StubBackend(lambda _buf: list(captured))
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=3.0, step_seconds=1.0, stable_margin_seconds=1.0
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 2.0))))
    partial = [e for e in events if not e.is_final]
    # Note: the end-of-stream flush will eventually commit 'grace' as final.
    # We expect to see at least one partial emission before that.
    saw_partial = any(any(w.text == "grace" for w in e.words) for e in partial)
    final = [e for e in events if e.is_final]
    saw_final = any(any(w.text == "grace" for w in e.words) for e in final)
    assert saw_partial, "expected 'grace' to appear as a partial before flush"
    assert saw_final, "expected 'grace' to appear in the end-of-stream flush"


def test_partial_then_final_for_same_word():
    """During the stream the word lives in flux; at end-of-stream flush
    the wrapper commits it. The matcher should see partial → final, never
    final → partial."""
    captured = [word("hosanna", 0.4, 0.8)]
    backend = StubBackend(lambda _buf: list(captured))
    # Window large enough that the stable cutoff is always after t_end=0.8
    # for the entire mid-stream period — so the word stays partial until flush.
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=5.0, step_seconds=0.25, stable_margin_seconds=2.0
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 1.0))))
    seen_partial_first = False
    saw_final = False
    for e in events:
        if any(w.text == "hosanna" for w in e.words):
            if not e.is_final and not saw_final:
                seen_partial_first = True
            if e.is_final:
                saw_final = True
                # Once final, no later partials for this word.
    assert seen_partial_first
    assert saw_final


def test_no_double_commit_for_already_emitted_words():
    """Backend returns the SAME word on every call; the wrapper must only
    emit it as final once, then suppress it forever."""
    captured = [word("amen", 0.0, 0.3)]
    backend = StubBackend(lambda _buf: list(captured))
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=3.0, step_seconds=0.5, stable_margin_seconds=0.5
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 3.0))))
    final_amens = sum(
        1 for e in events if e.is_final for w in e.words if w.text == "amen"
    )
    assert final_amens == 1, f"got {final_amens} final 'amen' emissions"


def test_every_spoken_word_commits_exactly_once_under_jitter():
    """The whole invariant, over a run of distinct words with jittered times.

    This is what a real recogniser does: re-running it over an overlapping
    window re-estimates every word slightly differently (faster-whisper
    measures at about ±0.02 s). The wrapper used to suppress repeats by
    comparing a candidate's `t_end` against a watermark that *was* a previous
    word's `t_end`, so any re-estimate landing later slipped through and the
    word was emitted twice — 19–30% of the live finals stream turned out to
    be these immediate repeats, against 0.6–1.5% for the same model reading
    the same audio in one pass.

    Note that `test_no_double_commit_for_already_emitted_words` above returns
    byte-identical timestamps on every call, which is exactly the assumption
    that does not hold in practice — which is why it passed throughout. This
    test fails against the old rule.

    Guards both directions: no word may be duplicated, and none may be
    dropped — a dedup rule that suppresses too eagerly is not a fix.
    """
    spoken = [(f"w{i:02d}", i * 0.5, i * 0.5 + 0.3) for i in range(12)]
    calls = {"n": 0}

    def jittered(buf: list[AudioFrame]) -> list[Word]:
        calls["n"] += 1
        shift = 0.02 if calls["n"] % 2 else -0.02
        t0, t1 = buf[0].t_start, buf[-1].t_end
        return [
            word(t, s + shift, e + shift)
            for t, s, e in spoken
            if s >= t0 and e <= t1
        ]

    backend = StubBackend(jittered)
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=6.0, step_seconds=1.0, stable_margin_seconds=1.5
    )
    emitted: list[str] = []
    for e in wrapper.transcribe(iter(frames_at(10, 8.0))):
        if e.is_final:
            emitted.extend(w.text for w in e.words)

    counts = {t: emitted.count(t) for t, _, _ in spoken}
    duplicated = {t: n for t, n in counts.items() if n > 1}
    dropped = [t for t, n in counts.items() if n == 0]
    assert not duplicated, f"words committed more than once: {duplicated}"
    assert not dropped, f"words never committed: {dropped}"


def test_zero_duration_words_are_not_duplicated():
    """Whisper sometimes emits `t_start == t_end`. Such a word has no width,
    so a midpoint carries no margin at all and a re-estimate 20 ms later slips
    past the watermark. Identity matching is what catches these.

    Observed live as `my [40.44,40.44] -> my [40.46,40.46]`.
    """
    calls = {"n": 0}

    def degenerate(_buf: list[AudioFrame]) -> list[Word]:
        calls["n"] += 1
        t = 0.40 + 0.02 * min(calls["n"] - 1, 3)  # drifts later, then settles
        return [word("my", t, t)]

    backend = StubBackend(degenerate)
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=3.0, step_seconds=0.5, stable_margin_seconds=0.5
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 3.0))))
    n = sum(1 for e in events if e.is_final for w in e.words if w.text == "my")
    assert n == 1, f"zero-duration word committed {n} times"


def test_long_note_re_estimated_by_a_second_is_not_duplicated():
    """Sustained sung notes get re-timed by far more than half a word, so the
    midpoint rule alone cannot catch them — the two estimates overlap.

    Observed live as `all [78.08,79.66] -> all [78.76,80.64]`.
    """
    calls = {"n": 0}

    def held_note(_buf: list[AudioFrame]) -> list[Word]:
        # Commit the first estimate, then re-time it later AND longer — the
        # note stretching is what carries its midpoint past the watermark.
        calls["n"] += 1
        if calls["n"] <= 6:  # commits on pass 6, once the cutoff passes 1.90
            return [word("all", 0.30, 1.90)]
        return [word("all", 1.00, 2.90)]

    backend = StubBackend(held_note)
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=6.0, step_seconds=0.5, stable_margin_seconds=0.5
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 5.0))))
    n = sum(1 for e in events if e.is_final for w in e.words if w.text == "all")
    assert n == 1, f"re-estimated held note committed {n} times"


def test_genuinely_repeated_words_are_still_emitted_twice():
    """The counterweight: a doubled word in a lyric ("so so good") is a real
    repeat and both words must survive. A dedup rule that swallows genuine repetition is not a fix.
    """
    spoken = [("so", 1.0, 1.4), ("so", 1.9, 2.3), ("good", 2.6, 3.0)]

    def stable(buf: list[AudioFrame]) -> list[Word]:
        t0, t1 = buf[0].t_start, buf[-1].t_end
        return [word(t, s, e) for t, s, e in spoken if s >= t0 and e <= t1]

    backend = StubBackend(stable)
    wrapper = SlidingWindowWrapper(backend)
    emitted = [
        w.text
        for e in wrapper.transcribe(iter(frames_at(10, 6.0)))
        if e.is_final
        for w in e.words
    ]
    assert emitted.count("so") == 2, f"genuine repeat lost: {emitted}"
    assert emitted.count("good") == 1


# --- end-of-stream flush -----------------------------------------------------


def test_end_of_stream_flushes_remaining_words():
    """Words that never made it past the stable cutoff during streaming
    must still be emitted as final when the input ends."""
    captured = [
        word("first", 0.1, 0.5),
        word("trailing", 0.9, 1.0),
    ]
    backend = StubBackend(lambda _buf: list(captured))
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=3.0, step_seconds=0.5, stable_margin_seconds=2.5
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 1.0))))
    final_words = {w.text for e in events if e.is_final for w in e.words}
    assert "first" in final_words
    assert "trailing" in final_words


def test_no_flush_when_input_is_empty():
    backend = StubBackend(lambda _buf: [word("noop", 0, 0.1)])
    wrapper = SlidingWindowWrapper(backend)
    events = list(wrapper.transcribe(iter([])))
    assert events == []


# --- monotonic + ordering ----------------------------------------------------


def test_committed_events_are_strictly_increasing_in_time():
    """Across the whole stream, each successive final event's first word
    must start no earlier than the previous final event's last word ended."""

    def supplier(buf: list[AudioFrame]) -> list[Word]:
        if not buf:
            return []
        # Synthesize a transcript that fills [buffer_start, buffer_end - 0.2].
        b_start = buf[0].t_start
        b_end = buf[-1].t_end
        words: list[Word] = []
        t = b_start
        while t + 0.3 < b_end - 0.2:
            words.append(word(f"w{int(t*10)}", t, t + 0.2))
            t += 0.3
        return words

    backend = StubBackend(supplier)
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=4.0, step_seconds=1.0, stable_margin_seconds=1.0
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 6.0))))
    last_t_end = float("-inf")
    for e in events:
        if not e.is_final or not e.words:
            continue
        first_w = e.words[0]
        assert first_w.t_start >= last_t_end - 1e-6, (
            f"final event starting at {first_w.t_start} overlaps prior commit ending at {last_t_end}"
        )
        last_t_end = e.words[-1].t_end


def test_segment_ids_monotonically_increase_on_finals():
    captured = [word("a", 0.1, 0.3), word("b", 0.4, 0.6), word("c", 0.7, 0.9)]
    backend = StubBackend(lambda _buf: list(captured))
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=2.0, step_seconds=0.5, stable_margin_seconds=0.5
    )
    events = list(wrapper.transcribe(iter(frames_at(10, 3.0))))
    final_segment_ids = [e.segment_id for e in events if e.is_final]
    assert final_segment_ids == sorted(set(final_segment_ids)), (
        f"final segment_ids should be strictly increasing, got: {final_segment_ids}"
    )


# --- buffer management -------------------------------------------------------


def test_buffer_trims_to_window():
    """When 4 s of audio passes through a 2 s window, the backend should
    only ever see a buffer ≤ 2 s wide."""
    seen_spans: list[float] = []

    def supplier(buf: list[AudioFrame]) -> list[Word]:
        if buf:
            seen_spans.append(buf[-1].t_end - buf[0].t_start)
        return []

    backend = StubBackend(supplier)
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=2.0, step_seconds=0.5, stable_margin_seconds=0.5
    )
    list(wrapper.transcribe(iter(frames_at(10, 4.0))))
    assert seen_spans
    for span in seen_spans:
        assert span <= 2.0 + 1e-6, f"buffer span {span} exceeded window 2.0"


def test_name_propagates_streaming_prefix():
    wrapper = SlidingWindowWrapper(StubBackend(lambda _: [], name="whisper-tiny"))
    assert wrapper.name() == "streaming(whisper-tiny)"


# --- re-entry ----------------------------------------------------------------


def test_state_resets_on_each_transcribe_call():
    """Calling `transcribe()` twice on the same wrapper should be independent;
    a word committed on the first call must not be suppressed on the second."""
    captured = [word("hello", 0.1, 0.3)]
    backend = StubBackend(lambda _buf: list(captured))
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=2.0, step_seconds=0.5, stable_margin_seconds=0.5
    )

    events1 = list(wrapper.transcribe(iter(frames_at(10, 1.0))))
    events2 = list(wrapper.transcribe(iter(frames_at(10, 1.0))))

    finals1 = [w for e in events1 if e.is_final for w in e.words]
    finals2 = [w for e in events2 if e.is_final for w in e.words]
    assert any(w.text == "hello" for w in finals1)
    assert any(w.text == "hello" for w in finals2), (
        "wrapper state should reset between transcribe() calls"
    )


def test_emission_observer_reports_when_a_word_became_actionable():
    """The gap this exists to expose: a word sung at t is not *knowable* until
    the window holds `stable_margin_seconds` past it and a pass has run.

    `TranscriptEvent` carries only the sung time, and every decision
    downstream is stamped with it — which is why the harness reported
    anticipation the room never saw. This reports the second clock too.
    """
    seen: list[tuple[float, float]] = []

    def observe(event, available_at):
        if event.is_final:
            for w in event.words:
                seen.append((w.t_end, available_at))

    backend = StubBackend(lambda _buf: [word("amazing", 0.5, 1.0)])
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=3.0, step_seconds=1.0, stable_margin_seconds=1.0,
        emission_observer=observe,
    )
    list(wrapper.transcribe(iter(frames_at(10, 2.0))))

    assert seen, "no final event reached the observer"
    for t_end, available_at in seen:
        # Availability is at least the stability margin past the word. That
        # wait is the mechanism, not an implementation detail.
        assert available_at >= t_end + 1.0 - 1e-6, (t_end, available_at)


def test_the_emission_observer_is_measurement_only():
    """It must not change what is emitted, or when — an instrument that alters
    what it measures is worse than none. `_adaptive_step` in particular stays
    timed across the consumer, not just the pass."""
    def build(observer):
        return SlidingWindowWrapper(
            StubBackend(lambda _buf: [word("amazing", 0.5, 1.0)]),
            window_seconds=3.0, step_seconds=1.0, stable_margin_seconds=1.0,
            emission_observer=observer,
        )

    plain = [(e.is_final, [w.text for w in e.words])
             for e in build(None).transcribe(iter(frames_at(10, 2.0)))]
    watched = [(e.is_final, [w.text for w in e.words])
               for e in build(lambda e, t: None).transcribe(iter(frames_at(10, 2.0)))]
    assert plain == watched


# --- sentence boundaries ------------------------------------------------------
#
# The wrapper re-chunks words by its stability clock, so a sentence longer than
# one step arrives in pieces. It used to flatten the backend's segments away
# entirely (`[w for e in events for w in e.words]`), leaving nothing downstream
# able to tell a sentence boundary from a pass boundary.
#
# Measured live: Whisper returned "Can you go to the bridge of what a beautiful
# name?" as one clean 11-word segment and the wrapper delivered it as four
# events of 4, 1, 3 and 2 words. The spoken-instruction path saw fragments,
# the model was handed half a request, and the command was not followed.


def _sentence() -> list[Word]:
    """One backend segment spanning several wrapper steps."""
    texts = ["can", "you", "go", "to", "the", "bridge", "of", "what", "a",
             "beautiful", "name"]
    return [word(t, 1.0 + i * 0.3, 1.0 + (i + 1) * 0.3) for i, t in enumerate(texts)]


def test_only_the_run_that_reaches_the_sentence_end_says_so():
    """The boundary belongs to the piece that contains it, not to every piece
    of the sentence — otherwise a consumer acts on a fragment."""
    sentence = _sentence()
    backend = SegmentedBackend([sentence])
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=6.0, step_seconds=1.0, stable_margin_seconds=1.5
    )
    events = [e for e in wrapper.transcribe(iter(frames_at(10, 7.0))) if e.is_final]

    assert len(events) > 1, "this fixture is meant to span several steps"
    ending = [e for e in events if e.ends_segment]
    assert len(ending) == 1, "exactly one run finishes the sentence"
    assert ending[0].words[-1].text == "name"
    # And the whole sentence is recoverable by concatenating in order.
    got = [w.text for e in events for w in e.words]
    assert got == [w.text for w in sentence]


def test_two_sentences_in_one_pass_are_both_marked():
    first = [word("go", 1.0, 1.2), word("back", 1.2, 1.4)]
    second = [word("thanks", 2.0, 2.2), word("everyone", 2.2, 2.4)]
    backend = SegmentedBackend([first, second])
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=6.0, step_seconds=1.0, stable_margin_seconds=1.5
    )
    events = [e for e in wrapper.transcribe(iter(frames_at(10, 5.0))) if e.is_final]
    ends = [w.text for e in events if e.ends_segment for w in e.words[-1:]]
    # Both boundaries survive, even though the wrapper emits on its own clock.
    assert "everyone" in ends


def test_a_backend_that_reports_no_segmentation_behaves_as_before():
    """`StubBackend` returns one event per pass with no sentence structure, and
    every hand-built event in these tests defaults `ends_segment` False."""
    backend = StubBackend(lambda _b: [word("amazing", 0.5, 1.0)])
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=3.0, step_seconds=1.0, stable_margin_seconds=1.0
    )
    events = [e for e in wrapper.transcribe(iter(frames_at(10, 2.0))) if e.is_final]
    assert events
    # One word, and it *is* that stub's only segment end — so this asserts the
    # default rather than the detection: a hand-built event says nothing.
    assert TranscriptEvent(words=[word("x", 0.0, 1.0)], is_final=True).ends_segment is False


def test_the_end_of_stream_tail_closes_its_sentence():
    """Audio stops mid-sentence at the end of a service. A consumer waiting for
    a boundary would otherwise wait forever on the last thing said."""
    backend = SegmentedBackend([[word("one", 0.2, 0.4), word("more", 0.4, 0.6)]])
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=6.0, step_seconds=1.0, stable_margin_seconds=1.5
    )
    events = [e for e in wrapper.transcribe(iter(frames_at(10, 1.0))) if e.is_final]
    assert events and events[-1].ends_segment


# --- junk --------------------------------------------------------------------
#
# Traced 2026-09-29: Whisper's first output on a near-silent opening was a lone
# "!" (probability 0.00), which the matcher took as a word and let decide the
# session's first song.


def test_a_punctuation_only_token_never_reaches_the_matcher():
    backend = SegmentedBackend([[word("!", 0.2, 0.9, conf=0.0)]])
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=6.0, step_seconds=1.0, stable_margin_seconds=1.5
    )
    events = [e for e in wrapper.transcribe(iter(frames_at(10, 3.0))) if e.is_final]
    assert [w.text for e in events for w in e.words] == []


def test_a_dropped_trailing_token_keeps_the_sentence_boundary():
    """The boundary moves to the last kept word rather than vanishing with
    the "!" — spoken instructions wait on it."""
    backend = SegmentedBackend(
        [[word("go", 1.0, 1.2), word("back", 1.2, 1.4), word("!", 1.4, 1.5)]]
    )
    wrapper = SlidingWindowWrapper(
        backend, window_seconds=6.0, step_seconds=1.0, stable_margin_seconds=1.5
    )
    events = [e for e in wrapper.transcribe(iter(frames_at(10, 5.0))) if e.is_final]
    ends = [w.text for e in events if e.ends_segment for w in e.words[-1:]]
    assert ends == ["back"]


# --- a failed pass (engine review 2026-11-10, finding 10) ---------------------


def test_one_failed_pass_is_a_gap_not_the_end():
    calls = {"n": 0}

    def flaky(_buf):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("decoder blew up")
        return [Word(text="grace", t_start=0.1, t_end=0.4)]

    wrapper = SlidingWindowWrapper(StubBackend(flaky))
    events = list(wrapper.transcribe(iter(frames_at(10, 6.0))))
    assert calls["n"] > 2, "passes kept running after the failure"
    assert any(e.words for e in events)


def test_failures_in_a_row_still_end_the_session():
    def broken(_buf):
        raise RuntimeError("model gone")

    from auto_presenter.asr.streaming import MAX_FAILED_PASSES

    backend = StubBackend(broken)
    with pytest.raises(RuntimeError, match="model gone"):
        list(SlidingWindowWrapper(backend).transcribe(iter(frames_at(10, 30.0))))
    assert backend.call_count == MAX_FAILED_PASSES
