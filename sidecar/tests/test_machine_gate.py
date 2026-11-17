"""What the machine check's verdict turns on and off in the engine.

Off Apple silicon, live following's CPU path runs only on a computer whose
check passed (and whose scorer model is here); a computer that failed gets
no live AI at all (2026-10-15: below the accuracy bar it isn't offered)."""

from __future__ import annotations

import json

import pytest

from auto_presenter import server_follower as sf
from auto_presenter.asr import machine_check as mc
from auto_presenter.protocol import ParticleFilterTunables
from auto_presenter.server_state import _state


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(_state, "library_db_path", tmp_path / "library.db")
    monkeypatch.setattr(_state, "tunables", ParticleFilterTunables())
    monkeypatch.setattr("auto_presenter.follower.scorer_models.is_downloaded", lambda *a: True)
    return tmp_path


def _save(verdict: str, backend: str = "whisper") -> None:
    mc.save(mc.Assessment(verdict, backend, "why", 0.7), sf.machine_verdict_path())


def test_a_passed_check_turns_the_cpu_path_on(app_dir):
    assert not sf.cpu_path_enabled()
    _save("full")
    assert sf.cpu_path_enabled()


def test_a_failed_check_leaves_it_off(app_dir):
    _save("insufficient")
    assert not sf.cpu_path_enabled()


def test_without_its_scorer_model_the_path_stays_off(app_dir, monkeypatch):
    _save("full")
    monkeypatch.setattr("auto_presenter.follower.scorer_models.is_downloaded", lambda *a: False)
    assert not sf.cpu_path_enabled()


def test_a_stale_verdict_counts_for_nothing(app_dir):
    _save("full")
    path = sf.machine_verdict_path()
    data = json.loads(path.read_text())
    data["check_id"] = "an older check"
    path.write_text(json.dumps(data))
    assert sf.saved_verdict() is None
    assert not sf.cpu_path_enabled()


def test_an_unknown_verdict_is_not_kept(app_dir):
    _save("unknown")
    assert not sf.machine_verdict_path().exists()


def test_the_tunable_still_forces_it_for_development(app_dir):
    _state.tunables = ParticleFilterTunables(score_follower_cpu=True)
    assert sf.cpu_path_enabled()


@pytest.mark.parametrize(
    ("backend", "mlx", "verdict", "allowed"),
    [
        ("whisper", False, "full", True),
        ("whisper", False, "insufficient", False),
        ("whisper", False, "assist", False),  # Assist only: not Auto
        ("whisper", False, None, False),  # never checked: not yet
        ("whisper", True, None, True),  # Apple silicon: unchanged
        ("groq", False, None, True),  # hosted speech: unchanged
        ("mlx", True, None, True),
    ],
)
def test_who_may_switch_live_ai_on(app_dir, monkeypatch, backend, mlx, verdict, allowed):
    from auto_presenter import server

    monkeypatch.setattr(server.MLXWhisperBackend, "is_available", staticmethod(lambda: mlx))
    monkeypatch.setattr(_state, "session_asr_backend", backend)
    monkeypatch.setattr(_state, "session_asr_model", "small.en")
    if verdict:
        _save(verdict)
    assert server.live_ai_allowed() is allowed


def test_the_cpu_path_runs_only_the_model_it_was_judged_with(app_dir, monkeypatch):
    from auto_presenter import server

    monkeypatch.setattr(server.MLXWhisperBackend, "is_available", staticmethod(lambda: False))
    monkeypatch.setattr(_state, "session_asr_backend", "whisper")
    _save("full")
    monkeypatch.setattr(_state, "session_asr_model", "small.en")
    assert server.live_ai_allowed()
    monkeypatch.setattr(_state, "session_asr_model", "tiny.en")
    assert not server.live_ai_allowed()


@pytest.mark.asyncio
async def test_checks_asked_at_once_run_once(app_dir, monkeypatch):
    """The home card, the pill and Settings each ask: they share one check,
    since two timed at once slow each other into a wrong verdict."""
    import asyncio

    from auto_presenter import server

    runs = []

    def assess(_dir):
        runs.append(1)
        import time as _t

        _t.sleep(0.05)
        return mc.Assessment("full", "whisper", "keeps up", 0.7)

    replies = []

    async def capture(_ws, reply):
        replies.append(reply)

    monkeypatch.setattr(mc, "assess", assess)
    monkeypatch.setattr(server, "_send", capture)
    from auto_presenter.protocol import AssessMachineRequest

    await asyncio.gather(*[
        server._handle_assess_machine(None, AssessMachineRequest(id=i)) for i in range(3)
    ])
    assert len(runs) == 1
    assert [r.verdict for r in replies] == ["full"] * 3


@pytest.mark.parametrize(
    ("verdict", "mode", "allowed"),
    [
        ("assist", "assist", True),
        ("assist", "active", False),
        ("assist", "shadow", False),
        ("full", "assist", True),
        ("full", "active", True),
        ("insufficient", "assist", False),
    ],
)
def test_the_assist_tier_allows_assist_alone(app_dir, monkeypatch, verdict, mode, allowed):
    from auto_presenter import server

    monkeypatch.setattr(server.MLXWhisperBackend, "is_available", staticmethod(lambda: False))
    monkeypatch.setattr(_state, "session_asr_backend", "whisper")
    monkeypatch.setattr(_state, "session_asr_model", "small.en")
    _save(verdict)
    assert server.live_ai_allowed(mode) is allowed


@pytest.mark.parametrize(
    ("verdict", "requested", "starts"),
    [
        ("assist", "active", "assist"),  # an operator who last chose Auto
        ("assist", "assist", "assist"),
        ("assist", "off", "off"),
        ("insufficient", "assist", "off"),
        ("full", "active", "active"),
    ],
)
def test_a_session_starts_in_a_mode_this_computer_allows(
    app_dir, monkeypatch, verdict, requested, starts
):
    from auto_presenter import server

    monkeypatch.setattr(server.MLXWhisperBackend, "is_available", staticmethod(lambda: False))
    monkeypatch.setattr(_state, "session_asr_backend", "whisper")
    monkeypatch.setattr(_state, "session_asr_model", "small.en")
    _save(verdict)
    assert server._allowed_start_mode(requested) == starts


def test_the_assist_tier_runs_no_follower_and_no_cpu_path(app_dir):
    _save("assist")
    assert sf.assist_tier()
    assert not sf.cpu_path_enabled()
    _save("full")
    assert not sf.assist_tier()


def test_assist_on_a_slower_computer_has_no_next_pulse_and_drops_late_moves(app_dir, monkeypatch):
    from auto_presenter import server_assist

    _save("assist")
    assert server_assist._config().next_tier is False
    monkeypatch.setattr(_state, "pipeline_lag_s", 3.0)
    assert server_assist._stale()
    monkeypatch.setattr(_state, "pipeline_lag_s", 1.0)
    assert not server_assist._stale()
    _save("full")
    monkeypatch.setattr(_state, "pipeline_lag_s", 3.0)
    assert server_assist._config().next_tier is True
    assert not server_assist._stale()  # a fast computer's lag is the behind-warning's job
