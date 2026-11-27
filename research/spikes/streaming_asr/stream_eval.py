"""Streaming speech providers vs chunked Whisper, on public sung audio.

Spike for docs/research/STREAMING_ASR_SPIKE.md. Two steps:

    # 1. stream every clip in real time to each provider, logging every
    #    message with the audio time it arrived at (outside the repo)
    uv run python ../research/spikes/streaming_asr/stream_eval.py run --clips 4

    # 2. score the logs: word accuracy, timing error, and latency from each
    #    word's true time to its first (interim) and final arrival
    uv run python ../research/spikes/streaming_asr/stream_eval.py score

Run from `sidecar/` so `auto_presenter` imports (the Whisper baselines go
through the app's own `SlidingWindowWrapper`, so their latency is the app's).

Audio: JamendoLyrics (public, CC, hand-made word timings), via the 16 kHz
copies in `public_goldens/jamendolyrics`. Never the church's own recordings:
this script sends audio to cloud services.

Keys are read from KEYS_FILE (or the environment) and never printed:
    DEEPGRAM_API_KEY=...
    ASSEMBLYAI_API_KEY=...
    GROQ_API_KEY=...
A provider whose key is missing is skipped.

Lyrics are read from the dataset at run time; nothing here or in the logs
lands in the repo.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RESEARCH = Path.home() / "Development" / "auto-presenter-research"
JAMALT = RESEARCH / "public_worship" / "jamalt"
GOLDENS = RESEARCH / "public_goldens" / "jamendolyrics"
WORK = RESEARCH / "work" / "streaming_asr"
KEYS_FILE = Path(os.environ.get("STREAM_EVAL_KEYS", Path.home() / ".config" / "church-slides" / "research-keys.env"))

SR = 16_000
CHUNK_S = 0.08  # 80 ms: Deepgram's recommendation for Flux, inside AssemblyAI's 50-1000 ms


# --------------------------------------------------------------------- data


@dataclass
class Clip:
    name: str
    pcm16: bytes  # 16 kHz mono s16le
    ref: list[tuple[str, float, float]]  # (normalised word, start, end)
    lines: list[str]  # lyric lines, for keyterms


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower().replace("’", "'"))


def load_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    if KEYS_FILE.exists():
        for line in KEYS_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("DEEPGRAM_API_KEY", "ASSEMBLYAI_API_KEY", "GROQ_API_KEY", "DEEPINFRA_API_KEY"):
        if os.environ.get(k):
            keys[k] = os.environ[k]
    return {k: v for k, v in keys.items() if v}


def clip_names() -> list[str]:
    return [line.strip() for line in (JAMALT / "en.txt").read_text().splitlines() if line.strip()]


HYMNS = RESEARCH / "public_goldens" / "librivox_v2"


def _slide_text(slide) -> str:
    """A slide is a string, or (in these goldens) a list of single words with
    no line breaks; the leading blank slide is []. Word lists come back as
    six-word phrases, one per line, so keyterms are phrases as for the songs."""
    if isinstance(slide, str):
        return slide
    words = [str(x) for x in slide]
    return "\n".join(" ".join(words[i : i + 6]) for i in range(0, len(words), 6))


def load_hymn(rec_id: str) -> Clip:
    """A public-domain sung hymn (LibriVox). Only slide-level timing exists, so
    each slide's words are spread evenly over the time it was shown: good for
    counting words heard, too coarse for per-word latency or timing error."""
    d = HYMNS / rec_id
    rec = json.loads((d / "recording.json").read_text())
    gold = json.loads((d / "golden.json").read_text())
    with wave.open(str(d / "audio.wav")) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        pcm = w.readframes(w.getnframes())
    items = {it["item_idx"]: it for it in rec["setlist_content"]}
    cues = sorted(gold["ground_truth"], key=lambda c: c["t_seconds"])
    ref: list[tuple[str, float, float]] = []
    lines: list[str] = []
    for k, c in enumerate(cues):
        slides = items[c["item_idx"]]["slides"]
        if not 0 <= c["slide_idx"] < len(slides):
            continue
        text = _slide_text(slides[c["slide_idx"]])
        start = c["t_seconds"]
        end = cues[k + 1]["t_seconds"] if k + 1 < len(cues) else rec["duration_seconds"]
        toks = [norm(t) for t in text.split() if norm(t)]
        step = (end - start) / max(1, len(toks))
        ref += [(t, start + i * step, start + (i + 1) * step) for i, t in enumerate(toks)]
    for it in rec["setlist_content"]:
        for s in it["slides"]:
            lines += _slide_text(s).splitlines()
    return Clip(rec_id, pcm, ref, lines)


def load_clip(name: str) -> Clip:
    if name.startswith("rec_"):
        return load_hymn(name)
    with wave.open(str(GOLDENS / f"rec_pub-jl_{name}" / "audio.wav")) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
        pcm = w.readframes(w.getnframes())
    text = (JAMALT / "lyrics" / f"{name}.txt").read_text()
    tokens = text.split()
    with open(JAMALT / "words" / f"{name}.csv") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != len(tokens):
        raise ValueError(f"{name}: {len(tokens)} lyric tokens vs {len(rows)} timings")
    ref = []
    for tok, row in zip(tokens, rows):
        n = norm(tok)
        if n:
            ref.append((n, float(row["word_start"]), float(row["word_end"])))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return Clip(name, pcm, ref, lines)


def keyterms(lines: list[str], *, max_terms: int = 100, max_chars: int = 50, max_words: int = 300) -> list[str]:
    """The song's distinct lines as keyterm phrases, within both providers'
    limits (AssemblyAI: 100 terms; Deepgram: 500 tokens, ~300 words)."""
    out: list[str] = []
    seen: set[str] = set()
    words = 0
    for ln in lines:
        phrase = re.sub(r"[^\w' ]", " ", ln)
        phrase = " ".join(phrase.split())
        while len(phrase) > max_chars:
            phrase = phrase.rsplit(" ", 1)[0]
        key = phrase.lower()
        if not phrase or key in seen:
            continue
        n = len(phrase.split())
        if len(out) >= max_terms or words + n > max_words:
            break
        seen.add(key)
        out.append(phrase)
        words += n
    return out


# --------------------------------------------------------------- providers


def _post_json(url: str, headers: dict, body: dict | None, method: str = "POST") -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def deepgram_token(key: str) -> str:
    """Short-lived grant, as our server would mint it for a slides computer."""
    return _post_json("https://api.deepgram.com/v1/auth/grant", {"Authorization": f"Token {key}"}, {"ttl_seconds": 60})[
        "access_token"
    ]


def assemblyai_token(key: str) -> str:
    try:
        return _post_json(
            "https://token.assemblyai.com/v1/token",
            {"Authorization": key},
            {"product": "streaming", "expires_in_seconds": 60},
        )["token"]
    except Exception:
        url = "https://streaming.assemblyai.com/v3/token?expires_in_seconds=60"
        return _post_json(url, {"Authorization": key}, None, method="GET")["token"]


@dataclass
class Stream:
    """One websocket stream config: provider + variant."""

    label: str
    provider: str  # dg | flux | aai
    model: str
    terms: list[str]

    def connect_args(self, keys: dict[str, str], via_token: bool) -> tuple[str, dict]:
        if self.provider in ("dg", "flux"):
            q: list[tuple[str, str]] = [("model", self.model), ("encoding", "linear16"), ("sample_rate", str(SR))]
            if self.provider == "dg":
                q += [("interim_results", "true"), ("punctuate", "false"), ("smart_format", "false")]
            q += [("keyterm", t) for t in self.terms]
            path = "v1" if self.provider == "dg" else "v2"
            url = f"wss://api.deepgram.com/{path}/listen?" + urllib.parse.urlencode(q)
            key = keys["DEEPGRAM_API_KEY"]
            auth = f"Bearer {deepgram_token(key)}" if via_token else f"Token {key}"
            return url, {"Authorization": auth}
        q2: dict[str, str] = {"sample_rate": str(SR), "encoding": "pcm_s16le", "speech_model": self.model}
        if self.terms:
            q2["keyterms_prompt"] = json.dumps(self.terms)
        headers = {}
        if via_token:
            q2["token"] = assemblyai_token(keys["ASSEMBLYAI_API_KEY"])
        else:
            headers = {"Authorization": keys["ASSEMBLYAI_API_KEY"]}
        return "wss://streaming.assemblyai.com/v3/ws?" + urllib.parse.urlencode(q2), headers

    def close_message(self) -> str:
        return json.dumps({"type": "Terminate"} if self.provider == "aai" else {"type": "CloseStream"})


async def run_ws(stream: Stream, clip: Clip, keys: dict, t0: float, out: Path, via_token: bool) -> None:
    from websockets.asyncio.client import connect

    log = out.open("w")
    log.write(json.dumps({"meta": {"label": stream.label, "model": stream.model, "n_terms": len(stream.terms)}}) + "\n")
    try:
        try:
            url, headers = await asyncio.to_thread(stream.connect_args, keys, via_token)
        except Exception as exc:  # e.g. a Deepgram key below Member can't mint grants
            if not via_token:
                raise
            log.write(json.dumps({"token_fallback": repr(exc)}) + "\n")
            url, headers = await asyncio.to_thread(stream.connect_args, keys, False)
        t_conn = time.monotonic()
        async with connect(url, additional_headers=headers, max_size=None, open_timeout=15) as ws:
            log.write(json.dumps({"connect_s": time.monotonic() - t_conn}) + "\n")

            async def sender() -> None:
                step = int(SR * CHUNK_S) * 2
                for i in range(0, len(clip.pcm16), step):
                    due = t0 + (i // 2) / SR
                    delay = due - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await ws.send(clip.pcm16[i : i + step])
                await ws.send(stream.close_message())

            send_task = asyncio.create_task(sender())
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    log.write(json.dumps({"t": time.monotonic() - t0, "msg": json.loads(raw)}) + "\n")
            finally:
                send_task.cancel()
    except Exception as exc:  # logged, scored as missing
        log.write(json.dumps({"error": repr(exc)}) + "\n")
        print(f"  {stream.label}: {exc!r}", file=sys.stderr)
    finally:
        log.close()


def run_wrapper(label: str, backend, clip: Clip, t0: float, out: Path, window: float | None = None) -> None:
    """A chunked-Whisper baseline through the app's SlidingWindowWrapper,
    fed in real time. The wrapper's `available_at` (window end + pass time)
    is when each event could first act — the app's own latency definition."""
    from auto_presenter.asr.streaming import SlidingWindowWrapper
    from auto_presenter.asr.types import TranscriptEvent
    from auto_presenter.audio.types import AudioFrame

    samples = np.frombuffer(clip.pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    rows: list[dict] = []

    def observe(ev: TranscriptEvent, available_at: float) -> None:
        rows.append(
            {
                "t": available_at,
                "wall": time.monotonic() - t0,
                "final": ev.is_final,
                "words": [[w.text, w.t_start, w.t_end] for w in ev.words],
            }
        )

    def frames():
        n = 1600
        for i in range(0, len(samples), n):
            t_start = i / SR
            delay = t0 + t_start + n / SR - time.monotonic()
            if delay > 0:
                time.sleep(delay)  # a frame exists only once it has been sung
            yield AudioFrame(samples[i : i + n], t_start, (i + len(samples[i : i + n])) / SR)

    kw = {"window_seconds": window} if window else {}
    if callable(getattr(backend, "streamer", None)):  # streams its own words (Qwen, remote)
        wrapper = backend.streamer(emission_observer=observe)
    else:
        wrapper = SlidingWindowWrapper(backend, emission_observer=observe, **kw)
    with out.open("w") as log:
        log.write(json.dumps({"meta": {"label": label, "model": backend.name()}}) + "\n")
        try:
            for _ in wrapper.transcribe(frames()):
                pass
        except Exception as exc:
            log.write(json.dumps({"error": repr(exc)}) + "\n")
        for r in rows:
            log.write(json.dumps({"wrapper": r}) + "\n")


def groq_backend(key: str, prompt: str | None):
    from auto_presenter.asr.groq_backend import GroqWhisperBackend

    class Prompted(GroqWhisperBackend):
        """Whisper's `prompt`: the closest Groq has to keyterms (224 tokens)."""

        def _call(self, wav_bytes: bytes) -> dict:  # noqa: D401
            from auto_presenter.http_client import open_multipart_post, read_json

            fields = {
                "model": self._model,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
                "language": "en",
            }
            if prompt:
                fields["prompt"] = prompt
            with open_multipart_post(
                f"{self._base_url}/audio/transcriptions",
                fields,
                file_field="file",
                filename="audio.wav",
                file_content_type="audio/wav",
                file_bytes=wav_bytes,
                api_key=self._api_key,
                timeout_seconds=self._timeout_seconds,
            ) as resp:
                return read_json(resp)

    be = Prompted(key)
    # Free tier is 20 requests a minute and the app's 3 s step sits exactly on
    # it, so a spike run gets refused windows. Pace a little under the limit.
    be.min_step_seconds = float(os.environ.get("GROQ_STEP", be.min_step_seconds))
    return be


_DI_TOKENS: dict[str, str] = {}


def deepinfra_backend(key: str, model: str, *, carry_prompt: bool):
    """DeepInfra's OpenAI-compatible endpoint with a scoped JWT for one model,
    as the app would get one from its server (docs/roadmap/HOSTED_SPEECH.md)."""
    from auto_presenter.asr.hosted_backend import HostedCredentials, HostedSpeechBackend

    if model not in _DI_TOKENS:
        _DI_TOKENS[model] = _post_json(
            "https://api.deepinfra.com/v1/scoped-jwt",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {"models": [model], "expires_delta": 7200, "spending_limit": 2.0},
        )["token"]
    creds = HostedCredentials("https://api.deepinfra.com/v1/openai", model, _DI_TOKENS[model])
    return HostedSpeechBackend(lambda: creds, carry_prompt=carry_prompt)


async def run_clip(clip: Clip, providers: list[str], keys: dict, variants: list[str], via_token: bool, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    terms = keyterms(clip.lines)
    streams: list[Stream] = []
    for v in variants:
        t = terms if v == "kt" else []
        if "DEEPGRAM_API_KEY" in keys:
            if "nova3" in providers:
                streams.append(Stream(f"nova3-{v}", "dg", "nova-3", t))
            if "flux" in providers:
                streams.append(Stream(f"flux-{v}", "flux", "flux-general-en", t))
        if "ASSEMBLYAI_API_KEY" in keys:
            if "aai" in providers:
                streams.append(Stream(f"aai-{v}", "aai", "assemblyai-stt-3-7-realtime", t))
            if "aai-u" in providers:
                streams.append(Stream(f"aaiu-{v}", "aai", "universal-streaming-english", t))
    t0 = time.monotonic() + 3.0  # time for every connection to open first
    threads: list[threading.Thread] = []
    if "groq" in providers and "GROQ_API_KEY" in keys:
        for v in variants:
            if v == "kt" and "groq-prompt" not in providers:
                continue  # a second Groq stream doubles the rate (free tier: 20/min)
            prompt = " ".join(terms)[:800] if v == "kt" else None
            be = groq_backend(keys["GROQ_API_KEY"], prompt)
            threads.append(threading.Thread(target=run_wrapper, args=(f"groq-{v}", be, clip, t0, outdir / f"groq-{v}.jsonl")))
    if "deepinfra" in providers and "DEEPINFRA_API_KEY" in keys:
        # DI_STREAMS: label=model:window[:p], comma-separated (p = carry the
        # words heard so far as the prompt). Online speech, asr.hosted_backend.
        for spec in os.environ.get("DI_STREAMS", "di06=Qwen/Qwen3-ASR-0.6B:6").split(","):
            label, rest = spec.split("=")
            model, window, *flags = rest.split(":")
            be = deepinfra_backend(keys["DEEPINFRA_API_KEY"], model, carry_prompt="p" in flags)
            threads.append(threading.Thread(target=run_wrapper, args=(label, be, clip, t0, outdir / f"{label}.jsonl", float(window))))
    if "remote" in providers:
        # REMOTE_SPEECH_URL + REMOTE_SPEECH_TOKEN: the streaming Qwen engine on a
        # server (speech-server/), asr.remote_backend.
        from auto_presenter.asr.remote_backend import RemoteStreamingBackend

        token = os.environ["REMOTE_SPEECH_TOKEN"]
        be = RemoteStreamingBackend(os.environ["REMOTE_SPEECH_URL"], lambda: token)
        label = os.environ.get("REMOTE_LABEL", "remote")
        # REMOTE_COPIES: that many streams at once, as that many churches would
        for k in range(int(os.environ.get("REMOTE_COPIES", "1"))):
            lab = label if k == 0 else f"{label}_{k}"
            be_k = be if k == 0 else RemoteStreamingBackend(os.environ["REMOTE_SPEECH_URL"], lambda: token)
            if os.environ.get("REMOTE_PHONES"):
                # Ask for the next-line check's phones too, as the app does,
                # and log how late each tick arrives (room clock - t_end).
                plog = (outdir / f"{lab}.phones.jsonl").open("w")

                def on_phones(t_end, logits, ms, plog=plog):
                    plog.write(json.dumps({"late": time.monotonic() - t0 - t_end, "ms": ms}) + "\n")

                be_k.on_phones = on_phones
            threads.append(threading.Thread(target=run_wrapper, args=(lab, be_k, clip, t0, outdir / f"{lab}.jsonl")))
    if "mlx" in providers:
        from auto_presenter.asr.mlx_backend import MLXWhisperBackend

        be = MLXWhisperBackend("small.en")
        be.warm()
        threads.append(threading.Thread(target=run_wrapper, args=("mlx-off", be, clip, t0, outdir / "mlx-off.jsonl")))
    for th in threads:
        th.start()
    await asyncio.gather(*(run_ws(s, clip, keys, t0, outdir / f"{s.label}.jsonl", via_token) for s in streams))
    for th in threads:
        await asyncio.to_thread(th.join)


# ------------------------------------------------------------------ scoring


@dataclass
class Obs:
    t: float  # audio time it arrived
    final: bool
    words: list[tuple[str, float | None, float | None]]


def parse_log(path: Path) -> tuple[dict, list[Obs], str | None]:
    meta: dict = {}
    obs: list[Obs] = []
    err = None
    aai_final_counts: dict[int, int] = {}
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        if "meta" in rec:
            meta = rec["meta"]
        elif "error" in rec:
            err = rec["error"]
        elif "wrapper" in rec:
            r = rec["wrapper"]
            obs.append(Obs(r["t"], r["final"], [(norm(w[0]), w[1], w[2]) for w in r["words"]]))
        elif "msg" in rec:
            m, t = rec["msg"], rec["t"]
            typ = m.get("type")
            if typ == "Results":  # Deepgram Nova
                alt = (m.get("channel", {}).get("alternatives") or [{}])[0]
                ws = [(norm(w["word"]), w.get("start"), w.get("end")) for w in alt.get("words", [])]
                obs.append(Obs(t, bool(m.get("is_final")), ws))
            elif typ == "TurnInfo":  # Deepgram Flux: whole turn so far, final at EndOfTurn
                ws = [(norm(w["word"]), w.get("start"), w.get("end")) for w in m.get("words", [])]
                obs.append(Obs(t, m.get("event") == "EndOfTurn", ws))
            elif typ == "Turn":  # AssemblyAI: per-word finality
                turn = m.get("turn_order", 0)
                ws = m.get("words", [])
                done = aai_final_counts.get(turn, 0)
                finals = [w for w in ws if w.get("word_is_final")]
                # Universal-Streaming re-sends a finished turn once formatted;
                # STT-3.7 formats every message. Counting final words per turn
                # handles both without double-counting.
                new_final = finals[done:]
                if new_final:
                    aai_final_counts[turn] = len(finals)
                    obs.append(Obs(t, True, [(norm(w["text"]), w["start"] / 1000, w["end"] / 1000) for w in new_final]))
                interim = [w for w in ws if not w.get("word_is_final")]
                if interim:
                    obs.append(Obs(t, False, [(norm(w["text"]), w["start"] / 1000, w["end"] / 1000) for w in interim]))
    for o in obs:
        o.words = [w for w in o.words if w[0]]
    return meta, obs, err


def align(ref: list[str], hyp: list[str]) -> tuple[int, int, int, list[tuple[int, int]]]:
    """Levenshtein alignment. Returns (subs, dels, ins, [(ref_i, hyp_j) hits])."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0] = np.arange(n + 1)
    d[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        ri = ref[i - 1]
        for j in range(1, m + 1):
            c = 0 if ri == hyp[j - 1] else 1
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + c)
    i, j, s, de, ins, hits = n, m, 0, 0, 0, []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i, j] == d[i - 1, j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1):
            if ref[i - 1] == hyp[j - 1]:
                hits.append((i - 1, j - 1))
            else:
                s += 1
            i, j = i - 1, j - 1
        elif i > 0 and d[i, j] == d[i - 1, j] + 1:
            de, i = de + 1, i - 1
        else:
            ins, j = ins + 1, j - 1
    return s, de, ins, hits[::-1]


def score_stream(clip_ref: list[tuple[str, float, float]], obs: list[Obs]) -> dict:
    finals = [(w, o.t) for o in obs if o.final for w in o.words]
    # Flux re-sends the whole turn; its EndOfTurn is the final. Nova/AAI/wrapper
    # finals don't repeat. Order by word time where known.
    finals.sort(key=lambda x: (x[0][1] if x[0][1] is not None else x[1]))
    ref_words = [r[0] for r in clip_ref]
    hyp_words = [w[0][0] for w in finals]
    s, de, ins, hits = align(ref_words, hyp_words)
    n = len(ref_words)
    timing = []
    final_lat = []
    for ri, hj in hits:
        (txt, hs, he), arrived = finals[hj]
        if hs is not None:
            timing.append(hs - clip_ref[ri][1])
        final_lat.append(arrived - clip_ref[ri][2])
    # First heard: earliest message (interim or final) carrying the same word
    # within 1.5 s of where it was sung. Only over words the final got right,
    # so a lucky interim match on a common word doesn't count.
    first_lat = []
    by_word: dict[str, list[tuple[float, float]]] = {}
    for o in obs:
        for txt, hs, he in o.words:
            mid = (hs + he) / 2 if hs is not None and he is not None else None
            if mid is not None:
                by_word.setdefault(txt, []).append((mid, o.t))
    for ri, _ in hits:
        txt, rs, re_ = clip_ref[ri]
        cands = [t for mid, t in by_word.get(txt, []) if rs - 1.5 <= mid <= re_ + 1.5 and t >= rs]
        if cands:
            first_lat.append(min(cands) - re_)
    # Heard at all: any message, interim or final, carrying the word near where
    # it was sung. Catches Flux, whose finals only come at a (rare) turn end.
    heard = 0
    for txt, rs, re_ in clip_ref:
        if any(rs - 1.5 <= mid <= re_ + 1.5 for mid, _ in by_word.get(txt, [])):
            heard += 1
    return {
        "heard": heard,
        "n_ref": n,
        "wer": (s + de + ins) / n if n else 0.0,
        "recall": len(hits) / n if n else 0.0,
        "subs": s,
        "dels": de,
        "ins": ins,
        "hits": len(hits),
        "timing_err": timing,
        "final_lat": final_lat,
        "first_lat": first_lat,
        "n_msgs": len(obs),
    }


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def cmd_score(args) -> None:
    root = Path(args.out)
    agg: dict[str, dict] = {}
    per_clip: list[dict] = []
    for clip_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        clip = load_clip(clip_dir.name)
        for log in sorted(clip_dir.glob("*.jsonl")):
            meta, obs, err = parse_log(log)
            label = log.stem
            if err and not obs:
                per_clip.append({"clip": clip.name, "stream": label, "error": err})
                continue
            r = score_stream(clip.ref, obs)
            a = agg.setdefault(label, {"n_ref": 0, "err": 0, "hits": 0, "heard": 0, "timing": [], "final": [], "first": [], "clips": 0})
            a["n_ref"] += r["n_ref"]
            a["heard"] += r["heard"]
            a["err"] += r["subs"] + r["dels"] + r["ins"]
            a["hits"] += r["hits"]
            a["timing"] += r["timing_err"]
            a["final"] += r["final_lat"]
            a["first"] += r["first_lat"]
            a["clips"] += 1
            per_clip.append(
                {"clip": clip.name, "stream": label, "wer": round(r["wer"], 3), "recall": round(r["recall"], 3),
                 "final_p50": round(pct(r["final_lat"], 0.5), 2), "first_p50": round(pct(r["first_lat"], 0.5), 2),
                 "error": err}
            )
    header = (f"{'stream':<12}{'clips':>6}{'WER':>7}{'recall':>8}{'heard':>7}{'first p50':>10}{'p90':>6}"
              f"{'final p50':>10}{'p90':>6}{'|t err| p50':>12}{'bias':>7}{'spread':>8}")
    print(header)
    rows = []
    for label, a in sorted(agg.items()):
        te = [abs(x) for x in a["timing"]]
        bias = statistics.median(a["timing"]) if a["timing"] else 0.0
        row = {
            "heard": a["heard"] / a["n_ref"],
            "timing_spread_p50": pct([abs(x - bias) for x in a["timing"]], 0.5),
            "stream": label,
            "clips": a["clips"],
            "words": a["n_ref"],
            "wer": a["err"] / a["n_ref"],
            "recall": a["hits"] / a["n_ref"],
            "first_p50": pct(a["first"], 0.5),
            "first_p90": pct(a["first"], 0.9),
            "final_p50": pct(a["final"], 0.5),
            "final_p90": pct(a["final"], 0.9),
            "timing_abs_p50": pct(te, 0.5),
            "timing_bias": statistics.median(a["timing"]) if a["timing"] else float("nan"),
        }
        rows.append(row)
        print(
            f"{label:<12}{row['clips']:>6}{row['wer']:>7.3f}{row['recall']:>8.3f}{row['heard']:>7.3f}{row['first_p50']:>10.2f}{row['first_p90']:>6.2f}"
            f"{row['final_p50']:>10.2f}{row['final_p90']:>6.2f}{row['timing_abs_p50']:>12.2f}{row['timing_bias']:>7.2f}{row['timing_spread_p50']:>8.2f}"
        )
    (root / "summary.json").write_text(json.dumps({"streams": rows, "per_clip": per_clip}, indent=1))
    print(f"\nper clip + summary: {root / 'summary.json'}")


def cmd_run(args) -> None:
    keys = load_keys()
    print("keys found:", ", ".join(sorted(k.split("_")[0].lower() for k in keys)) or "none")
    names = args.names.split(",") if args.names else clip_names()[args.skip : args.skip + args.clips]
    providers = args.providers.split(",")
    variants = args.variants.split(",")
    for name in names:
        clip = load_clip(name)
        if args.seconds:
            start = int(args.start * SR) * 2
            clip.pcm16 = clip.pcm16[start : start + int(args.seconds * SR) * 2]
            if args.start:  # shift the reference to the piece's own clock
                clip.ref = [(w, s - args.start, e - args.start) for w, s, e in clip.ref
                            if args.start <= s and e <= args.start + args.seconds]
        print(f"{name}: {len(clip.pcm16) / 2 / SR:.0f} s, {len(clip.ref)} words, {len(keyterms(clip.lines))} keyterms")
        asyncio.run(run_clip(clip, providers, keys, variants, not args.no_token, Path(args.out) / name))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--clips", type=int, default=4)
    r.add_argument("--skip", type=int, default=0)
    r.add_argument("--names", default="")
    r.add_argument("--providers", default="nova3,flux,aai,groq,mlx")
    r.add_argument("--variants", default="off,kt")
    r.add_argument("--seconds", type=float, default=0.0, help="stream only this much of each clip (connection checks)")
    r.add_argument("--start", type=float, default=0.0)
    r.add_argument("--no-token", action="store_true", help="connect with the API key instead of a minted short-lived token")
    r.add_argument("--out", default=str(WORK / "runs" / "default"))
    s = sub.add_parser("score")
    s.add_argument("--out", default=str(WORK / "runs" / "default"))
    args = ap.parse_args()
    {"run": cmd_run, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
