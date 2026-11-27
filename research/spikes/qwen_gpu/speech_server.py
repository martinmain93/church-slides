"""The speech server's websocket (protocol in `modal_app.py`), as a plain
FastAPI app so it runs anywhere: on Modal's GPU, or locally on a Mac's
(`python serve_local.py`) to check the protocol end to end.
"""




#: Failed steps in a row before the container gives up and exits, so Modal
#: replaces it: a kernel that won't load fails every step after (2026-11-26),
#: and a server that answers without words keeps the app off its fallback.
FAILED_STEPS_TO_EXIT = 8


def _watch_steps() -> None:
    import logging
    import os

    class Watch(logging.Handler):
        failed = 0

        def emit(self, record: logging.LogRecord) -> None:
            if record.getMessage().startswith("qwen step failed"):
                Watch.failed += 1
                if Watch.failed >= FAILED_STEPS_TO_EXIT:
                    n = Watch.failed
                    print(f"{n} steps failed in a row; exiting so the container is replaced",
                          flush=True)
                    os._exit(3)

    logging.getLogger("auto_presenter.asr.qwen_backend").addHandler(Watch())
    _WATCH.append(Watch)


_WATCH: list = []


def step_ok() -> None:
    """A step that produced words: the failures weren't in a row."""
    for w in _WATCH:
        w.failed = 0


def make_api(backend, *, secret: str, gpu: str, load_s: float, phone_head=None):
    """`phone_head`: the Qwen next-line phone model (`next_line.PhoneHead`).
    With it, a stream that starts with `"phones": true` also gets the
    next-line check's phones: every `next_line.TICK_S` of audio, Qwen's audio
    tower and the head read the trailing `WINDOW_S`, as on the Mac."""
    import asyncio
    import base64
    import hashlib
    import hmac
    import json
    import queue
    import threading
    import time

    import numpy as np
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect

    from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame
    from auto_presenter.follower import next_line

    encode = next_line.qwen_encoder(backend) if phone_head is not None else None

    api = FastAPI()
    secret_b = secret.encode()
    _watch_steps()

    def valid(token: str) -> bool:
        try:
            payload, sig = token.split(".")
            want = base64.urlsafe_b64encode(
                hmac.new(secret_b, payload.encode(), hashlib.sha256).digest()
            ).rstrip(b"=").decode()
            if not hmac.compare_digest(sig, want):
                return False
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            return float(claims["exp"]) > time.time()
        except Exception:
            return False

    @api.get("/health")
    def health():
        return {"ok": True, "load_s": round(load_s, 1), "gpu": gpu}

    @api.websocket("/stream")
    async def stream(ws: WebSocket):
        if not valid(ws.query_params.get("token", "")):
            await ws.close(code=4401)
            return
        await ws.accept()
        await ws.send_text(json.dumps({"type": "ready", "load_s": round(load_s, 1), "gpu": gpu}))
        loop = asyncio.get_running_loop()
        frames: queue.Queue = queue.Queue()
        outbox: asyncio.Queue = asyncio.Queue()

        def run() -> None:
            last_pass = [0.0]

            def timer():
                class T:
                    def __enter__(s):
                        s.t = time.monotonic()

                    def __exit__(s, *a):
                        last_pass[0] = time.monotonic() - s.t

                return T()

            def gen():
                while (f := frames.get()) is not None:
                    yield f

            streamer = backend.streamer(pass_timer=timer)
            try:
                for ev in streamer.transcribe(gen()):
                    step_ok()
                    msg = {
                        "type": "words", "final": ev.is_final, "segment_id": ev.segment_id,
                        "ends_segment": ev.ends_segment, "pass_s": round(last_pass[0], 3),
                        "words": [[w.text, w.t_start, w.t_end, w.confidence] for w in ev.words],
                    }
                    loop.call_soon_threadsafe(outbox.put_nowait, json.dumps(msg))
            finally:
                loop.call_soon_threadsafe(outbox.put_nowait, None)

        async def sender() -> None:
            while (m := await outbox.get()) is not None:
                await ws.send_text(m)

        phones = None

        def on_logits(t_end: float, logits: np.ndarray) -> None:
            ms = phones.ms[-1] if phones is not None and phones.ms else None
            msg = {
                "type": "phones", "t_end": round(t_end, 3), "shape": list(logits.shape),
                "logits": base64.b64encode(logits.astype("<f2").tobytes()).decode(),
                "ms": None if ms is None else round(ms, 1),
            }
            loop.call_soon_threadsafe(outbox.put_nowait, json.dumps(msg))

        worker = threading.Thread(target=run, daemon=True)
        send_task = asyncio.create_task(sender())
        t = 0.0
        started = False
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text"):
                    m = json.loads(msg["text"])
                    if m.get("type") == "start":
                        t = float(m.get("t0", 0.0))
                        if m.get("phones") and encode is not None and phones is None:
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

    return api
