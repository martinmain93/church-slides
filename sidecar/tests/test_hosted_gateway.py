"""The organization's AI allowance stands in for a Groq key (api_keys.endpoint)."""

import json
import time

import pytest

from auto_presenter import api_keys


@pytest.fixture
def keys(tmp_path, monkeypatch):
    monkeypatch.setenv(api_keys.KEYS_FILE_ENV, str(tmp_path / "keys.json"))
    monkeypatch.delenv("AUTO_PRESENTER_GROQ_API_KEY", raising=False)
    hosted = tmp_path / "hosted_ai.json"
    monkeypatch.setenv(api_keys.HOSTED_AI_FILE_ENV, str(hosted))
    return hosted


def write(path, expires_at):
    path.write_text(json.dumps({"base_url": "https://api.test/ai/v1/", "key": "csa_k", "expires_at": expires_at,
                                "org_id": "o", "org_name": "O"}))


def test_nothing_without_a_key_or_an_allowance(keys):
    assert api_keys.endpoint("groq", "https://api.groq.com/openai/v1") is None


def test_the_allowance_when_no_key_of_your_own(keys):
    write(keys, time.time() * 1000 + 60_000)
    assert api_keys.endpoint("groq", "https://api.groq.com/openai/v1") == ("https://api.test/ai/v1", "csa_k")


def test_your_own_key_wins(keys):
    write(keys, time.time() * 1000 + 60_000)
    api_keys.save_key("groq", "gsk_mine")
    assert api_keys.endpoint("groq", "https://api.groq.com/openai/v1") == ("https://api.groq.com/openai/v1", "gsk_mine")


def test_an_expired_allowance_key_is_ignored(keys):
    write(keys, time.time() * 1000 - 1)
    assert api_keys.endpoint("groq", "https://api.groq.com/openai/v1") is None


def test_only_providers_the_gateway_stands_in_for(keys):
    write(keys, time.time() * 1000 + 60_000)
    assert api_keys.endpoint("gemini", "https://generativelanguage.googleapis.com/v1beta/openai") is None
