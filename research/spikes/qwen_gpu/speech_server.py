"""The speech server's websocket (protocol in `modal_app.py`), as a plain
FastAPI app so it runs anywhere: on Modal's GPU, or locally on a Mac's
(`python serve_local.py`) to check the protocol end to end.
"""




def make_api(backend, *, secret: str, gpu: str, load_s: float):
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

    api = FastAPI()
    secret_b = secret.encode()

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
        except WebSocketDisconnect:
            pass
        finally:
            frames.put(None)
        if started:
            await asyncio.to_thread(worker.join)
        try:
            await send_task
            await ws.close()
        except Exception:
            pass

    return api
