"""The speech server's websocket with a fake engine: tokens, words, phones,
and the failed-step watchdog.

    cd sidecar && uv run --no-sync --with fastapi --with httpx \
        python -m pytest ../speech-server/tests -q
"""

import base64
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
import tokens

from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.follower import next_line

SECRET = "test-secret"


class FakeBackend:
    """One final word per audio frame, at that frame's time."""

    def streamer(self, pass_timer=None):
        return self

    def transcribe(self, frames):
        for i, f in enumerate(frames):
            yield TranscriptEvent(
                words=[Word(text="holy", t_start=f.t_start, t_end=f.t_end, confidence=0.9)],
                is_final=True,
                segment_id=i,
            )


def client(**kw):
    api = server.make_api(FakeBackend(), secret=SECRET, gpu="fake", load_s=1.0, build="abc", **kw)
    return TestClient(api)


def pcm(seconds=0.1):
    return np.zeros(int(16000 * seconds), "<i2").tobytes()


def test_tokens_round_trip_and_refuse():
    t = tokens.mint(SECRET, 60, "org_1")
    assert tokens.verify(SECRET, t)["org"] == "org_1"
    assert tokens.verify("other", t) is None
    assert tokens.verify(SECRET, tokens.mint(SECRET, -1)) is None
    assert tokens.verify(SECRET, "nonsense") is None
    payload, sig = t.split(".")
    assert tokens.verify(SECRET, payload + "x." + sig) is None


def test_health_reports_the_build():
    r = client().get("/health").json()
    assert r == {"ok": True, "load_s": 1.0, "gpu": "fake", "build": "abc"}


@pytest.mark.parametrize(
    "token", ["", "bad.token", tokens.mint("other", 60), tokens.mint(SECRET, -5)]
)
def test_a_bad_token_is_closed(token):
    with pytest.raises(WebSocketDisconnect) as e:
        with client().websocket_connect(f"/stream?token={token}") as ws:
            ws.receive_text()
    assert e.value.code == 4401  # closed before accepting: a real client sees HTTP 403


def test_words_come_back_at_the_room_clock():
    with client().websocket_connect(f"/stream?token={tokens.mint(SECRET, 60)}") as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_text(json.dumps({"type": "start", "t0": 5.0}))
        for _ in range(3):
            ws.send_bytes(pcm())
        msgs = [json.loads(ws.receive_text()) for _ in range(3)]
        ws.send_text(json.dumps({"type": "end"}))
    assert [m["type"] for m in msgs] == ["words"] * 3
    assert msgs[0]["words"][0][1] == pytest.approx(5.0)
    assert msgs[2]["words"][0][2] == pytest.approx(5.3)


def test_phones_only_when_asked():
    head = lambda feats: np.ones((3, 4), np.float32)  # noqa: E731
    encode = lambda samples: np.zeros(8, np.float32)  # noqa: E731
    c = client(phone_head=head, encode=encode)
    n = int(next_line.WINDOW_S / 0.1) + 6
    for asked in (True, False):
        with c.websocket_connect(f"/stream?token={tokens.mint(SECRET, 60)}") as ws:
            ws.receive_text()
            ws.send_text(json.dumps({"type": "start", "t0": 0.0, "phones": asked}))
            for _ in range(n):
                ws.send_bytes(pcm())
            seen = [json.loads(ws.receive_text()) for _ in range(n)]
            ws.send_text(json.dumps({"type": "end"}))
            while True:  # the rest, until the server closes
                try:
                    seen.append(json.loads(ws.receive_text()))
                except WebSocketDisconnect:
                    break
        phones = [m for m in seen if m["type"] == "phones"]
        if asked:
            assert phones, "no phones"
            got = np.frombuffer(base64.b64decode(phones[0]["logits"]), "<f2").reshape(
                phones[0]["shape"]
            )
            assert np.array_equal(got, np.ones((3, 4)))
        else:
            assert not phones


def test_watchdog_exits_after_failures_in_a_row():
    exits = []
    w = server.StepWatch(exit=exits.append)
    lg = logging.getLogger("test.watch")
    lg.addHandler(w)
    for _ in range(server.FAILED_STEPS_TO_EXIT - 1):
        lg.warning("qwen step failed: boom")
    w.ok()  # a step with words in between
    for _ in range(server.FAILED_STEPS_TO_EXIT - 1):
        lg.warning("qwen step failed: boom")
    assert exits == []
    lg.warning("qwen step failed: boom")
    assert exits == [3]


class Reports:
    """A stand-in for the Church Slides server's usage endpoint."""

    def __init__(self, answer=True):
        self.answer = answer
        self.got: list[dict] = []

    def __call__(self, body: bytes):
        self.got.append(json.loads(body))
        return self.answer


def test_usage_is_reported_when_a_stream_closes():
    got = Reports()
    rep = server.UsageReporter(SECRET, None, post=got)
    c = client(reporter=rep, report_every_s=3600)
    with c.websocket_connect(f"/stream?token={tokens.mint(SECRET, 60, 'org_1')}") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "start", "t0": 0.0}))
        for _ in range(5):
            ws.send_bytes(pcm())
        for _ in range(5):
            ws.receive_text()
        ws.send_text(json.dumps({"type": "end"}))
    assert len(got.got) == 1
    final = got.got[0]
    assert final["org"] == "org_1" and final["final"] is True
    assert final["audio_seconds"] == pytest.approx(0.5)
    assert final["gpu"] == "fake" and final["build"] == "abc"


def test_a_stop_closes_the_stream_and_refuses_the_org_for_a_while():
    got = Reports(answer=False)
    rep = server.UsageReporter(SECRET, None, post=got)
    c = client(reporter=rep, report_every_s=0.05)
    token = tokens.mint(SECRET, 60, "org_2")
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect(f"/stream?token={token}") as ws:
            ws.receive_text()
            ws.send_text(json.dumps({"type": "start", "t0": 0.0}))
            while True:
                ws.send_bytes(pcm())
                ws.receive_text()
    assert e.value.code == 4402
    assert rep.refused("org_2") and not rep.refused("org_3")
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect(f"/stream?token={token}") as ws:
            ws.receive_text()
    assert e.value.code == 4402


def test_an_unreachable_server_doesnt_stop_the_stream():
    rep = server.UsageReporter(SECRET, None, post=lambda body: None)
    c = client(reporter=rep, report_every_s=0.01)
    with c.websocket_connect(f"/stream?token={tokens.mint(SECRET, 60, 'org_4')}") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "start", "t0": 0.0}))
        for _ in range(20):
            ws.send_bytes(pcm())
            assert json.loads(ws.receive_text())["type"] == "words"
        ws.send_text(json.dumps({"type": "end"}))
    assert not rep.refused("org_4")


def test_reports_are_signed_as_the_server_checks():
    sent = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b'{"continue": true}'

    def fake_urlopen(req, timeout):
        sent["sig"] = req.headers["X-speech-signature"]
        sent["body"] = req.data
        return Resp()

    import urllib.request

    real = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        ok = server.UsageReporter(SECRET, "https://api.test/internal/speech/usage").report(
            {"stream": "s", "org": "o"}
        )
    finally:
        urllib.request.urlopen = real
    assert ok is True
    assert sent["sig"] == tokens.sign(SECRET, sent["body"].decode())


def test_a_wake_up_with_no_audio_isnt_reported():
    got = Reports()
    c = client(reporter=server.UsageReporter(SECRET, None, post=got), report_every_s=3600)
    with c.websocket_connect(f"/stream?token={tokens.mint(SECRET, 60, 'org_5')}") as ws:
        ws.receive_text()
    assert got.got == []
