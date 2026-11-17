"""WhisperBackend tests.

Unit tests use a hand-rolled mock that mimics faster-whisper's surface
(`transcribe(audio, word_timestamps=True, ...) -> (segments, info)`).
That lets us validate the audio buffering + segment-to-event mapping
without touching the network or the filesystem.

The optional `test_real_model_transcribes_silence` integration test
exercises `.load()` and a `tiny.en` model. It's skipped unless
`RUN_WHISPER_TESTS=1`, so CI stays fast.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.asr.whisper_backend import (
    SHORT_CONTEXT_PAD_S,
    SILENT_TAIL_TOLERANCE_S,
    WhisperBackend,
)
from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame


@dataclass
class FakeWord:
    word: str
    start: float
    end: float
    probability: float = 0.95


@dataclass
class FakeSegment:
    words: list[FakeWord]


class FakeWhisperModel:
    """Captures the `transcribe()` call args and returns a canned segment list."""

    def __init__(self, segments: list[FakeSegment]) -> None:
        self._segments = segments
        self.calls: list[dict] = []

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        word_timestamps: bool = True,
        language: str | None = "en",
        beam_size: int = 5,
        temperature=0.0,
        vad_filter: bool = False,
        max_new_tokens: int | None = None,
    ) -> tuple[list[FakeSegment], dict]:
        self.calls.append(
            {
                "audio_shape": audio.shape,
                "audio_dtype": audio.dtype,
                "word_timestamps": word_timestamps,
                "language": language,
                "beam_size": beam_size,
                "temperature": temperature,
                "vad_filter": vad_filter,
                "max_new_tokens": max_new_tokens,
            }
        )
        return self._segments, {"language": language or "en"}


def make_frame(t_start: float, t_end: float, fill: float = 0.0) -> AudioFrame:
    n = max(1, int((t_end - t_start) * SAMPLE_RATE))
    return AudioFrame(
        samples=np.full(n, fill, dtype=np.float32),
        t_start=t_start,
        t_end=t_end,
    )


def test_empty_frame_stream_yields_nothing():
    model = FakeWhisperModel(segments=[])
    backend = WhisperBackend(model)
    events = list(backend.transcribe(iter([])))
    assert events == []
    assert model.calls == []  # we don't even invoke the model on empty input


def test_concatenates_frames_into_single_buffer():
    model = FakeWhisperModel(segments=[])
    backend = WhisperBackend(model)
    frames = [make_frame(0.0, 0.5), make_frame(0.5, 1.0), make_frame(1.0, 1.5)]
    list(backend.transcribe(iter(frames)))
    assert len(model.calls) == 1
    # 1.5 s of audio at 16 kHz = 24000 samples
    assert model.calls[0]["audio_shape"] == (24000,)
    assert model.calls[0]["audio_dtype"] == np.float32
    assert model.calls[0]["word_timestamps"] is True


def test_word_timestamps_are_offset_by_first_frame_start():
    """Whisper produces timestamps relative to the buffer; we offset by the
    first frame's t_start so the events land on the same clock as the input."""
    segments = [
        FakeSegment(
            words=[
                FakeWord(word="amazing", start=0.0, end=0.5),
                FakeWord(word="grace", start=0.6, end=1.0),
            ]
        )
    ]
    model = FakeWhisperModel(segments=segments)
    backend = WhisperBackend(model)
    # First frame starts at t=100s.
    frames = [make_frame(100.0, 101.0)]
    events = list(backend.transcribe(iter(frames)))
    assert len(events) == 1
    words = events[0].words
    assert [w.text for w in words] == ["amazing", "grace"]
    assert words[0].t_start == pytest.approx(100.0)
    assert words[0].t_end == pytest.approx(100.5)
    assert words[1].t_start == pytest.approx(100.6)
    assert words[1].t_end == pytest.approx(101.0)


def test_multiple_segments_emit_separate_events():
    segments = [
        FakeSegment(words=[FakeWord(word="hello", start=0.0, end=0.4)]),
        FakeSegment(words=[FakeWord(word="world", start=0.5, end=0.9)]),
    ]
    backend = WhisperBackend(FakeWhisperModel(segments=segments))
    events = list(backend.transcribe(iter([make_frame(0.0, 1.0)])))
    assert len(events) == 2
    assert events[0].segment_id == 0
    assert events[1].segment_id == 1
    assert events[0].words[0].text == "hello"
    assert events[1].words[0].text == "world"


def test_all_events_are_marked_final_in_batch_mode():
    segments = [FakeSegment(words=[FakeWord(word="hi", start=0.0, end=0.2)])]
    backend = WhisperBackend(FakeWhisperModel(segments=segments))
    events = list(backend.transcribe(iter([make_frame(0.0, 0.5)])))
    assert all(e.is_final for e in events)


def test_empty_words_are_skipped():
    """Whisper sometimes emits an empty word (e.g. for silence). Skip them
    so the event stream is always meaningful."""
    segments = [
        FakeSegment(
            words=[
                FakeWord(word="", start=0.0, end=0.1),
                FakeWord(word="  ", start=0.1, end=0.2),
                FakeWord(word="real", start=0.2, end=0.5),
            ]
        )
    ]
    backend = WhisperBackend(FakeWhisperModel(segments=segments))
    events = list(backend.transcribe(iter([make_frame(0.0, 0.5)])))
    assert len(events) == 1
    assert [w.text for w in events[0].words] == ["real"]


def test_segments_with_only_empty_words_are_dropped():
    segments = [
        FakeSegment(words=[FakeWord(word="", start=0.0, end=0.1)]),
        FakeSegment(words=[FakeWord(word="hi", start=0.2, end=0.4)]),
    ]
    backend = WhisperBackend(FakeWhisperModel(segments=segments))
    events = list(backend.transcribe(iter([make_frame(0.0, 0.5)])))
    assert len(events) == 1
    assert events[0].words[0].text == "hi"


def test_trims_whitespace_from_word_text():
    segments = [
        FakeSegment(
            words=[FakeWord(word=" amazing ", start=0.0, end=0.5)]
        )
    ]
    backend = WhisperBackend(FakeWhisperModel(segments=segments))
    events = list(backend.transcribe(iter([make_frame(0.0, 0.5)])))
    assert events[0].words[0].text == "amazing"


def test_passes_language_and_beam_size_through():
    model = FakeWhisperModel(segments=[])
    backend = WhisperBackend(model, language="fr", beam_size=10, vad_filter=True)
    list(backend.transcribe(iter([make_frame(0.0, 0.5)])))
    assert model.calls[0]["language"] == "fr"
    assert model.calls[0]["beam_size"] == 10
    assert model.calls[0]["vad_filter"] is True


def test_decoding_is_greedy_by_default():
    """Production builds `WhisperBackend(model)` with no beam argument, so the
    default is what ships. Beam 5 shipped by accident and cost on both axes:
    737 ms -> 649 ms and WER 0.357 -> 0.220 going to greedy, because the beam's
    fluency prior pulls a sung lyric toward plausible prose."""
    model = FakeWhisperModel(segments=[])
    list(WhisperBackend(model).transcribe(iter([make_frame(0.0, 0.5)])))
    assert model.calls[0]["beam_size"] == 1


def test_one_decode_attempt_by_default():
    """faster-whisper's default temperature *list* re-decodes a segment up to
    six times when it trips a confidence threshold, which sung audio over
    instrumental backing does routinely. Measured, that is a 4240 ms worst-case
    pass against 648 ms for the same words — and because the wrapper paces the
    next pass off the last one's duration, a spike costs a hole in the
    transcript rather than just its own time."""
    model = FakeWhisperModel(segments=[])
    list(WhisperBackend(model).transcribe(iter([make_frame(0.0, 0.5)])))
    assert model.calls[0]["temperature"] == 0.0


def test_name_includes_model_size_when_loaded():
    """The default constructor uses the explicit `model_name` arg; `.load()`
    builds a name from the size string."""
    model = FakeWhisperModel(segments=[])
    backend = WhisperBackend(model, model_name="custom-name")
    assert backend.name() == "custom-name"


def test_returns_typed_transcript_events():
    segments = [
        FakeSegment(
            words=[FakeWord(word="amazing", start=0.0, end=0.5, probability=0.85)]
        )
    ]
    backend = WhisperBackend(FakeWhisperModel(segments=segments))
    events = list(backend.transcribe(iter([make_frame(0.0, 1.0)])))
    assert isinstance(events[0], TranscriptEvent)
    assert events[0].words[0].confidence == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Optional integration test against a real Whisper model.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("RUN_WHISPER_TESTS") != "1",
    reason="Set RUN_WHISPER_TESTS=1 to run (downloads tiny.en model ~75 MB)",
)
def test_real_model_transcribes_silence_without_crashing() -> None:
    """Smoke test for the production `.load()` path.

    Doesn't assert on transcript content — Whisper's behavior on silence
    isn't deterministic across versions (it sometimes hallucinates
    common phrases). The point of this test is to verify the wire-up:
    `.load()` brings up a real model, `transcribe()` runs end-to-end,
    and the result fits our types.
    """
    backend = WhisperBackend.load("tiny.en")
    silence = AudioFrame(
        samples=np.zeros(SAMPLE_RATE, dtype=np.float32),  # 1 s of silence
        t_start=0.0,
        t_end=1.0,
    )
    events = list(backend.transcribe(iter([silence])))
    # Just verify it didn't crash and returned a list (possibly empty).
    assert isinstance(events, list)
    for ev in events:
        assert ev.is_final
        for w in ev.words:
            assert isinstance(w.text, str)
            assert w.t_start <= w.t_end


def test_a_pass_decodes_at_most_a_few_tokens_per_second():
    """Whisper loops on sung audio; uncapped, one 6 s window took 12.7 s on CPU."""
    from auto_presenter.asr import whisper_backend as wb

    model = FakeWhisperModel(segments=[])
    backend = WhisperBackend(model)
    list(backend.transcribe(iter([make_frame(0.0, 6.0)])))
    list(backend.transcribe(iter([make_frame(0.0, 1.0)])))
    assert model.calls[0]["max_new_tokens"] == 96
    assert model.calls[1]["max_new_tokens"] == wb.MIN_TOKENS


class TestShortContext:
    """`short_context_s`: the window encoded as it is plus a little silence,
    not padded to 30 s. Runs a real tiny.en when it's on this machine."""

    @pytest.fixture(scope="class")
    def backend(self):
        pytest.importorskip("faster_whisper")
        from faster_whisper import WhisperModel

        try:
            model = WhisperModel("tiny.en", device="cpu", compute_type="int8", local_files_only=True)
        except Exception:
            pytest.skip("tiny.en isn't downloaded here")
        return WhisperBackend(model, model_name="whisper-tiny.en")

    @pytest.fixture(scope="class")
    def sung(self):
        import soundfile as sf

        root = Path(__file__).resolve().parents[3]
        audio, _ = sf.read(root / "data/fixtures/perf/service.wav", dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio[30 * 16000 : 36 * 16000]

    def test_words_land_inside_the_window_on_its_clock(self, backend, sung):
        backend.short_context_s = SHORT_CONTEXT_PAD_S
        frame = AudioFrame(samples=sung, t_start=500.0, t_end=506.0)
        words = [w for e in backend.transcribe([frame]) for w in e.words]
        backend.short_context_s = None
        assert words, "the sung window should be heard"
        for w in words:
            assert 500.0 <= w.t_start < 506.0 - SILENT_TAIL_TOLERANCE_S
            assert w.t_end <= 506.0

    def test_it_hears_what_the_padded_pass_hears(self, backend, sung):
        frame = AudioFrame(samples=sung, t_start=0.0, t_end=6.0)
        backend.short_context_s = None
        padded = {w.text.lower().strip(".,!?") for e in backend.transcribe([frame]) for w in e.words}
        backend.short_context_s = SHORT_CONTEXT_PAD_S
        short = {w.text.lower().strip(".,!?") for e in backend.transcribe([frame]) for w in e.words}
        backend.short_context_s = None
        assert len(padded & short) >= 0.6 * len(padded)
