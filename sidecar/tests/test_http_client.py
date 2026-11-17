"""The shared urllib plumbing three call sites used to hand-roll
independently — mainly pinning the header behavior each of them
rediscovered on their own (`User-Agent`, `Authorization`)."""

from __future__ import annotations

import json
from io import BytesIO

from auto_presenter import http_client


class _Resp(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch, body: bytes = b"{}"):
    captured = {}

    def fake_urlopen(req, timeout=None, **_kwargs):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        captured["timeout"] = timeout
        captured["context"] = _kwargs.get("context")
        return _Resp(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


class TestHeaders:
    def test_every_open_helper_sends_a_real_user_agent(self, monkeypatch):
        """Cloudflare blocks urllib's default UA in front of Groq's API —
        found once, must not need finding again for the next hosted call
        site."""
        for opener, args in [
            (http_client.open_get, ("https://x/get",)),
            (http_client.open_json_post, ("https://x/post", {"a": 1})),
            (
                http_client.open_multipart_post,
                ("https://x/upload", {"a": "1"}),
            ),
        ]:
            captured = _capture(monkeypatch)
            kwargs = (
                dict(file_field="file", filename="a.wav", file_content_type="audio/wav", file_bytes=b"x")
                if opener is http_client.open_multipart_post
                else {}
            )
            opener(*args, **kwargs)
            assert "python-urllib" not in captured["headers"]["User-agent"].lower()

    def test_api_key_becomes_a_bearer_header(self, monkeypatch):
        captured = _capture(monkeypatch)
        http_client.open_json_post("https://x/post", {}, api_key="sk-123")
        assert captured["headers"]["Authorization"] == "Bearer sk-123"

    def test_no_api_key_means_no_authorization_header(self, monkeypatch):
        captured = _capture(monkeypatch)
        http_client.open_get("https://x/get")
        assert "Authorization" not in captured["headers"]


class TestJsonPost:
    def test_sends_the_payload_as_a_json_body(self, monkeypatch):
        captured = _capture(monkeypatch)
        http_client.open_json_post("https://x/post", {"hello": "world"})
        assert json.loads(captured["data"]) == {"hello": "world"}
        assert captured["headers"]["Content-type"] == "application/json"

    def test_read_json_parses_the_response(self, monkeypatch):
        _capture(monkeypatch, body=json.dumps({"ok": True}).encode())
        with http_client.open_json_post("https://x/post", {}) as resp:
            assert http_client.read_json(resp) == {"ok": True}


class TestMultipartPost:
    def test_body_contains_fields_and_the_file(self, monkeypatch):
        captured = _capture(monkeypatch)
        http_client.open_multipart_post(
            "https://x/upload",
            {"model": "m"},
            file_field="file", filename="a.wav",
            file_content_type="audio/wav", file_bytes=b"RIFF...",
        )
        assert captured["method"] == "POST"
        assert b'name="model"' in captured["data"]
        assert b"RIFF..." in captured["data"]
        assert captured["headers"]["Content-type"].startswith("multipart/form-data")


class TestTLS:
    def test_https_verifies_against_the_bundled_ca_file(self, monkeypatch):
        """A frozen sidecar can't rely on the build machine's CA path, so every
        request carries a context loaded from certifi's bundle."""
        import ssl

        captured = _capture(monkeypatch)
        http_client.open_get("https://x/get")
        ctx = captured["context"]
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.cert_store_stats()["x509_ca"] > 50
