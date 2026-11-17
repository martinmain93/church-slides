"""API key storage — plaintext JSON in the app data directory, env var wins.

`tmp_path` + monkeypatching `app_data_dir` keeps this off the real app data
directory; nothing here should touch `~/Library/Application Support/...`.
"""

from __future__ import annotations

import sys

import pytest

from auto_presenter import api_keys


@pytest.fixture(autouse=True)
def _isolated_app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(api_keys, "app_data_dir", lambda: tmp_path)
    monkeypatch.delenv("AUTO_PRESENTER_GROQ_API_KEY", raising=False)
    monkeypatch.delenv("AUTO_PRESENTER_GEMINI_API_KEY", raising=False)


def test_get_key_is_none_when_nothing_is_saved():
    assert api_keys.get_key("groq") is None


def test_save_then_get_round_trips():
    api_keys.save_key("groq", "gk_abc123")
    assert api_keys.get_key("groq") == "gk_abc123"


def test_saving_an_empty_string_clears_the_key():
    api_keys.save_key("groq", "gk_abc123")
    api_keys.save_key("groq", "")
    assert api_keys.get_key("groq") is None


def test_keys_for_different_providers_dont_collide():
    api_keys.save_key("groq", "gk_1")
    api_keys.save_key("gemini", "gm_1")
    assert api_keys.get_key("groq") == "gk_1"
    assert api_keys.get_key("gemini") == "gm_1"


def test_env_var_wins_over_a_saved_key(monkeypatch):
    api_keys.save_key("groq", "saved-key")
    monkeypatch.setenv("AUTO_PRESENTER_GROQ_API_KEY", "env-key")
    assert api_keys.get_key("groq") == "env-key"


def test_a_corrupt_file_is_treated_as_no_keys_saved(tmp_path):
    (tmp_path / "instruction_api_keys.json").write_text("{not valid json")
    assert api_keys.load_keys() == {}
    assert api_keys.get_key("groq") is None


# POSIX permission bits. Windows has no group/other bits to clear: the file is
# private because it lives in the user's own profile, whose ACL already says so.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="no POSIX file modes on Windows")


@posix_only
def test_the_key_file_is_not_world_readable(tmp_path, monkeypatch):
    """Account credentials in plaintext. The app data dir is already per-user,
    so this is defence in depth — but it is two lines and narrows a shared or
    backed-up machine."""
    import stat

    monkeypatch.setattr("auto_presenter.api_keys.app_data_dir", lambda: tmp_path)
    api_keys.save_key("groq", "gk_secret")
    mode = stat.S_IMODE((tmp_path / "instruction_api_keys.json").stat().st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, oct(mode)


@posix_only
def test_permissions_are_tightened_on_a_file_that_already_existed(tmp_path, monkeypatch):
    """A store written by an earlier version keeps its umask-default mode
    until something rewrites it — so the chmod has to apply on update, not
    only on create."""
    import stat

    monkeypatch.setattr("auto_presenter.api_keys.app_data_dir", lambda: tmp_path)
    path = tmp_path / "instruction_api_keys.json"
    path.write_text("{}")
    path.chmod(0o644)
    api_keys.save_key("groq", "gk_secret")
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, oct(mode)
