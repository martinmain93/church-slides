"""`MLXWhisperBackend` against a mocked `mlx_whisper` — no model, no Metal.

The module is imported lazily inside `transcribe`, so these tests inject a
stand-in through `sys.modules` and never touch the real package. That also
means they pass on a machine with no Apple silicon and no install, which is
the point: this backend is optional, but its conversion logic is not allowed
to rot just because CI cannot run it.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from auto_presenter.asr import mlx_backend as mlx_backend_module
from auto_presenter.asr.mlx_backend import MLXWhisperBackend, repo_for
from auto_presenter.audio.types import AudioFrame


def _frame(seconds: float, t_start: float = 0.0) -> AudioFrame:
    n = int(seconds * 16_000)
    return AudioFrame(
        samples=np.zeros(n, dtype=np.float32), t_start=t_start, t_end=t_start + seconds
    )


@pytest.fixture()
def fake_mlx(monkeypatch):
    """Install a stand-in `mlx_whisper` and capture what it was called with."""
    calls: list[dict] = []
    result = {"segments": []}

    def transcribe(audio, **kwargs):
        calls.append({"audio": audio, **kwargs})
        return result

    mod = types.ModuleType("mlx_whisper")
    mod.transcribe = transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", mod)
    return calls, result


class TestRepoResolution:
    def test_maps_a_bare_size_to_the_community_conversion(self):
        assert repo_for("small.en") == "mlx-community/whisper-small.en-mlx"

    def test_passes_a_full_repo_id_through(self):
        assert repo_for("my-org/my-whisper-mlx") == "my-org/my-whisper-mlx"

    def test_an_unknown_size_says_what_to_do_instead(self):
        with pytest.raises(ValueError, match="full HF repo id"):
            repo_for("enormous.en")


class TestTranscribe:
    def test_offsets_timestamps_onto_the_frame_clock(self, fake_mlx):
        """mlx-whisper reports relative to the buffer it was handed. A sliding
        window hands it audio starting at t=60, and the matcher and the ground
        truth both live on the recording's clock — so 0.5s into that buffer has
        to come back as 60.5, not 0.5."""
        _, result = fake_mlx
        result["segments"] = [
            {"words": [
                {"word": " amazing", "start": 0.5, "end": 1.0, "probability": 0.9},
                {"word": " grace", "start": 1.0, "end": 1.6, "probability": 0.8},
            ]}
        ]
        be = MLXWhisperBackend("small.en")
        events = list(be.transcribe(iter([_frame(6.0, t_start=60.0)])))
        assert len(events) == 1
        assert [(w.text, w.t_start, w.t_end) for w in events[0].words] == [
            ("amazing", 60.5, 61.0),
            ("grace", 61.0, 61.6),
        ]
        assert events[0].words[0].confidence == pytest.approx(0.9)
        assert events[0].is_final is True

    def test_asks_for_word_timestamps_and_a_single_decode(self, fake_mlx, monkeypatch):
        """Both are load-bearing. Without word timestamps nothing downstream
        works at all; `temperature=0` stops Whisper's fallback cascade, which
        sung audio trips routinely and which costs a hole in the transcript
        rather than merely a slow pass — see the class docstring."""
        calls, _ = fake_mlx
        # As on a machine that hasn't downloaded the model yet.
        monkeypatch.setattr(mlx_backend_module, "local_snapshot", lambda repo: repo)
        list(MLXWhisperBackend("small.en").transcribe(iter([_frame(6.0)])))
        assert calls[0]["word_timestamps"] is True
        assert calls[0]["temperature"] == 0.0
        assert calls[0]["path_or_hf_repo"] == "mlx-community/whisper-small.en-mlx"

    def test_a_pass_decodes_at_most_a_few_tokens_per_second(self, fake_mlx, monkeypatch):
        """Whisper loops on sung audio ("please, please, ..."); uncapped, one
        such 6 s window took 6 s to decode and starved the ASR queue."""
        calls, _ = fake_mlx
        monkeypatch.setattr(mlx_backend_module, "local_snapshot", lambda repo: repo)
        backend = MLXWhisperBackend("small.en")
        list(backend.transcribe(iter([_frame(6.0)])))
        list(backend.transcribe(iter([_frame(1.0)])))
        assert calls[0]["sample_len"] == 60
        assert calls[1]["sample_len"] == mlx_backend_module.MIN_TOKENS

    def test_no_frames_means_no_call_and_no_events(self, fake_mlx):
        calls, _ = fake_mlx
        assert list(MLXWhisperBackend("small.en").transcribe(iter([]))) == []
        assert calls == []

    def test_a_failed_pass_yields_nothing_rather_than_raising(self, monkeypatch):
        """Same contract as every other backend here: this runs on the ASR
        thread, and one bad window must be 'no words', not an exception."""
        mod = types.ModuleType("mlx_whisper")

        def boom(audio, **kwargs):
            raise RuntimeError("metal device lost")

        mod.transcribe = boom
        monkeypatch.setitem(sys.modules, "mlx_whisper", mod)
        be = MLXWhisperBackend("small.en")
        assert list(be.transcribe(iter([_frame(6.0)]))) == []

    def test_empty_and_whitespace_words_are_dropped(self, fake_mlx):
        _, result = fake_mlx
        result["segments"] = [
            {"words": [
                {"word": "   ", "start": 0.0, "end": 0.1},
                {"word": " real", "start": 0.1, "end": 0.4},
            ]},
            {"words": [{"word": "  ", "start": 1.0, "end": 1.1}]},
        ]
        events = list(MLXWhisperBackend("small.en").transcribe(iter([_frame(6.0)])))
        # The all-blank segment produces no event at all, not an empty one.
        assert len(events) == 1
        assert [w.text for w in events[0].words] == ["real"]

    def test_a_missing_probability_defaults_rather_than_crashing(self, fake_mlx):
        _, result = fake_mlx
        result["segments"] = [{"words": [{"word": " x", "start": 0.0, "end": 0.2}]}]
        events = list(MLXWhisperBackend("small.en").transcribe(iter([_frame(6.0)])))
        assert events[0].words[0].confidence == 1.0

    def test_name_identifies_the_backend_and_size(self):
        assert MLXWhisperBackend("small.en").name() == "mlx-whisper-small.en"

    def test_warm_runs_a_short_silent_pass(self, fake_mlx):
        calls, _ = fake_mlx
        MLXWhisperBackend("tiny.en").warm()
        assert len(calls) == 1
        assert calls[0]["audio"].shape[0] == 16_000


class TestCachedModel:
    def test_a_cached_model_is_read_from_disk_not_the_hub(self, fake_mlx, monkeypatch, tmp_path):
        """Given a repo id, mlx_whisper asks the Hub for updates on its first
        pass even with the model on disk; on a stalled network that held a
        cached model for 90 s and then failed. A local path skips the Hub."""
        calls, _ = fake_mlx
        import huggingface_hub

        seen = {}

        def fake_snapshot(repo_id, local_files_only=False, **_kw):
            seen["local_files_only"] = local_files_only
            return str(tmp_path)

        monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
        list(MLXWhisperBackend("small.en").transcribe(iter([_frame(6.0)])))
        assert seen["local_files_only"] is True
        assert calls[0]["path_or_hf_repo"] == str(tmp_path)

    def test_an_uncached_model_falls_back_to_the_repo_id(self, monkeypatch):
        import huggingface_hub

        def not_cached(repo_id, local_files_only=False, **_kw):
            raise FileNotFoundError(repo_id)

        monkeypatch.setattr(huggingface_hub, "snapshot_download", not_cached)
        assert mlx_backend_module.local_snapshot("mlx-community/x") == "mlx-community/x"
