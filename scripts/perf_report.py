"""Run the real AI engine on real audio and report what it cost the machine.

The build's smoke test for performance and leaks. It starts the engine as its
own process (the frozen binary a build just produced, or the dev source), plays
`data/fixtures/perf/service.wav` into it as if it were the microphone, and
samples the process from outside with the OS's own counters (psutil: CPU time,
resident and private memory, threads, handles). Alongside those it records what
the engine says about itself (speech-pass and matcher timings, lag, dropped
audio) and scores the slides it chose against the fixture's ground truth.

Phases, about 3 minutes on Apple silicon:

  startup   process start -> listening, then the speech model's load
  live      the 100 s clip at real speed: CPU, memory, timings, lag, accuracy
  soak      the clip again, back to back as fast as the engine goes, with a
            session restart each time: memory and threads must not keep
            growing (the leak smoke test)
  idle      a few seconds with no session: CPU must drop to ~0

`--quick` skips the live phase (and its accuracy score) for a short smoke run.

Every number is compared with what's expected for this machine
(`data/fixtures/perf/expected.json`, keyed by `--label`). Higher than expected
is a warning; only an extreme outlier, or the engine crashing, fails the run
(exit 1). `--update-expected` records this run as the new expectation.

    uv run --project sidecar python scripts/perf_report.py --label macos-apple-silicon
    uv run --project sidecar python scripts/perf_report.py --engine build/sidecar/auto-presenter-sidecar \\
        --label macos-apple-silicon --out build/perf

    # Fold reports into a release's perf.json (the changelog page's card)
    python3 scripts/perf_report.py merge build/release/perf.json build/perf/perf-*.json

    # Take a saved report's numbers as the expectation for its label
    python3 scripts/perf_report.py adopt build/perf/perf-windows-x64-ci.json
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Imported in main() for a run only: `merge` and `adopt` work with a bare
# python3, as the release script calls them.
psutil: Any = None
websockets: Any = None

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "data" / "fixtures" / "perf"
EXPECTED = FIXTURE / "expected.json"
SAMPLE_SECONDS = 0.25


# --------------------------------------------------------------------- engine


def engine_command(engine: str) -> tuple[list[str], Path]:
    if engine == "dev":
        return [sys.executable, "-m", "auto_presenter"], ROOT / "sidecar"
    exe = Path(engine).resolve()
    if not exe.is_file():
        raise SystemExit(f"no engine at {exe}")
    return [str(exe)], exe.parent


class Engine:
    """The engine process, its stdout drained on a thread so it never blocks."""

    def __init__(self, engine: str, db_path: Path) -> None:
        cmd, cwd = engine_command(engine)
        self.started = time.monotonic()
        self.proc = subprocess.Popen(
            [*cmd, "--port", "0", "--db-path", str(db_path), "--dev-tools"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.port: int | None = None
        self.listening_after: float | None = None
        self.stderr_tail: list[str] = []
        self._ready = threading.Event()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            if self.port is None and line.startswith("{"):
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("event") == "listening":
                    self.port = int(msg["port"])
                    self.listening_after = time.monotonic() - self.started
                    self._ready.set()
        self._ready.set()

    def _read_stderr(self) -> None:
        assert self.proc.stderr
        for line in self.proc.stderr:
            self.stderr_tail = ([*self.stderr_tail, line.rstrip()])[-40:]

    def wait_listening(self, timeout: float) -> int:
        self._ready.wait(timeout)
        if self.port is None:
            tail = "\n".join(self.stderr_tail[-15:])
            raise RuntimeError(f"the engine never started listening:\n{tail}")
        return self.port

    def crashed(self) -> bool:
        return self.proc.poll() is not None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# -------------------------------------------------------------------- sampler


@dataclass
class Sample:
    t: float
    phase: str
    cpu_seconds: float  # user + system, whole process tree, cumulative
    rss_mb: float
    private_mb: float  # memory only this process holds (USS / private bytes)
    threads: int
    handles: int
    wakeups: int  # cumulative (macOS)


def _mac_rusage(pid: int) -> tuple[int, int] | None:
    """(phys_footprint bytes, wakeups so far) from the kernel's
    proc_pid_rusage. phys_footprint is Activity Monitor's "Memory" column;
    resident size overstates it (shared libraries, reclaimable pages), and
    psutil's USS isn't available for it on macOS. Wakeups (idle + interrupt)
    are what "Energy Impact" counts."""
    import ctypes

    global _libproc
    if _libproc is None:
        _libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    buf = ctypes.create_string_buffer(512)  # rusage_info_v2 and change
    if _libproc.proc_pid_rusage(pid, 2, buf) != 0:
        return None
    # struct rusage_info_v2: 16-byte uuid, then uint64s: user_time,
    # system_time, pkg_idle_wkups, interrupt_wkups, pageins, wired_size,
    # resident_size, phys_footprint, ...
    words = ctypes.cast(ctypes.byref(buf, 16), ctypes.POINTER(ctypes.c_uint64))
    return int(words[7]), int(words[2] + words[3])


_libproc = None


def _private_and_wakeups(p: psutil.Process) -> tuple[int, int]:
    """What the process alone holds, as the OS's own monitor shows it:
    phys_footprint on a Mac, private bytes on Windows (Task Manager's
    "Memory"), USS on Linux. Wakeups are macOS only (0 elsewhere)."""
    if sys.platform == "darwin":
        got = _mac_rusage(p.pid)
        if got:
            return got
    if sys.platform == "win32":
        return int(getattr(p.memory_info(), "private", 0)), 0
    try:
        return int(p.memory_full_info().uss), 0
    except (psutil.AccessDenied, AttributeError):
        return int(p.memory_info().rss), 0


class Sampler:
    """Polls the engine's process tree from outside, as the OS sees it."""

    def __init__(self, pid: int) -> None:
        self.root = psutil.Process(pid)
        self.phase = "startup"
        self.samples: list[Sample] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._t0 = time.monotonic()
        self._dead_cpu = 0.0  # CPU of children that have already exited
        self._seen: dict[int, float] = {}

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                procs = [self.root, *self.root.children(recursive=True)]
            except psutil.NoSuchProcess:
                return
            cpu = rss = private = threads = handles = wakeups = 0.0
            alive: dict[int, float] = {}
            for p in procs:
                try:
                    with p.oneshot():
                        t = p.cpu_times()
                        c = t.user + t.system
                        alive[p.pid] = c
                        cpu += c
                        rss += p.memory_info().rss
                        mem, wk = _private_and_wakeups(p)
                        private += mem
                        wakeups += wk
                        threads += p.num_threads()
                        handles += p.num_handles() if sys.platform == "win32" else p.num_fds()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            for pid, last in self._seen.items():
                if pid not in alive:
                    self._dead_cpu += last
            self._seen = alive
            self.samples.append(
                Sample(
                    t=time.monotonic() - self._t0,
                    phase=self.phase,
                    cpu_seconds=cpu + self._dead_cpu,
                    rss_mb=rss / 1e6,
                    private_mb=private / 1e6,
                    threads=int(threads),
                    handles=int(handles),
                    wakeups=int(wakeups),
                )
            )
            self._stop.wait(SAMPLE_SECONDS)

    def phase_samples(self, phase: str) -> list[Sample]:
        return [s for s in self.samples if s.phase == phase]


# ------------------------------------------------------------------ the drive


@dataclass
class Collected:
    ai_moves: list[tuple[float, int, int]] = field(default_factory=list)  # (t, item, slide)
    health: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Client:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.next_id = 0
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.collected = Collected()
        self.clock_zero = time.monotonic()
        self.stopped = asyncio.Event()

    async def pump(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            kind = msg.get("kind")
            now = time.monotonic() - self.clock_zero
            if kind == "slide_change" and msg.get("source") == "ai":
                self.collected.ai_moves.append((now, msg["item_idx"], msg["slide_idx"]))
                # the app reports every move it shows; unreported, the engine
                # takes the move as declined and re-anchors after 1.5 s
                await self.ws.send(json.dumps({"kind": "record_slide_change", "item_idx": msg["item_idx"],
                                               "slide_idx": msg["slide_idx"], "source": "ai"}))
            elif kind == "pipeline_health":
                self.collected.health.append({**msg, "t": now})
            elif kind == "audio_session_state":
                if msg.get("state") == "stopped":
                    self.stopped.set()
                elif msg.get("state") == "error":
                    self.collected.errors.append(msg.get("error") or "session error")
                    self.stopped.set()
            elif kind == "error":
                self.collected.errors.append(msg.get("message") or "error")
            await self.inbox.put(msg)

    async def request(
        self, payload: dict[str, Any], reply_kind: str, timeout: float
    ) -> dict[str, Any]:
        self.next_id += 1
        payload = {**payload, "id": self.next_id}
        await self.ws.send(json.dumps(payload))
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no {reply_kind} within {timeout:.0f}s")
            msg = await asyncio.wait_for(self.inbox.get(), left)
            if msg.get("id") == self.next_id and msg.get("kind") in (reply_kind, "error"):
                if msg["kind"] == "error":
                    raise RuntimeError(msg.get("message"))
                return msg

    async def play(
        self, wav: Path, session: dict[str, Any], *, realtime: bool, timeout: float
    ) -> float:
        """Start a session on the file and wait for it to run out. Returns the
        seconds from the request to the session running (the model load)."""
        self.stopped.clear()
        sent = time.monotonic()
        reply = await self.request(
            {
                "kind": "inject_audio",
                "wav_path": str(wav),
                "realtime": realtime,
                "session": session,
            },
            "inject_audio_response",
            timeout=600,
        )
        if reply.get("error"):
            raise RuntimeError(f"inject_audio: {reply['error']}")
        loaded = time.monotonic() - sent
        self.clock_zero = time.monotonic()
        await asyncio.wait_for(self.stopped.wait(), timeout)
        return loaded


def _excerpt(wav: Path, seconds: float, tmp: Path) -> Path:
    """The fixture's first `seconds`, for the soak passes: they're about
    repeated sessions with real work in them, not about the whole song."""
    import wave

    out = tmp / "soak.wav"
    with wave.open(str(wav)) as src, wave.open(str(out), "wb") as dst:
        dst.setparams(src.getparams())
        dst.writeframes(src.readframes(int(seconds * src.getframerate())))
    return out


async def drive(engine: Engine, sampler: Sampler, args: argparse.Namespace) -> dict[str, Any]:
    fixture = json.loads((FIXTURE / "service.json").read_text(encoding="utf-8"))
    wav = FIXTURE / fixture["audio"]
    port = engine.wait_listening(120)
    out: dict[str, Any] = {"startup_seconds": engine.listening_after}

    async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
        client = Client(ws)
        pump = asyncio.create_task(client.pump())
        rec = await client.request({"kind": "recommend_model"}, "recommend_model_response", 60)
        backend = args.backend or rec["backend"]
        model = args.model or rec["model_size"]
        out["model"] = f"{backend} {model}"
        if not rec.get("model_downloaded") or args.model or args.backend:
            await client.request(
                {"kind": "prepare_model", "backend": backend, "model_size": model},
                "prepare_model_response",
                timeout=900,
            )
        # As the app does before offering live AI (asr/machine_check.py): off
        # Apple silicon, a computer below the bar gets no live AI at all, so
        # there's no following to judge on it.
        check = await client.request({"kind": "assess_machine"}, "assess_machine_response", 600)
        out["machine_check"] = {
            k: check.get(k) for k in ("verdict", "backend", "speech_load", "limit", "reason")
        }
        session = {
            "kind": "start_audio_session",
            "backend": backend,
            "model_size": model,
            "setlist_content": fixture["setlist_content"],
            "start_item_idx": fixture["start_item_idx"],
            "start_slide_idx": fixture["start_slide_idx"],
            "ai_mode": "active",
            "record": False,
        }
        if args.tunable:
            # e.g. score_follower_cpu=1: profile a setting before it ships
            from auto_presenter.protocol import ParticleFilterTunables

            fields = ParticleFilterTunables.model_fields
            overrides = {}
            for item in args.tunable:
                key, _, raw = item.partition("=")
                if key not in fields:
                    raise SystemExit(f"--tunable: no tunable {key!r}")
                overrides[key] = raw.lower() in ("1", "true", "yes") if fields[key].annotation is bool else raw
            session["tunables"] = ParticleFilterTunables(**overrides).model_dump()
            out["tunables"] = overrides

        if not args.quick:
            # The model loads inside the first session start: timed, then the live run.
            client.collected = Collected()
            out["model_load_seconds"] = await _play_live(client, wav, session, sampler, fixture)
            out["live"] = summarise_live(client.collected, sampler, fixture)

        sampler.phase = "soak"
        soak_ends: list[Sample] = []
        soak_started = time.monotonic()
        excerpt = _excerpt(wav, args.soak_seconds, Path(args.tmp))
        for i in range(args.soak):
            loaded = await client.play(excerpt, session, realtime=False, timeout=900)
            if args.quick and i == 0:
                out["model_load_seconds"] = loaded
            if sampler.samples:
                soak_ends.append(sampler.samples[-1])
        soak_wall = time.monotonic() - soak_started
        out["soak"] = summarise_soak(soak_ends, sampler, args.soak_seconds * args.soak, soak_wall)
        out["session_errors"] = client.collected.errors

        sampler.phase = "idle"
        await asyncio.sleep(args.idle)
        pump.cancel()
    out["idle"] = summarise_idle(sampler)
    return out


async def _play_live(
    client: Client, wav: Path, session: dict[str, Any], sampler: Sampler, fixture: dict[str, Any]
) -> float:
    sampler.phase = "load"
    client.stopped.clear()
    sent = time.monotonic()
    reply = await client.request(
        {"kind": "inject_audio", "wav_path": str(wav), "realtime": True, "session": session},
        "inject_audio_response",
        timeout=600,
    )
    if reply.get("error"):
        raise RuntimeError(f"inject_audio: {reply['error']}")
    load = time.monotonic() - sent
    sampler.phase = "live"
    client.clock_zero = time.monotonic()
    await asyncio.wait_for(client.stopped.wait(), fixture["duration_seconds"] + 60)
    return load


# ------------------------------------------------------------------ summaries


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def _cpu_percents(samples: list[Sample]) -> list[float]:
    """Percent of one core, between consecutive samples."""
    out = []
    for a, b in itertools.pairwise(samples):
        dt = b.t - a.t
        if dt > 0:
            out.append(100 * (b.cpu_seconds - a.cpu_seconds) / dt)
    return out


def summarise_live(c: Collected, sampler: Sampler, fixture: dict[str, Any]) -> dict[str, Any]:
    s = sampler.phase_samples("live")
    cpu = _cpu_percents(s)
    audio = fixture["duration_seconds"]
    asr = [h["asr_ms"] for h in c.health if h.get("asr_ms")]
    matcher = [h["matcher_ms"] for h in c.health if h.get("matcher_ms")]
    lag = [h["lag_seconds"] for h in c.health]

    def health(key: str) -> list[float]:
        return [h[key] for h in c.health if h.get(key)]

    # The music follower and chord analyser, on the capture thread beside the
    # speech engine (`pipeline.stage_cost`). Absent when they don't run.
    music = {
        f"{name}_ms_per_s": statistics.fmean(health(f"{name}_ms_per_s"))
        for name in ("follower", "chord")
        if health(f"{name}_ms_per_s")
    } | {
        f"{name}_frame_ms_p95": _pct(health(f"{name}_frame_ms_p95"), 0.95)
        for name in ("follower", "chord")
        if health(f"{name}_frame_ms_p95")
    }
    return {
        "cpu_percent_mean": statistics.fmean(cpu) if cpu else 0.0,
        "cpu_percent_p95": _pct(cpu, 0.95),
        "cpu_seconds_per_audio_second": (s[-1].cpu_seconds - s[0].cpu_seconds) / audio
        if len(s) > 1
        else 0.0,
        "private_mb_peak": max((x.private_mb for x in s), default=0.0),
        "rss_mb_peak": max((x.rss_mb for x in s), default=0.0),
        "threads_peak": max((x.threads for x in s), default=0),
        "asr_ms_p50": _pct(asr, 0.5),
        "asr_ms_p95": _pct(asr, 0.95),
        "matcher_ms_p95": _pct(matcher, 0.95),
        "lag_seconds_max": max(lag, default=0.0),
        "dropped_chunks": max((h.get("dropped_chunks", 0) for h in c.health), default=0),
        **music,
        **score(c.ai_moves, fixture),
    }


def score(moves: list[tuple[float, int, int]], fixture: dict[str, Any]) -> dict[str, Any]:
    """Each expected change counts as on time when the AI showed that slide
    from 2 s early to 5 s late. Moves to another song are always wrong."""
    item = fixture["start_item_idx"]
    hits, delays = 0, []
    for g in fixture["ground_truth"]:
        match = [
            t
            for t, i, sl in moves
            if i == g["item_idx"] and sl == g["slide_idx"] and -2 <= t - g["t_seconds"] <= 5
        ]
        if match:
            hits += 1
            delays.append(min(match, key=lambda t: abs(t - g["t_seconds"])) - g["t_seconds"])
    return {
        "slides_on_time": hits,
        "slides_expected": len(fixture["ground_truth"]),
        "slide_delay_median_s": statistics.median(delays) if delays else None,
        "ai_moves": len(moves),
        "wrong_song_moves": sum(1 for _, i, _ in moves if i != item),
    }


def summarise_soak(
    ends: list[Sample], sampler: Sampler, audio_seconds: float, wall: float
) -> dict[str, Any]:
    s = sampler.phase_samples("soak")
    # The first passes warm caches (the speech model's and the score
    # follower's GPU buffers), so the trend starts after `SOAK_WARMUP`. And
    # MLX releases and refills its cache between sessions, so single passes
    # swing by hundreds of MB with no leak at all: on v0.0.11 the passes read
    # 2178, 2138, 2143, 2144, 1927, 2152, 2133, 2145 MB — flat — and a least-
    # squares line through the last four called it +64 MB a pass. The slope
    # is the median of every pairwise slope (Theil-Sen), which one odd pass
    # can't move: 24 passes of the same build, any 12 in a row, read -4..+7
    # MB a pass (least squares: -15..+34). A real leak (the session cycle
    # fixed 2026-10-19, ~50 MB a restart) stands well clear of that.
    settled = ends[SOAK_WARMUP:] if len(ends) > SOAK_WARMUP + 2 else ends[1:] or ends
    per_pass = _slope([e.private_mb for e in settled])
    threads = settled[-1].threads - settled[0].threads if len(settled) > 1 else 0
    return {
        "passes": len(ends),
        "speed_x_realtime": audio_seconds / wall if wall else 0.0,
        "private_mb_peak": max((x.private_mb for x in s), default=0.0),
        "private_mb_growth_per_pass": per_pass,
        "private_mb_by_pass": [round(e.private_mb, 1) for e in ends],
        "rss_mb_by_pass": [round(e.rss_mb, 1) for e in ends],
        "thread_growth": threads,
        "handle_growth": settled[-1].handles - settled[0].handles if len(settled) > 1 else 0,
    }


#: Soak passes left out of the memory trend while caches fill.
SOAK_WARMUP = 2


def _slope(ys: list[float]) -> float:
    """Change per step, robust to outliers: the median pairwise slope."""
    pairs = [(ys[j] - ys[i]) / (j - i) for i, j in itertools.combinations(range(len(ys)), 2)]
    return statistics.median(pairs) if pairs else 0.0


def summarise_idle(sampler: Sampler) -> dict[str, Any]:
    s = sampler.phase_samples("idle")[2:]  # let the last session wind down
    cpu = _cpu_percents(s)
    span = s[-1].t - s[0].t if len(s) > 1 else 0.0
    out: dict[str, Any] = {
        "cpu_percent_mean": statistics.fmean(cpu) if cpu else 0.0,
        "private_mb": s[-1].private_mb if s else 0.0,
    }
    if sys.platform == "darwin" and span:
        out["wakeups_per_second"] = (s[-1].wakeups - s[0].wakeups) / span
    return out


# ------------------------------------------------------------- expectations

#: name -> (label, unit, worse when "higher"/"lower", warn ratio, fail ratio)
#: Ratios are against the expected value. Fails are for extreme outliers only.
METRICS: dict[str, tuple[str, str, str, float, float]] = {
    "startup_seconds": ("Engine start", "s", "higher", 2.0, 6.0),
    "model_load_seconds": ("Speech model load", "s", "higher", 2.0, 8.0),
    "live.cpu_percent_mean": ("CPU while following (mean)", "% of a core", "higher", 1.4, 4.0),
    "live.cpu_percent_p95": ("CPU while following (p95)", "% of a core", "higher", 1.4, 4.0),
    "live.private_mb_peak": ("Memory while following (peak)", "MB", "higher", 1.25, 3.0),
    "live.asr_ms_p95": ("Speech pass (p95)", "ms", "higher", 1.5, 4.0),
    "live.matcher_ms_p95": ("Matcher update (p95)", "ms", "higher", 1.5, 6.0),
    "live.lag_seconds_max": ("Worst lag behind the audio", "s", "higher", 1.5, 5.0),
    "live.slides_on_time": ("Slides on time", "", "lower", 0.85, 0.3),
    "live.follower_ms_per_s": ("Music follower (ms per audio second)", "ms/s", "higher", 1.5, 6.0),
    "live.follower_frame_ms_p95": ("Music follower, slow frame (p95)", "ms", "higher", 2.0, 10.0),
    "live.chord_ms_per_s": ("Chord recognition (ms per audio second)", "ms/s", "higher", 1.5, 6.0),
    "live.chord_frame_ms_p95": ("Chord recognition, slow frame (p95)", "ms", "higher", 2.0, 10.0),
    "soak.speed_x_realtime": ("Engine speed", "x real time", "lower", 0.7, 0.25),
    "soak.private_mb_peak": ("Memory during soak (peak)", "MB", "higher", 1.25, 3.0),
    "idle.cpu_percent_mean": ("CPU when idle", "% of a core", "higher", 3.0, 20.0),
    "idle.private_mb": ("Memory when idle", "MB", "higher", 1.25, 3.0),
    "idle.wakeups_per_second": ("Wakeups when idle (energy)", "/s", "higher", 3.0, 20.0),
}

#: Checks that don't need an expectation: (path, label, warn above, fail above).
ABSOLUTE: list[tuple[str, str, float, float]] = [
    ("live.dropped_chunks", "Audio dropped while following (chunks)", 0, 50),
    ("idle.cpu_percent_mean", "CPU when idle, any machine (% of a core)", 5, 200),
    ("live.wrong_song_moves", "Jumps to the wrong song", 0, 3),
    ("soak.private_mb_growth_per_pass", "Memory growth per soak pass (MB)", 15, 150),
    ("soak.thread_growth", "Threads left over after soak", 2, 50),
    ("soak.handle_growth", "Handles left over after soak", 20, 500),
]


def get(d: dict[str, Any], path: str) -> Any:
    for part in path.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def live_ai_offered(result: dict[str, Any]) -> bool:
    return get(result, "machine_check.verdict") in (None, "full")


def judge(result: dict[str, Any], expected: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    check = result.get("machine_check")
    offered = live_ai_offered(result)
    if check:
        load = check.get("speech_load")
        rows.append(
            {
                "metric": "machine_check.verdict",
                "label": "Live AI offered (machine check)",
                "unit": "",
                "value": ("yes" if offered else "no")
                + (f", load {load:.2f}" if load is not None else ""),
                "expected": None,
                "status": "ok",
            }
        )
    for path, (label, unit, worse, warn, fail) in METRICS.items():
        value = get(result, path)
        if value is None or (not offered and path.startswith("live.")):
            continue
        exp = get(expected or {}, path)
        status = "ok"
        if exp is None:
            if expected is not None:
                status = "new"
        elif exp > 0:  # a ratio to ~0 (idle CPU) means nothing; ABSOLUTE covers those
            ratio = value / exp
            if worse == "higher":
                status = "fail" if ratio >= fail else "warn" if ratio >= warn else "ok"
            else:
                status = "fail" if ratio <= fail else "warn" if ratio <= warn else "ok"
        rows.append(
            {
                "metric": path,
                "label": label,
                "unit": unit,
                "value": value,
                "expected": exp,
                "status": status,
            }
        )
    for path, label, warn, fail in ABSOLUTE:
        value = get(result, path)
        if value is None or (not offered and path.startswith("live.")):
            continue
        status = "fail" if value > fail else "warn" if value > warn else "ok"
        rows.append(
            {
                "metric": path,
                "label": label,
                "unit": "",
                "value": value,
                "expected": warn,
                "status": status,
            }
        )
    return rows


# -------------------------------------------------------------------- report

ICON = {"ok": "ok", "warn": "WARN", "fail": "FAIL", "new": "new"}


def fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}" if abs(v) < 10 else f"{v:.0f}"
    return str(v)


def markdown(report: dict[str, Any]) -> str:
    r = report["result"]
    lines = [
        f"### Engine performance: {report['label']}",
        "",
        f"{report['machine']} · engine `{report['engine']}` · model {r.get('model', '?')}",
        "",
        "| | Measured | Expected | |",
        "|---|---:|---:|---|",
    ]
    for row in report["checks"]:
        unit = f" {row['unit']}" if row["unit"] else ""
        lines.append(
            f"| {row['label']} | {fmt(row['value'])}{unit} | {fmt(row['expected'])} | {ICON[row['status']]} |"
        )
    live = r.get("live")
    if not live_ai_offered(r):
        lines += ["", f"Live AI isn't offered on this machine: {r['machine_check']['reason']}"]
    elif live:
        lines += [
            "",
            f"Slides on time {live['slides_on_time']}/{live['slides_expected']}, "
            f"median delay {fmt(live['slide_delay_median_s'])} s, {live['ai_moves']} AI moves.",
        ]
    if r.get("session_errors"):
        lines += ["", "Session errors: " + "; ".join(r["session_errors"][:5])]
    warns = [c for c in report["checks"] if c["status"] in ("warn", "fail")]
    lines += ["", f"**{len(warns)} warning(s)**" if warns else "No warnings."]
    return "\n".join(lines) + "\n"


def terminal(report: dict[str, Any]) -> str:
    colour = {"warn": "\033[33m", "fail": "\033[31m"}
    out = [
        f"\n\033[1mEngine performance: {report['label']}\033[0m  ({report['result'].get('model', '?')})"
    ]
    for row in report["checks"]:
        c = colour.get(row["status"], "")
        reset = "\033[0m" if c else ""
        exp = f"(expected {fmt(row['expected'])})" if row["expected"] is not None else ""
        out.append(
            f"  {c}{ICON[row['status']]:>4}  {row['label']:<40} {fmt(row['value']):>9} {row['unit']:<12} {exp}{reset}"
        )
    return "\n".join(out)


def machine() -> str:
    cpu = platform.processor() or platform.machine()
    try:
        if sys.platform == "darwin":
            cpu = (
                subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
                ).stdout.strip()
                or cpu
            )
    except OSError:
        pass
    ram = psutil.virtual_memory().total / 2**30
    desc = f"{platform.system()} {platform.release()} · {cpu} · {psutil.cpu_count()} cores · {ram:.0f} GB"
    return desc + power_note()


def power_note() -> str:
    """The power state, when it would skew the numbers: Low Power Mode or a
    near-empty battery slows the engine several-fold (2026-10-14: below real
    time, matcher p95 8 -> 900 ms), and a report should say so up front."""
    try:
        from auto_presenter.pipeline.power import read_power_state
    except ImportError:
        return ""
    state = read_power_state()
    if state.low_power_mode:
        return " · LOW POWER MODE (numbers not comparable)"
    if state.battery_low:
        return f" · battery {state.battery_percent}% (numbers not comparable)"
    return ""


# ---------------------------------------------------------------------- main


#: What the changelog card shows, per platform: (path, label, unit).
CARD = [
    ("live.cpu_percent_mean", "CPU while following", "% of a core"),
    ("live.private_mb_peak", "Memory (peak)", "MB"),
    ("live.asr_ms_p95", "Speech pass (p95)", "ms"),
    ("soak.speed_x_realtime", "Engine speed", "x real time"),
]


def merge(dest: Path, reports: list[Path]) -> None:
    """Fold full reports into a release's compact perf.json."""
    data = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else {}
    for path in reports:
        rep = json.loads(path.read_text(encoding="utf-8"))
        r = rep["result"]
        live = r.get("live") or {}
        data[rep["label"]] = {
            "machine": rep["machine"],
            "model": r.get("model"),
            "quick": rep.get("quick", False),
            "stats": {path_: get(r, path_) for path_, _, _ in CARD},
            "slides_on_time": [live["slides_on_time"], live["slides_expected"]] if live else None,
            "warnings": [c["label"] for c in rep["checks"] if c["status"] in ("warn", "fail")],
            "error": rep.get("error"),
        }
    dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def adopt(path: Path) -> None:
    rep = json.loads(path.read_text(encoding="utf-8"))
    if rep.get("error") or not rep.get("result"):
        raise SystemExit(f"{path} is from a failed run; not adopting it")
    expected_all = json.loads(EXPECTED.read_text(encoding="utf-8")) if EXPECTED.exists() else {}
    expected_all[rep["label"]] = _expectation(rep)
    EXPECTED.write_text(json.dumps(expected_all, indent=2) + "\n", encoding="utf-8")
    print(f"Expectations for {rep['label']} now come from {path}")


def _expectation(report: dict[str, Any]) -> dict[str, Any]:
    result = report["result"]
    keep = {k: v for k, v in result.items() if k not in ("session_errors", "model")}
    return {
        **keep,
        "model": result.get("model"),
        "machine": report["machine"],
        "recorded": time.strftime("%Y-%m-%d"),
    }


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "merge":
        merge(Path(sys.argv[2]), [Path(p) for p in sys.argv[3:]])
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "adopt":
        adopt(Path(sys.argv[2]))
        return 0
    global psutil, websockets
    import psutil as _psutil
    import websockets as _websockets

    psutil, websockets = _psutil, _websockets
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--engine", default="dev", help="the frozen engine binary, or 'dev' for the source"
    )
    ap.add_argument(
        "--label",
        required=True,
        help="which expectations to compare with, e.g. macos-apple-silicon",
    )
    ap.add_argument("--quick", action="store_true", help="skip the real-time phase")
    ap.add_argument(
        "--soak", type=int, default=12, help="soak passes, the first two a warm-up (default 12)"
    )
    ap.add_argument(
        "--soak-seconds", type=float, default=20.0, help="audio per soak pass (default 20)"
    )
    ap.add_argument("--idle", type=float, default=6.0, help="idle seconds at the end")
    ap.add_argument("--backend", help="override the engine's recommended backend")
    ap.add_argument(
        "--tunable",
        action="append",
        metavar="KEY=VALUE",
        help="session tunable override (repeatable), e.g. score_follower_cpu=1",
    )
    ap.add_argument("--model", help="override the engine's recommended model")
    ap.add_argument("--out", type=Path, help="write perf-<label>.json and .md here")
    ap.add_argument(
        "--update-expected", action="store_true", help="record this run as what to expect"
    )
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        args.tmp = tmp
        engine = Engine(args.engine, Path(tmp) / "library.db")
        sampler = Sampler(engine.proc.pid)
        sampler.start()
        error = None
        try:
            result = asyncio.run(drive(engine, sampler, args))
        except Exception as exc:  # the report still goes out, as a failure
            error = f"{type(exc).__name__}: {exc}"
            result = {}
        finally:
            crashed = engine.crashed()
            sampler.stop()
            engine.stop()
    if crashed and not error:
        error = f"the engine exited during the run (code {engine.proc.returncode})"
    if error and engine.stderr_tail:
        error += "\n" + "\n".join(engine.stderr_tail[-10:])

    expected_all = json.loads(EXPECTED.read_text(encoding="utf-8")) if EXPECTED.exists() else {}
    expected = expected_all.get(args.label)
    report = {
        "label": args.label,
        "machine": machine(),
        "engine": args.engine if args.engine == "dev" else Path(args.engine).name,
        "quick": args.quick,
        "result": result,
        "error": error,
        "checks": judge(result, expected) if result else [],
    }
    if error:
        report["checks"].append(
            {
                "metric": "run",
                "label": "Run completed",
                "unit": "",
                "value": error.splitlines()[0],
                "expected": None,
                "status": "fail",
            }
        )

    print(terminal(report))
    if error:
        print(f"\n\033[31m{error}\033[0m")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"perf-{args.label}.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        (args.out / f"perf-{args.label}.md").write_text(markdown(report), encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(markdown(report))

    if args.update_expected and result and not error:
        expected_all[args.label] = _expectation(report)
        EXPECTED.write_text(json.dumps(expected_all, indent=2) + "\n", encoding="utf-8")
        print(f"\nRecorded as the expectation for {args.label} in {EXPECTED.relative_to(ROOT)}")

    return 1 if any(c["status"] == "fail" for c in report["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
