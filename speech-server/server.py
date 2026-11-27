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
):
    """`backend`: a loaded `QwenStreamingBackend`. `phone_head`: the Qwen
    next-line phone model (`next_line.PhoneHead`); with it, a stream that
    starts with `"phones": true` also gets the check's phones: every
    `next_line.TICK_S` of audio, the trailing `WINDOW_S` through Qwen's audio
    tower (`encode`, by default `next_line.qwen_encoder(backend)`) and the
    head, as on the Mac."""
    import asyncio
    import base64
    import json
    import queue
    import threading
    import time

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
        await ws.accept()
        opened = time.monotonic()
        org = claims.get("org", "-")
        log.info("stream open org=%s", org)
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
        finally:
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
        log.info(
            "stream closed org=%s after %.0f s, %.0f s of audio",
            org,
            time.monotonic() - opened,
            t - t0,
        )

    return api
