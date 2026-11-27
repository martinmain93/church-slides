"""The speech server's websocket, as a plain FastAPI app, so it runs anywhere:
on Modal's GPU (`app.py`) or on a Mac's (`serve_local.py`). The protocol is
in README.md; the app's side is `sidecar/auto_presenter/asr/remote_backend.py`.

Nothing here logs or keeps words or audio: only connections, timings and
failures.
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tokens

log = logging.getLogger("speech_server")

#: Failed steps in a row before the process gives up and exits, so Modal
#: replaces the container: a kernel that won't load fails every step after
#: (2026-11-26), and a server that answers without words keeps the app off
#: its fallback.
FAILED_STEPS_TO_EXIT = 8


class StepWatch(logging.Handler):
    """Counts "qwen step failed" from the engine; a step with words resets it."""

    def __init__(self, exit=os._exit) -> None:
        super().__init__()
        self.failed = 0
        self._exit = exit

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith("qwen step failed"):
            self.failed += 1
            if self.failed >= FAILED_STEPS_TO_EXIT:
                print(
                    f"{self.failed} steps failed in a row; exiting so the container is replaced",
                    flush=True,
                )
                self._exit(3)

    def ok(self) -> None:
        self.failed = 0


#: How often a stream's totals go to the Church Slides server, which answers
#: whether it may go on (docs/PLANS_AND_USAGE.md in the server repo).
REPORT_EVERY_S = 60.0
#: After a "stop", that organization's new streams are refused this long
#: (as long as a token lasts).
REFUSE_FOR_S = 15 * 60.0


class UsageReporter:
    """Posts a stream's cumulative totals, signed with the shared secret.
    `post(body) -> bool | None`: whether the stream may go on, or None when
    the server couldn't be reached (the stream goes on: the service never
    stops, and the next report carries the totals)."""

    def __init__(self, secret: str, url: str | None, post=None) -> None:
        self.secret = secret
        self.url = url
        self._post = post or self._http
        self._refused: dict[str, float] = {}

    def _http(self, body: bytes) -> bool | None:
        import json
        import urllib.request

        if not self.url:
            return None
        req = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Speech-Signature": tokens.sign(self.secret, body.decode()),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return bool(json.loads(r.read()).get("continue", True))
        except Exception as exc:
            log.warning("usage report failed: %s", exc)
            return None

    def report(self, totals: dict) -> bool | None:
        import json

        ok = self._post(json.dumps(totals, separators=(",", ":")).encode())
        if ok is False:
            import time

            self._refused[totals["org"]] = time.monotonic() + REFUSE_FOR_S
        return ok

    def refused(self, org: str) -> bool:
        import time

        until = self._refused.get(org)
        return until is not None and until > time.monotonic()


def make_api(
    backend,
    *,
    secret: str,
    gpu: str,
    load_s: float,
    build: str = "dev",
    phone_head=None,
    encode=None,
    watch: StepWatch | None = None,
    reporter: UsageReporter | None = None,
    report_every_s: float = REPORT_EVERY_S,
):
    """`backend`: a loaded `QwenStreamingBackend`. `phone_head`: the Qwen
    next-line phone model (`next_line.PhoneHead`); with it, a stream that
    starts with `"phones": true` also gets the check's phones: every
    `next_line.TICK_S` of audio, the trailing `WINDOW_S` through Qwen's audio
    tower (`encode`, by default `next_line.qwen_encoder(backend)`) and the
    head, as on the Mac. `reporter`: where each stream's usage goes (none:
    not reported)."""
    import asyncio
    import base64
    import json
    import queue
    import threading
    import time
    import uuid

    import numpy as np
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect

    from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame
    from auto_presenter.follower import next_line

    if phone_head is not None and encode is None:
        encode = next_line.qwen_encoder(backend)
    if watch is None:
        watch = StepWatch()
    logging.getLogger("auto_presenter.asr.qwen_backend").addHandler(watch)

    api = FastAPI()
    ready = {"type": "ready", "load_s": round(load_s, 1), "gpu": gpu, "build": build}

    @api.get("/health")
    def health():
        return {"ok": True, "load_s": round(load_s, 1), "gpu": gpu, "build": build}

    # (no `from __future__ import annotations` in this file: FastAPI must see
    # `WebSocket` itself, or it reads `ws` as a missing query parameter)
    @api.websocket("/stream")
    async def stream(ws: WebSocket):
        claims = tokens.verify(secret, ws.query_params.get("token", ""))
        if claims is None:
            await ws.close(code=4401)
            return
        org = claims.get("org", "-")
        if reporter is not None and reporter.refused(org):
            await ws.close(code=4402)
            return
        await ws.accept()
        opened = time.monotonic()
        stream_id = uuid.uuid4().hex
        #: GPU time this stream's steps and checks took (the ledger's gpu_seconds)
        gpu_s = [0.0]
        log.info("stream open org=%s stream=%s", org, stream_id)
        await ws.send_text(json.dumps(ready))
        loop = asyncio.get_running_loop()
        frames: queue.Queue = queue.Queue()
        outbox: asyncio.Queue = asyncio.Queue()

        def run() -> None:
            last_pass = [0.0]

            class Timer:
                def __enter__(self):
                    self.t = time.monotonic()

                def __exit__(self, *a):
                    last_pass[0] = time.monotonic() - self.t
                    gpu_s[0] += last_pass[0]

            def gen():
                while (f := frames.get()) is not None:
                    yield f

            streamer = backend.streamer(pass_timer=Timer)
            try:
                for ev in streamer.transcribe(gen()):
                    watch.ok()
                    msg = {
                        "type": "words",
                        "final": ev.is_final,
                        "segment_id": ev.segment_id,
                        "ends_segment": ev.ends_segment,
                        "pass_s": round(last_pass[0], 3),
                        "words": [[w.text, w.t_start, w.t_end, w.confidence] for w in ev.words],
                    }
                    loop.call_soon_threadsafe(outbox.put_nowait, json.dumps(msg))
            except Exception:
                log.exception("stream failed org=%s", org)
            finally:
                loop.call_soon_threadsafe(outbox.put_nowait, None)

        async def sender() -> None:
            while (m := await outbox.get()) is not None:
                await ws.send_text(m)

        phones = None

        def on_logits(t_end: float, logits) -> None:
            ms = phones.ms[-1] if phones is not None and phones.ms else None
            if ms is not None:
                gpu_s[0] += ms / 1000.0
            msg = {
                "type": "phones",
                "t_end": round(t_end, 3),
                "shape": list(logits.shape),
                "logits": base64.b64encode(np.asarray(logits).astype("<f2").tobytes()).decode(),
                "ms": None if ms is None else round(ms, 1),
            }
            loop.call_soon_threadsafe(outbox.put_nowait, json.dumps(msg))

        worker = threading.Thread(target=run, daemon=True)
        send_task = asyncio.create_task(sender())
        t = t0 = 0.0
        started = False

        def totals(final: bool) -> dict:
            return {
                "stream": stream_id,
                "org": org,
                "user": claims.get("user"),
                "audio_seconds": round(t - t0, 1),
                "wall_seconds": round(time.monotonic() - opened, 1),
                "gpu_seconds": round(gpu_s[0], 2),
                "gpu": gpu,
                "build": build,
                "final": final,
            }

        async def lease() -> None:
            """Report every `report_every_s`; a "stop" closes the stream (the
            app's own engine takes over)."""
            while True:
                await asyncio.sleep(report_every_s)
                if await asyncio.to_thread(reporter.report, totals(False)) is False:
                    log.info("stream stopped by the server org=%s stream=%s", org, stream_id)
                    await ws.close(code=4402)
                    return

        lease_task = asyncio.create_task(lease()) if reporter is not None else None
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text"):
                    m = json.loads(msg["text"])
                    if m.get("type") == "start":
                        t = t0 = float(m.get("t0", 0.0))
                        if m.get("phones") and phone_head is not None and phones is None:
                            phones = next_line.PhoneListener(encode, phone_head, on_logits)
                        if not started:
                            worker.start()
                            started = True
                    elif m.get("type") == "end":
                        break
                elif msg.get("bytes") and started:
                    pcm = np.frombuffer(msg["bytes"], dtype="<i2").astype(np.float32) / 32768.0
                    n = len(pcm) / SAMPLE_RATE
                    frames.put(AudioFrame(samples=pcm, t_start=t, t_end=t + n))
                    t += n
                    if phones is not None:
                        phones.feed(pcm, t)
        except WebSocketDisconnect:
            pass
        except RuntimeError:
            pass  # closed by the lease
        finally:
            if lease_task is not None:
                lease_task.cancel()
            frames.put(None)
            if phones is not None:
                phones.stop()
        if started:
            await asyncio.to_thread(worker.join)
        try:
            await send_task
            await ws.close()
        except Exception:
            pass
        if reporter is not None:
            await asyncio.to_thread(reporter.report, totals(True))
        log.info(
            "stream closed org=%s stream=%s after %.0f s, %.0f s of audio, %.1f GPU s",
            org,
            stream_id,
            time.monotonic() - opened,
            t - t0,
            gpu_s[0],
        )

    return api
