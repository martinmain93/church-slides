"""The comprehensive performance suite: one scenario at a time, before major releases.

`scripts/perf_report.py` is the build's quick smoke test (one song, short
sessions, idle) and runs on every release. This is the opt-in companion for
what that never touches: a long service with everything an operator does in
one, scripture, song search, the chord editor, and the app itself.

    uv run --project sidecar python scripts/perf_suite.py list
    uv run --project sidecar python scripts/perf_suite.py run engine.search
    uv run --project sidecar python scripts/perf_suite.py run engine.service --size full
    uv run --project sidecar python scripts/perf_suite.py run engine            # every engine scenario
    uv run --project sidecar python scripts/perf_suite.py run all --size full   # before a major release

Every scenario runs alone, against its own throwaway copy of your library,
Bibles and search index (nothing real is written), and has two sizes: `quick`
(the default, a minute or two, for iterating on one part) and `full` (long
enough to show slow growth). Each run is saved to `build/perf-suite/` and
compared with the previous run of the same scenario and size, so a change can
be judged in one command.

Layers:
  engine.*  the AI engine on its own (`--engine dev`, or a frozen binary)
  app.*     the whole app, UI included: needs a perf build with the UI driver
            (`scripts/build_perf_app.sh`), see `scripts/perf_app.py`

Before a major release, `FULL_PERF=1 scripts/release.sh` runs all of it at
full size (about 1.5 hours); it warns, never fails, and is off otherwise.

Growth is the median pairwise slope (Theil-Sen) over the scenario's rounds, as
in perf_report's soak: one odd sample (MLX dropping and refilling its cache)
can't fake a leak.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perf_report as pr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "perf-suite"
APP_DATA = Path.home() / "Library" / "Application Support" / "com.churchslides.app"
#: The long service: a real 12-minute live set (four songs, song changes, the
#: band talking between and over them) with an approved golden.
SERVICE_RECORDING = APP_DATA / "recordings" / "rec_20261002-185523_091"
#: Lead sheets for the chords scenario (SongSelect PDFs; local only).
LEAD_SHEETS = Path.home() / "Downloads"


# ---------------------------------------------------------------- results


#: Differences smaller than this (by unit) are never flagged, whatever the
#: percentage: 3 ms against 4 ms is 33% and means nothing.
NOISE_FLOOR = {"ms": 5.0, "s": 0.25, "MB": 25.0, "MB/min": 10.0, "% core": 5.0, "ms/s": 3.0}


@dataclass
class Metric:
    name: str
    value: float
    unit: str
    #: "lower" or "higher" is better; None: informational.
    better: str | None = "lower"


@dataclass
class Result:
    scenario: str
    size: str
    metrics: list[Metric] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    series: dict[str, list[float]] = field(default_factory=dict)

    def add(self, name: str, value: float, unit: str = "", better: str | None = "lower") -> None:
        self.metrics.append(Metric(name, float(value), unit, better))

    def latencies(self, name: str, ms: list[float]) -> None:
        if not ms:
            return
        self.add(f"{name} p50", statistics.median(ms), "ms")
        self.add(f"{name} p95", pr._pct(ms, 0.95), "ms")

    def growth(self, name: str, values: list[float], unit: str, warmup: int = 1) -> None:
        """Per-round growth of `values` (Theil-Sen), after `warmup` rounds."""
        self.series[name] = [round(v, 2) for v in values]
        settled = values[warmup:] if len(values) > warmup + 2 else values
        self.add(f"{name} growth per round", pr._slope(settled) if len(settled) > 1 else 0.0, unit)

    def save_and_compare(self) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        latest = OUT / f"{self.scenario}-{self.size}.json"
        previous = json.loads(latest.read_text()) if latest.exists() else None
        prev = {m["name"]: m["value"] for m in (previous or {}).get("metrics", [])}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        body = {
            "scenario": self.scenario,
            "size": self.size,
            "when": stamp,
            "commit": _git_head(),
            "machine": _machine(),
            "metrics": [m.__dict__ for m in self.metrics],
            "series": self.series,
            "notes": self.notes,
        }
        (OUT / f"{self.scenario}-{self.size}-{stamp}.json").write_text(json.dumps(body, indent=1))
        latest.write_text(json.dumps(body, indent=1))
        print(f"\n== {self.scenario} ({self.size})" + (f"  vs {previous['when']} {previous.get('commit', '')}" if previous else ""))
        for m in self.metrics:
            line = f"  {m.name:44s} {m.value:10.2f} {m.unit:6s}"
            if m.name in prev:
                d = m.value - prev[m.name]
                worse = (m.better == "lower" and d > 0) or (m.better == "higher" and d < 0)
                big = abs(d) > max(0.15 * abs(prev[m.name]), NOISE_FLOOR.get(m.unit, 1e-9))
                flag = "  WORSE" if worse and big else ("  better" if big and m.better else "")
                line += f"  (was {prev[m.name]:.2f}{flag})"
            print(line)
        for n in self.notes:
            print(f"  note: {n}")
        print(f"  saved {latest.relative_to(ROOT)}")


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _machine() -> str:
    import platform

    return f"{platform.system()} {platform.machine()} {os.cpu_count()} cores"


# ---------------------------------------------------------------- engine harness


def scratch_data(tmp: Path) -> tuple[Path, sqlite3.Connection]:
    """A throwaway copy of the library, Bibles, search index and hymns.
    Returns the library path to start the engine with, and a connection to
    hold open for the run: the library is in WAL mode, and as in the app
    (which keeps it open) its -wal/-shm files must exist, or the engine's
    read-only opens fail."""
    db = tmp / "library.db"
    src = sqlite3.connect(f"file:{APP_DATA / 'library.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(db)
    src.backup(dst)  # consistent even with the app's WAL open
    src.close()
    dst.execute("SELECT count(*) FROM songs").fetchone()
    for sub in ("scripture", "search", "hymns"):
        if (APP_DATA / sub).is_dir():
            shutil.copytree(APP_DATA / sub, tmp / sub)
    return db, dst


class EngineClient:
    """Requests matched by id, so a background task can ask while a session
    plays; broadcasts go to `on_message`."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.next_id = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.audio_t = 0.0  # the latest audio_level's audio clock
        self.stopped = asyncio.Event()
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.errors: list[str] = []

    async def pump(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            kind = msg.get("kind")
            if kind == "audio_level":
                self.audio_t = float(msg.get("t_seconds", self.audio_t))
            elif kind == "audio_session_state":
                if msg.get("state") in ("stopped", "error"):
                    if msg.get("state") == "error":
                        self.errors.append(msg.get("error") or "session error")
                    self.stopped.set()
            # Replies carry the request's id (whatever their kind: a pong,
            # setlist_content_updated, ..._response, error); broadcasts don't.
            fut = self.pending.get(msg["id"]) if msg.get("id") is not None else None
            if fut is not None and not fut.done():
                fut.set_result(msg)
            elif kind == "error":
                self.errors.append(msg.get("message") or "error")
            if kind == "slide_change" and msg.get("source") == "ai":
                # the app reports every move it shows; unreported, the engine
                # takes the move as declined and re-anchors after 1.5 s
                await self.notify({"kind": "record_slide_change", "item_idx": msg["item_idx"],
                                   "slide_idx": msg["slide_idx"], "source": "ai"})
            for f in self.listeners:
                f(msg)

    async def request(self, payload: dict[str, Any], timeout: float = 120) -> tuple[dict[str, Any], float]:
        """Send and wait for the reply; returns (reply, milliseconds)."""
        self.next_id += 1
        rid = self.next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        t0 = time.monotonic()
        await self.ws.send(json.dumps({**payload, "id": rid}))
        try:
            reply = await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)
        if reply.get("kind") == "error":
            raise RuntimeError(f"{payload['kind']}: {reply.get('message')}")
        return reply, (time.monotonic() - t0) * 1000

    async def notify(self, payload: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(payload))

    async def play(self, wav: Path, session: dict[str, Any], *, realtime: bool, timeout: float) -> None:
        self.stopped.clear()
        await self.request(
            {"kind": "inject_audio", "wav_path": str(wav), "realtime": realtime, "session": session},
            timeout=600,
        )
        await asyncio.wait_for(self.stopped.wait(), timeout)


@dataclass
class Ctx:
    args: argparse.Namespace
    client: EngineClient
    sampler: pr.Sampler
    tmp: Path
    result: Result

    def footprint(self) -> float:
        return self.sampler.samples[-1].private_mb if self.sampler.samples else 0.0

    async def model_session(self) -> tuple[str, str]:
        rec, _ = await self.client.request({"kind": "recommend_model"}, 60)
        backend, model = rec["backend"], rec["model_size"]
        if not rec.get("model_downloaded"):
            await self.client.request(
                {"kind": "prepare_model", "backend": backend, "model_size": model}, 900
            )
        return backend, model


async def run_engine(args: argparse.Namespace, scenario: str, body: Callable[[Ctx], Awaitable[None]]) -> Result:
    import websockets

    result = Result(scenario, args.size)
    with tempfile.TemporaryDirectory(prefix="perf-suite-") as t:
        tmp = Path(t)
        db, keep_open = scratch_data(tmp)
        engine = pr.Engine(args.engine, db)
        sampler = pr.Sampler(engine.proc.pid)
        sampler.start()
        try:
            port = engine.wait_listening(120)
            result.add("engine start", engine.listening_after or 0, "s")
            async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
                client = EngineClient(ws)
                pump = asyncio.create_task(client.pump())
                ctx = Ctx(args, client, sampler, tmp, result)
                await body(ctx)
                pump.cancel()
            if client.errors:
                result.notes.append(f"engine errors: {client.errors[:5]}")
            result.add("errors", len(client.errors), "")
            result.add("memory peak", max(s.private_mb for s in sampler.samples), "MB")
        finally:
            sampler.stop()
            crashed = engine.crashed()
            engine.stop()
            if crashed:
                result.notes.append("ENGINE CRASHED: " + " | ".join(engine.stderr_tail[-5:]))
            keep_open.close()
    return result


# ---------------------------------------------------------------- engine scenarios

SEARCH_QUERIES = [
    "songs about victory in Jesus",
    "good songs for Easter",
    "hymns about the blood of Jesus",
    "songs about grace and forgiveness",
    "Christmas songs",
    "songs about the cross",
    "a quiet song for communion",
    "songs of praise for the start of a service",
    "songs about God's faithfulness",
    "songs about the Holy Spirit",
]


async def engine_search(ctx: Ctx) -> None:
    """Song search by topic: the bundled embedding model's first load, then
    query latency and memory over repeated rounds of the queries people type."""
    rounds = 3 if ctx.args.size == "quick" else 25
    c, r = ctx.client, ctx.result
    t0 = time.monotonic()
    while True:  # the index builds on first use
        reply, ms = await c.request({"kind": "semantic_search", "texts": [SEARCH_QUERIES[0]], "limit": 20}, 300)
        if reply.get("status") == "ready":
            break
        if reply.get("status") == "unavailable":
            r.notes.append("semantic search unavailable (no search model installed)")
            return
        if time.monotonic() - t0 > 300:
            r.notes.append("search index still building after 300 s")
            return
        await asyncio.sleep(1)
    r.add("first search ready (load + index)", time.monotonic() - t0, "s")
    lat, mem = [], []
    for _ in range(rounds):
        for q in SEARCH_QUERIES:
            reply, ms = await c.request({"kind": "semantic_search", "texts": [q], "limit": 20}, 60)
            lat.append(ms)
        mem.append(ctx.footprint())
    r.latencies("topic search", lat)
    r.add("hits for the first query", len(reply.get("hits", [])), "", None)
    r.growth("memory", mem, "MB")
    hymn_ms = []
    for q in ("grace", "holy", "cross"):
        try:
            _, ms = await c.request({"kind": "search_hymns", "query": q, "refresh": False}, 60)
            hymn_ms.append(ms)
        except (RuntimeError, TimeoutError) as e:
            r.notes.append(f"hymn search: {e}")
            break
    r.latencies("hymn search (cached catalogue)", hymn_ms)


SCRIPTURE_SEARCHES = ["love one another", "the Lord is my shepherd", "faith hope and love",
                      "do not be afraid", "the word became flesh"]
# Verse ranges: passage resolution doesn't take a whole chapter ("Psalm 23").
SCRIPTURE_REFS = ["John 3:16-18", "Psalm 23:1-6", "Romans 8:28-39", "1 Corinthians 13:1-13",
                  "Isaiah 40:28-31", "Philippians 4:4-9", "Matthew 5:1-12", "Hebrews 11:1-6"]
SERMON = (
    "This morning we are in the gospel of John, chapter three, verses sixteen to eighteen. "
    "Paul says the same in Romans 8:28, and we will come back to Psalm 23 at the end. "
    "Remember Isaiah 40 verse 31: they that wait upon the Lord shall renew their strength. "
    "In First Corinthians thirteen, love is patient. Hebrews 11:1 tells us what faith is. "
) * 6


async def engine_scripture(ctx: Ctx) -> None:
    """Scripture as the operator uses it: search by words, resolve references,
    the passage around a verse, references found in a pasted sermon, and the
    live passage the operator puts up during a service."""
    rounds = 2 if ctx.args.size == "quick" else 20
    c, r = ctx.client, ctx.result
    tr, _ = await c.request({"kind": "list_scripture_translations"})
    translation = tr.get("default_translation") or (tr["translations"][0]["id"] if tr.get("translations") else "")
    if not translation:
        r.notes.append("no Bible installed: install one in Settings -> Scripture")
        return
    r.notes.append(f"translation {translation}")
    search, resolve, around, found, mem = [], [], [], [], []
    used_model = False
    for _ in range(rounds):
        for q in SCRIPTURE_SEARCHES:
            _, ms = await c.request({"kind": "search_scripture", "text": q, "translation": translation, "limit": 10})
            search.append(ms)
        reply, ms = await c.request({"kind": "resolve_passages", "references": SCRIPTURE_REFS, "translation": translation})
        resolve.append(ms)
        for ref in SCRIPTURE_REFS[:4]:
            _, ms = await c.request({"kind": "passage_contexts", "reference": ref, "translation": translation})
            around.append(ms)
        rep, ms = await c.request({"kind": "find_scripture_references", "text": SERMON, "translation": translation}, 120)
        found.append(ms)
        used_model = used_model or bool(rep.get("used_model"))
        for ref in SCRIPTURE_REFS[:3]:  # the operator putting passages up and down
            await c.notify({"kind": "live_scripture", "reference": ref, "display": "full"})
        await c.notify({"kind": "live_scripture", "reference": "", "display": "off"})
        mem.append(ctx.footprint())
    r.latencies("search by words", search)
    r.latencies("resolve 8 references", resolve)
    r.latencies("passage around a verse", around)
    r.latencies("references in a sermon", found)
    if used_model:
        r.notes.append("sermon references used the hosted model (network time included)")
    r.add("passages resolved", len(reply.get("passages", [])), "", None)
    r.growth("memory", mem, "MB")


def _say_wav(text: str, out: Path) -> Path:
    """`text` read aloud by macOS's built-in voice, as 16 kHz mono WAV."""
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-r", "165", "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(out)], check=True)
    return out


def _reading_slides(text: str, size: int = 160) -> list[list[str]]:
    """Passage text -> slides of about `size` characters, tokenised."""
    from auto_presenter.library.parser import tokenize

    slides, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > size:
            slides.append(tokenize(cur))
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        slides.append(tokenize(cur))
    return slides


async def engine_readalong(ctx: Ctx) -> None:
    """The AI following a Bible passage read aloud (read-along, off by default):
    a reading made with macOS's voice, played through a session with the
    passage as a spoken setlist item."""
    c, r = ctx.client, ctx.result
    tr, _ = await c.request({"kind": "list_scripture_translations"})
    translation = tr.get("default_translation") or ""
    ref = "John 3:1-12" if ctx.args.size == "quick" else "John 3:1-36"
    reply, _ = await c.request({"kind": "resolve_passages", "references": [ref], "translation": translation})
    if not reply.get("passages"):
        r.notes.append(f"could not resolve {ref}: {reply.get('errors')}")
        return
    text = " ".join(v["text"] for v in reply["passages"][0]["verses"])
    # off the event loop: a long passage takes `say` over 20 s, and a blocked
    # loop misses the engine's keepalive pings, which drops the connection
    wav = await asyncio.to_thread(_say_wav, text, ctx.tmp / "reading.wav")
    slides = [[], *_reading_slides(text)]
    with wave.open(str(wav)) as w:
        seconds = w.getnframes() / w.getframerate()
    backend, model = await ctx.model_session()
    from auto_presenter.protocol import ParticleFilterTunables

    moves: list[int] = []
    c.listeners.append(lambda m: moves.append(m["slide_idx"]) if m.get("kind") == "slide_change" and m.get("source") == "ai" else None)
    session = {
        "kind": "start_audio_session", "backend": backend, "model_size": model, "record": False,
        "ai_mode": "active", "start_item_idx": 0, "start_slide_idx": 0,
        "setlist_content": {"items": [{"item_idx": 0, "song_id": "reading", "title": ref,
                                       "slides": slides, "slide_sections": ["other:0"] * len(slides),
                                       "spoken": True}]},
        "tunables": ParticleFilterTunables(read_along=True).model_dump(),
    }
    ctx.sampler.phase = "readalong"
    t0 = time.monotonic()
    await c.play(wav, session, realtime=ctx.args.realtime, timeout=seconds * 3 + 120)
    wall = time.monotonic() - t0
    s = ctx.sampler.phase_samples("readalong")
    cpu = pr._cpu_percents(s)
    r.add("reading length", seconds, "s", None)
    r.add("engine speed", seconds / wall, "x real time", "higher")
    r.add("CPU while following a reading", statistics.fmean(cpu) if cpu else 0, "% core")
    r.add("slides in the passage", len(slides) - 1, "", None)
    r.add("slides the AI reached", len(set(moves) - {0}), "", "higher")


async def engine_chords(ctx: Ctx) -> None:
    """Reading lead-sheet PDFs (the chord editor's Fill from PDF and the lead
    sheet import): time and memory per sheet over repeated rounds. With
    `--network`, also the chord editor's Suggest, which asks Groq."""
    c, r = ctx.client, ctx.result
    pdfs = sorted(LEAD_SHEETS.glob("*-lead-*.pdf"))[: 3 if ctx.args.size == "quick" else 12]
    if not pdfs:
        r.notes.append(f"no lead sheets (*-lead-*.pdf) in {LEAD_SHEETS}")
    rounds = 1 if ctx.args.size == "quick" else 5
    ms_all, mem = [], []
    for _ in range(rounds):
        for pdf in pdfs:
            reply, ms = await c.request(
                {"kind": "parse_lead_sheet", "pdf_base64": base64.b64encode(pdf.read_bytes()).decode(),
                 "file_name": pdf.name}, 180)
            ms_all.append(ms)
        mem.append(ctx.footprint())
    r.latencies("read a lead sheet", ms_all)
    r.growth("memory", mem, "MB", warmup=0)
    if ctx.args.network:
        lines = [
            {"line_id": "v1", "section": "verse 1", "line": "Amazing grace how sweet the sound",
             "chords": ["G", "C", "G"], "chorded_words": ["Amazing", "sweet", "sound"],
             "chorded_word_indexes": [0, 3, 5], "role": "charted"},
            {"line_id": "v2", "section": "verse 1", "line": "That saved a wretch like me",
             "chords": [], "chorded_words": [], "chorded_word_indexes": [], "role": "target"},
        ]
        sug = []
        for _ in range(2):  # Groq's free tier: 8,000 tokens a minute
            reply, ms = await c.request({"kind": "suggest_chords", "song_key": "G", "lines": lines}, 30)
            sug.append(ms)
        r.latencies("chord Suggest (Groq)", sug)


def _concat(wav: Path, start: float, seconds: float | None, loops: int, out: Path) -> float:
    """`seconds` of `wav` from `start` (None: to the end), `loops` times over."""
    with wave.open(str(wav)) as src:
        params = src.getparams()
        rate = src.getframerate()
        src.setpos(int(start * rate))
        left = src.getnframes() - int(start * rate)
        n = left if seconds is None else min(left, int(seconds * rate))
        frames = src.readframes(n)
    with wave.open(str(out), "wb") as dst:
        dst.setparams(params)
        for _ in range(loops):
            dst.writeframes(frames)
    return n / rate


async def engine_service(ctx: Ctx) -> None:
    """One long unbroken service: a real live set (four songs, song changes,
    talk) played back to back, with an operator doing what operators do in
    it — taking over a slide, editing the setlist, switching the AI to assist
    and back, putting a Bible passage up. Memory, timing and accuracy are
    measured per pass through the set; a service must not get slower, bigger
    or worse the longer it runs."""
    c, r = ctx.client, ctx.result
    rec_dir = Path(ctx.args.recording)
    if not (rec_dir / "audio.wav").exists():
        r.notes.append(f"no recording at {rec_dir}")
        return
    rec = json.loads((rec_dir / "recording.json").read_text())
    golden = json.loads((rec_dir / "golden.json").read_text())["ground_truth"] if (rec_dir / "golden.json").exists() else []
    quick = ctx.args.size == "quick"
    loops = 1 if quick else ctx.args.loops
    # Quick: four minutes of one song sung clearly (the default recording's
    # Build My Life); full: the whole set, every pass.
    start = ctx.args.quick_start if quick else 0.0
    loop_s = _concat(rec_dir / "audio.wav", start, 240.0 if quick else None, loops, ctx.tmp / "service.wav")
    golden = [{**g, "t_seconds": g["t_seconds"] - start} for g in golden
              if 0 <= g["t_seconds"] - start <= loop_s]
    backend, model = await ctx.model_session()
    content = rec["setlist_content"]
    items = content["items"] if isinstance(content, dict) else content
    session = {
        "kind": "start_audio_session", "backend": backend, "model_size": model, "record": False,
        "ai_mode": "active", "setlist_content": {"items": items},
        # where the live session started: the golden's first song
        "start_item_idx": golden[0]["item_idx"] if golden else items[0]["item_idx"],
        "start_slide_idx": 0,
    }

    # What happened, on the audio clock.
    moves: list[tuple[float, int, int]] = []
    health: list[tuple[float, dict[str, Any]]] = []

    def listen(m: dict[str, Any]) -> None:
        if m.get("kind") == "slide_change" and m.get("source") == "ai":
            moves.append((c.audio_t, m["item_idx"], m["slide_idx"]))
        elif m.get("kind") == "pipeline_health":
            health.append((c.audio_t, m))

    c.listeners.append(listen)

    # The operator, at these points in every pass (seconds into the pass).
    def at(t: float) -> float:
        return t if quick else t * loop_s / 240.0

    tr, _ = await c.request({"kind": "list_scripture_translations"})
    translation = tr.get("default_translation") or ""
    extra = {"item_idx": max(i["item_idx"] for i in items) + 1, "song_id": "notice",
             "title": "Notices", "slides": [["welcome", "to", "church"]], "slide_sections": ["other:1"]}
    actions: list[tuple[float, str]] = [
        (at(40), "override"), (at(90), "setlist_edit"), (at(130), "assist"), (at(170), "active"),
        (at(200), "scripture_up"), (at(220), "scripture_down"),
    ]
    pass_ends: list[dict[str, float]] = []
    probe_ms: list[float] = []

    async def operator() -> None:
        done: set[tuple[int, int]] = set()
        k = 0
        while not c.stopped.is_set():
            t = c.audio_t
            loop = int(t // loop_s)
            while len(pass_ends) < loop:  # a pass just ended
                pass_ends.append({"t": t, "mb": ctx.footprint()})
            pos = t - loop * loop_s
            for i, (when, what) in enumerate(actions):
                if pos >= when and (loop, i) not in done:
                    done.add((loop, i))
                    if what == "override":
                        gt = [g for g in golden if g["t_seconds"] <= pos]
                        if gt:
                            await c.notify({"kind": "record_slide_change", "item_idx": gt[-1]["item_idx"],
                                            "slide_idx": gt[-1]["slide_idx"], "source": "override"})
                    elif what == "setlist_edit":
                        cur = moves[-1][1] if moves else items[0]["item_idx"]
                        await c.request({"kind": "update_setlist_content", "current_item_idx": cur,
                                         "setlist_content": {"items": [*items, extra]}})
                    elif what in ("assist", "active"):
                        await c.request({"kind": "set_ai_mode", "mode": what})
                    elif what == "scripture_up":
                        await c.notify({"kind": "live_scripture", "reference": "John 3:16", "display": "full"})
                        if translation:
                            _, ms = await c.request({"kind": "resolve_passages", "references": ["John 3:16"],
                                                     "translation": translation})
                            probe_ms.append(ms)
                    elif what == "scripture_down":
                        await c.notify({"kind": "live_scripture", "reference": "", "display": "off"})
            # How quickly the engine answers the UI while it follows the band.
            if k % 10 == 0 and translation:
                _, ms = await c.request({"kind": "search_scripture", "text": "grace", "translation": translation, "limit": 5})
                probe_ms.append(ms)
            k += 1
            await asyncio.sleep(0.5)

    ctx.sampler.phase = "service"
    t0 = time.monotonic()
    op = asyncio.create_task(operator())
    await c.play(ctx.tmp / "service.wav", session, realtime=ctx.args.realtime, timeout=loop_s * loops * 3 + 300)
    op.cancel()
    pass_ends.append({"t": c.audio_t, "mb": ctx.footprint()})
    wall = time.monotonic() - t0

    from auto_presenter.eval.metrics import wrong_slide_rate
    from auto_presenter.eval.types import Decision, GroundTruthEvent, SetlistItemSnapshot

    snaps = [SetlistItemSnapshot(**{k: v for k, v in i.items() if k in ("item_idx", "song_id", "slides", "slide_sections", "title")}) for i in items]
    per_pass: dict[str, list[float]] = {"wrong": [], "matcher": [], "follower": [], "asr": [], "lag": []}
    for k in range(loops):
        lo, hi = k * loop_s, (k + 1) * loop_s
        dec = [Decision(t_seconds=t - lo, item_idx=i, slide_idx=s, source="ai") for t, i, s in moves if lo <= t < hi]
        gt = [GroundTruthEvent(**g) for g in golden if g["t_seconds"] <= loop_s]
        if gt and dec:
            per_pass["wrong"].append(wrong_slide_rate(dec, gt, None, setlist_content=snaps))
        hs = [h for t, h in health if lo <= t < hi]
        per_pass["matcher"].append(pr._pct([h["matcher_ms"] for h in hs if h.get("matcher_ms")], 0.95))
        per_pass["follower"].append(statistics.fmean([h["follower_ms_per_s"] for h in hs if h.get("follower_ms_per_s")] or [0]))
        per_pass["asr"].append(pr._pct([h["asr_ms"] for h in hs if h.get("asr_ms")], 0.95))
        per_pass["lag"].append(max([h.get("lag_seconds", 0) for h in hs] or [0]))
    s = ctx.sampler.phase_samples("service")
    cpu = pr._cpu_percents(s)
    r.add("service length", loop_s * loops / 60, "min", None)
    r.add("passes through the set", loops, "", None)
    r.add("engine speed", loop_s * loops / wall, "x real time", "higher")
    r.add("CPU (mean)", statistics.fmean(cpu) if cpu else 0, "% core")
    r.add("AI slide changes", len(moves), "", None)
    if per_pass["wrong"]:
        r.add("wrong-slide, first pass", per_pass["wrong"][0], "")
        r.add("wrong-slide, last pass", per_pass["wrong"][-1], "")
        r.series["wrong-slide by pass"] = [round(x, 3) for x in per_pass["wrong"]]
    r.add("matcher update p95, last pass", per_pass["matcher"][-1], "ms")
    r.add("music follower, last pass", per_pass["follower"][-1], "ms/s")
    r.add("speech pass p95, last pass", per_pass["asr"][-1], "ms")
    r.latencies("engine reply while following", probe_ms)
    mb = [p["mb"] for p in pass_ends]
    r.series["memory by pass"] = [round(x, 1) for x in mb]
    if loops > 1:
        r.add("memory growth per pass", (mb[-1] - mb[0]) / (len(mb) - 1), "MB")
        r.add("matcher p95 growth per pass", (per_pass["matcher"][-1] - per_pass["matcher"][0]) / (loops - 1), "ms")
    # Within the session, the trend over every sample after the first pass's
    # warm-up: a slow climb shows here before it shows per pass.
    late = [x.private_mb for x in s[len(s) // 4 :]][:: max(1, len(s) // 200)]
    span_min = (s[-1].t - s[len(s) // 4].t) / 60 if len(s) > 4 else 0
    if len(late) > 3 and span_min > 0:
        r.add("memory trend in the session", pr._slope(late) * (len(late) - 1) / span_min, "MB/min")
    if quick:
        r.notes.append("quick: one 4-minute pass; --size full for the long service")
    if not ctx.args.realtime:
        r.notes.append("played faster than real time: accuracy is for comparing passes, not absolute")


ENGINE = {
    "engine.search": (engine_search, "song search by topic: model load, query latency, memory"),
    "engine.scripture": (engine_scripture, "scripture search, references, sermon parsing, live passages"),
    "engine.readalong": (engine_readalong, "the AI following a Bible reading (macOS voice)"),
    "engine.chords": (engine_chords, "lead-sheet PDFs; --network adds the chord editor's Suggest"),
    "engine.service": (engine_service, "a long live service with operator actions, per-pass drift"),
}


# ---------------------------------------------------------------- cli


def scenarios() -> dict[str, tuple[Callable, str]]:
    out = dict(ENGINE)
    try:
        import perf_app

        out.update(perf_app.SCENARIOS)
    except ImportError:
        pass
    return out


def select(names: list[str]) -> list[str]:
    known = scenarios()
    out: list[str] = []
    for n in names:
        if n == "all":
            out += list(known)
        elif n in ("engine", "app"):
            out += [k for k in known if k.startswith(n + ".")]
        elif n in known:
            out.append(n)
        else:
            raise SystemExit(f"no scenario {n!r}; see `list`")
    return list(dict.fromkeys(out))


def main() -> int:
    global pr
    import psutil
    import websockets

    pr.psutil, pr.websockets = psutil, websockets
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("names", nargs="+", help="scenarios, or engine / app / all")
    run.add_argument("--size", choices=("quick", "full"), default="quick")
    run.add_argument("--engine", default="dev", help="'dev' (the source) or a frozen engine binary")
    run.add_argument("--realtime", action="store_true", help="play audio at real speed (default: as fast as it goes)")
    run.add_argument("--loops", type=int, default=3, help="engine.service, full: passes through the set")
    run.add_argument("--recording", default=str(SERVICE_RECORDING), help="engine.service: recording folder")
    run.add_argument("--quick-start", type=float, default=275.0,
                     help="engine.service, quick: where in the recording its 4 minutes start")
    run.add_argument("--network", action="store_true", help="include steps that call hosted services (Groq)")
    run.add_argument("--app", help="app.*: the perf app bundle (default: build/perf-app/Church Slides.app)")
    args = ap.parse_args()

    known = scenarios()
    if args.cmd == "run" and not args.network:
        # No API keys: nothing in the run calls a hosted service (Groq).
        os.environ["AUTO_PRESENTER_API_KEYS_FILE"] = str(Path(tempfile.gettempdir()) / "perf-suite-no-keys.json")
    if args.cmd == "list":
        for name, (_fn, about) in known.items():
            print(f"  {name:20s} {about}")
        return 0
    failed = 0
    for name in select(args.names):
        fn, _about = known[name]
        print(f"\n### {name} ({args.size})", flush=True)
        try:
            if name.startswith("engine."):
                result = asyncio.run(run_engine(args, name, fn))
                if args.realtime:
                    result.size += "-realtime"  # never compared with a fast run
            else:
                result = asyncio.run(fn(args, name))
            result.save_and_compare()
        except Exception as e:  # one scenario failing shouldn't stop the rest
            failed += 1
            print(f"  FAILED: {type(e).__name__}: {e}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
