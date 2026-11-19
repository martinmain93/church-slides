"""`QwenStreamer`'s word logic, with the model and aligner faked out.

The real Qwen3-ASR loop is measured in research (docs/research/
QWEN3_ASR_IMPROVEMENTS.md); these pin what this module adds around it:
LocalAgreement-2, flushing a committed window, pausing, the side pass, and
taking `SlidingWindowWrapper`'s place in `AudioSession`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from auto_presenter.asr.qwen_backend import QwenStreamer, QwenStreamingBackend, _sentence_ends
from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame


@dataclass
class _State:
    buffer: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    audio_accum: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    window_text: str = ""
    chunk_size_samples: int = SAMPLE_RATE


class FakeQwen(QwenStreamingBackend):
    """Each step's window text comes from `script`; a step index in `commits`
    starts a new window (the package's commit) before that step's audio."""

    def __init__(
        self,
        script: list[str],
        commits: frozenset[int] = frozenset(),
        fail: frozenset[int] = frozenset(),
    ):
        super().__init__(None)
        self.script = script
        self.commits = commits
        self.fail = fail
        self.steps = 0
        self.fed: list[int] = []

    def new_state(self):
        return _State()

    def feed(self, state, audio):
        i = self.steps
        self.steps += 1
        self.fed.append(len(audio))
        if i in self.fail:
            raise RuntimeError("boom")
        if i in self.commits:
            state.audio_accum = np.zeros(0, np.float32)
        state.audio_accum = np.concatenate([state.audio_accum, audio])
        state.window_text = self.script[min(i, len(self.script) - 1)]

    def align(self, audio, text):
        # evenly spread over the window
        words = text.split()
        dur = len(audio) / SAMPLE_RATE
        step = dur / max(1, len(words))
        return [(w.strip(".,?!"), k * step, (k + 1) * step) for k, w in enumerate(words)]


async def _noop(*args, **kwargs) -> None:
    return None


def frames(seconds: float, start: float = 0.0, hop: float = 0.1) -> list[AudioFrame]:
    n = int(hop * SAMPLE_RATE)
    out = []
    for k in range(round(seconds / hop)):
        t = start + k * hop
        out.append(AudioFrame(samples=np.zeros(n, np.float32), t_start=t, t_end=t + hop))
    return out


def run(backend: QwenStreamingBackend, fs: list[AudioFrame], **kw) -> list[TranscriptEvent]:
    return list(QwenStreamer(backend, pace_safety=0.0, **kw).transcribe(fs))


def finals(events: list[TranscriptEvent]) -> list[str]:
    return [w.text for e in events if e.is_final for w in e.words]


def test_a_word_is_final_once_two_steps_agree_on_it():
    b = FakeQwen(["holy", "holy holy", "holy holy lord", "holy holy lord god"])
    events = run(b, frames(4.0))
    # step 1: nothing to agree with; step 2: "holy"; step 3: "holy holy"; step 4: "lord"
    by_step = [[w.text for w in e.words] for e in events if e.is_final]
    assert by_step[:3] == [["holy"], ["holy"], ["lord"]]
    # what was never confirmed is still flushed at the end
    assert finals(events) == ["holy", "holy", "lord", "god"]
    # every step reports what it heard beyond the final words as a partial
    assert any(not e.is_final for e in events)


def test_a_revised_word_is_not_final_until_it_settles():
    b = FakeQwen(["grace", "great", "great is", "great is thy"])
    events = run(b, frames(4.0))
    assert finals(events) == ["great", "is", "thy"]


def test_final_words_keep_their_times():
    b = FakeQwen(["a b", "a b", "a b c d e f"])
    events = run(b, frames(3.0))
    first = next(e for e in events if e.is_final)
    later = [w for e in events if e.is_final for w in e.words][len(first.words):]
    # the window's later alignment spreads words differently; the finals don't move
    assert [w.t_end for w in first.words] == [1.0, 2.0]
    assert later and later[0].t_start >= 0.0


def test_a_committed_window_flushes_what_it_still_held():
    # step 3 starts a new window: "c" was only heard once in the old one
    b = FakeQwen(["a b", "a b c", "x", "x y"], commits=frozenset({2}))
    events = run(b, frames(4.0))
    flushed = [e for e in events if e.is_final and [w.text for w in e.words] == ["c"]]
    assert flushed and flushed[0].ends_segment
    assert finals(events) == ["a", "b", "c", "x", "y"]
    # the new window's words are on the clock from where it starts (2 s)
    x = next(w for e in events if e.is_final for w in e.words if w.text == "x")
    assert x.t_start >= 2.0


def test_a_slow_step_is_followed_by_one_longer_step_not_a_queue():
    b = FakeQwen(["a"] * 10)
    s = QwenStreamer(b, pace_safety=1.5)
    import time as _time

    real = b.feed

    def slow(state, audio):
        real(state, audio)
        if b.steps == 1:
            _time.sleep(1.0)  # 1.5 s required before the next step

    b.feed = slow  # type: ignore[method-assign]
    list(s.transcribe(frames(4.0)))
    assert b.fed[0] == SAMPLE_RATE
    assert b.fed[1] >= int(1.5 * SAMPLE_RATE)


def test_a_pause_in_the_audio_starts_a_new_stream():
    b = FakeQwen(["one", "one", "two", "two"])
    events = run(b, frames(2.0) + frames(2.0, start=30.0))
    words = [w for e in events if e.is_final for w in e.words]
    assert [w.text for w in words] == ["one", "two"]
    assert words[1].t_start >= 30.0  # on the resumed audio's clock


def test_a_failed_step_is_a_gap_not_an_error():
    b = FakeQwen(["a", "a b", "a b", "a b c"], fail=frozenset({1}))
    events = run(b, frames(4.0))
    assert finals(events)[:2] == ["a", "b"]


def test_the_side_pass_sees_the_last_six_seconds_every_two():
    calls: list[tuple[float, float]] = []

    def side(buffer, t_end):
        calls.append((buffer[0].t_start, t_end))

    run(FakeQwen(["a"]), frames(10.0), side_pass=side)
    assert len(calls) == 5
    for start, end in calls:
        assert end - start <= 6.0 + 1e-9


def test_sentence_ends_come_from_the_decoded_punctuation():
    assert _sentence_ends("Next slide. Thank you!", 4) == [False, True, False, True]
    assert _sentence_ends("Can we sing it again?", 5)[-1] is True
    # counts disagree: mark nothing rather than guess
    assert _sentence_ends("Next slide. -- please", 2) == [False, False]


def test_a_sentence_end_marks_the_final_event():
    b = FakeQwen(["go on.", "go on.", "go on. now"])
    events = run(b, frames(3.0))
    first = next(e for e in events if e.is_final)
    assert [w.text for w in first.words] == ["go", "on"] and first.ends_segment


def test_audio_session_runs_a_streaming_backend_in_the_wrappers_place():
    from auto_presenter.asr.backend import MockASRBackend
    from auto_presenter.asr.streaming import SlidingWindowWrapper
    from auto_presenter.pipeline.audio_session import AudioSession

    def session(backend):
        return AudioSession(
            object(),  # type: ignore[arg-type]
            backend,
            on_event=_noop,
            on_state=_noop,
            wrapper_kwargs={"side_pass": lambda buf, t: None},
        )

    s = session(FakeQwen(["a"]))
    assert isinstance(s._wrapper, QwenStreamer)
    assert s._wrapper.side_pass is not None and s._wrapper.emission_observer is not None
    assert isinstance(session(MockASRBackend())._wrapper, SlidingWindowWrapper)


def test_the_follower_uses_the_whisper_a_qwen_session_carries():
    from auto_presenter.server_follower import _whisper_of

    whisper = object()
    assert _whisper_of(QwenStreamingBackend(whisper)) is whisper  # type: ignore[arg-type]
    other = object()
    assert _whisper_of(other) is other


def test_an_edge_margin_holds_words_the_aligner_squeezed_against_the_newest_audio():
    # "b" ends at the window's edge on every step: with a margin it waits
    b = FakeQwen(["a b", "a b", "a b", "a b c"])
    b.edge_margin_seconds = 0.5
    events = run(b, frames(4.0))
    by_step = [[w.text for w in e.words] for e in events if e.is_final]
    assert by_step[0] == ["a"]
    assert by_step[1] == ["b"]  # once "c" took the edge
    assert finals(events) == ["a", "b", "c"]
