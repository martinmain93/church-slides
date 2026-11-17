"""App-level scenarios for scripts/perf_suite.py: the whole app, UI included.

Drives "Church Slides Perf" (scripts/build_perf_app.sh: an optimised build
with the agent UI driver, its own name and data folder) the way an operator
does — clicking through the screens — and measures what the computer sees:

  app     the app's own process (Rust: windows, library, band server)
  engine  the AI engine it starts
  ui      the WebKit processes that draw the windows (attributed to the app
          by macOS's "responsible process", so other apps' WebKit is left out)

Each run starts from a fresh copy of your library, Bibles, search index and
hymns in the perf app's own folder (speech and search models are linked, not
copied), so nothing a scenario does reaches your real library.

    scripts/build_perf_app.sh        # once, and after changing the app
    uv run --project sidecar python scripts/perf_suite.py run app.search
    uv run --project sidecar python scripts/perf_suite.py run app --size full

The perf app opens its own windows (app.service: the operator view and an
audience window), so run these when you're not using the Mac. app.service
never opens the microphone: the app's own AI is turned off in the perf copy
and the engine's session is started from the recording.

UI times are measured in the page: from the action to its result being
painted (two animation frames later). "UI stall" is how late the page's own
100 ms timer ran — a busy main thread the operator would feel. It isn't
reported for app.idle: WebKit slows a quiet page's timers on purpose.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import json
import shutil
import sqlite3
import statistics
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import perf_report as pr
from perf_suite import APP_DATA, ROOT, Result

PERF_APP = ROOT / "build" / "perf-app" / "Church Slides Perf.app"
PERF_DATA = APP_DATA.parent / "com.churchslides.app.perf"
APP_NAME = "Church Slides Perf"
BRIDGE_PORTS = range(9223, 9243)


# ---------------------------------------------------------------- data + launch


def seed_data() -> None:
    """A fresh copy of the library and its companions in the perf app's folder."""
    if PERF_DATA.exists():
        for p in PERF_DATA.iterdir():
            if p.is_symlink() or p.is_file():
                p.unlink()
            else:
                shutil.rmtree(p)
    PERF_DATA.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{APP_DATA / 'library.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(PERF_DATA / "library.db")
    src.backup(dst)
    src.close()
    dst.close()
    for sub in ("scripture", "search", "hymns"):
        if (APP_DATA / sub).is_dir():
            shutil.copytree(APP_DATA / sub, PERF_DATA / sub)
    if (APP_DATA / "models").is_dir():
        (PERF_DATA / "models").symlink_to(APP_DATA / "models")


def _responsible() -> Callable[[int], int]:
    f = ctypes.CDLL(None).responsibility_get_pid_responsible_for_pid
    f.restype, f.argtypes = ctypes.c_int, [ctypes.c_int]
    return f


@dataclass
class GroupSample:
    t: float
    phase: str
    mb: dict[str, float]  # footprint by group
    cpu: dict[str, float]  # cumulative CPU seconds by group


class AppSampler:
    """Footprint and CPU of the app, its engine and its WebKit processes."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.phase = "startup"
        self.samples: list[GroupSample] = []
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self._resp = _responsible()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)

    def _groups(self) -> dict[str, list[Any]]:
        psutil = pr.psutil
        root = psutil.Process(self.pid)
        engine = root.children(recursive=True)
        ui = []
        for p in psutil.process_iter(["name"]):
            if "WebKit" in (p.info["name"] or "") and self._resp(p.pid) == self.pid:
                ui.append(p)
        return {"app": [root], "engine": engine, "ui": ui}

    def _run(self) -> None:
        psutil = pr.psutil
        while not self._stop.is_set():
            try:
                groups = self._groups()
            except psutil.NoSuchProcess:
                return
            mb, cpu = {}, {}
            for name, procs in groups.items():
                m = c = 0.0
                for p in procs:
                    try:
                        with p.oneshot():
                            t = p.cpu_times()
                            c += t.user + t.system
                            m += pr._private_and_wakeups(p)[0] / 1e6
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                mb[name], cpu[name] = m, c
            self.samples.append(GroupSample(time.monotonic() - self._t0, self.phase, mb, cpu))
            self._stop.wait(0.5)

    def now(self) -> dict[str, float]:
        return dict(self.samples[-1].mb) if self.samples else {}

    def total(self) -> float:
        return sum(self.now().values())

    def cpu_percent(self, phase: str) -> dict[str, float]:
        s = [x for x in self.samples if x.phase == phase]
        if len(s) < 2 or s[-1].t <= s[0].t:
            return {}
        dt = s[-1].t - s[0].t
        return {g: 100 * (s[-1].cpu.get(g, 0) - s[0].cpu.get(g, 0)) / dt for g in s[-1].cpu}


class Bridge:
    """The perf app's UI driver (tauri-plugin-mcp-bridge's WebSocket)."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.n = 0
        self.lock = asyncio.Lock()

    @classmethod
    async def connect(cls, timeout: float = 60) -> Bridge:
        import websockets

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for port in BRIDGE_PORTS:
                try:
                    ws = await asyncio.wait_for(websockets.connect(f"ws://127.0.0.1:{port}", max_size=None), 1)
                except (OSError, asyncio.TimeoutError):
                    continue
                b = cls(ws)
                try:
                    state = await b.call("invoke_tauri", {"command": "plugin:mcp-bridge|get_backend_state"}, 5)
                    if (state.get("data") or {}).get("app", {}).get("name") == APP_NAME:
                        return b
                except Exception:
                    pass
                await ws.close()
            await asyncio.sleep(1)
        raise RuntimeError(f"no UI driver for {APP_NAME} on ports {BRIDGE_PORTS.start}-{BRIDGE_PORTS.stop - 1}")

    async def call(self, command: str, args: dict[str, Any] | None = None, timeout: float = 60) -> dict[str, Any]:
        async with self.lock:
            self.n += 1
            rid = f"perf-{self.n}"
            await self.ws.send(json.dumps({"id": rid, "command": command, "args": args or {}}))
            deadline = time.monotonic() + timeout
            while True:
                raw = await asyncio.wait_for(self.ws.recv(), max(0.1, deadline - time.monotonic()))
                msg = json.loads(raw)
                if msg.get("id") == rid:
                    return msg

    async def js(self, script: str, window: str = "main", timeout: float = 60) -> Any:
        """Run `script` in a window; its value (awaited if it uses await).

        The driver evaluates synchronous scripts directly, but gives an async
        one only 5 s and drops its value. So async work runs as a job in the
        page (`__perf.start`, handed over base64 so the driver sees no
        `await` and stays on the direct path) and is polled until done.
        """
        if any(k in script for k in ("await ", "async ", ".then(", "Promise")):
            self.n += 1
            job = f"j{self.n}"
            b64 = base64.b64encode(script.encode()).decode()
            await self._direct(f'return window.__perf.start("{job}", "{b64}");', window, 10)
            deadline = time.monotonic() + timeout
            while True:
                st = await self._direct(f'return window.__perf.poll("{job}");', window, 10)
                if st and st.get("done"):
                    if st.get("error"):
                        raise RuntimeError(f"js error: {st['error']}\n{script[:300]}")
                    return st.get("value")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"js job ran over {timeout:.0f}s\n{script[:300]}")
                await asyncio.sleep(0.05)
        return await self._direct(script, window, timeout)

    async def install_helpers(self, window: str = "main") -> None:
        b64 = base64.b64encode(HELPERS.encode()).decode()
        await self._direct(
            f'eval(new TextDecoder().decode(Uint8Array.from(atob("{b64}"), (c) => c.charCodeAt(0)))); return true;',
            window, 10)

    async def _direct(self, script: str, window: str, timeout: float) -> Any:
        msg = await self.call("execute_js", {"script": script, "windowLabel": window}, timeout)
        if not msg.get("success", True) or msg.get("error"):
            raise RuntimeError(f"js failed: {msg.get('error')}\n{script[:300]}")
        data = msg.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                pass
        if isinstance(data, dict) and "__mcp_error__" in data:
            raise RuntimeError(f"js error: {data['__mcp_error__']}\n{script[:300]}")
        return data


# Page helpers, run in the app: find by test id or visible text, click, type.
HELPERS = r"""
window.__perf = {
  byTestId(id) { return document.querySelector(`[data-testid="${id}"]`); },
  byText(text, sel = "button, [role=button], a, [role=tab], [role=menuitem]") {
    const want = text.toLowerCase();
    return [...document.querySelectorAll(sel)].find(
      (e) => e.offsetParent !== null && (e.textContent || "").trim().toLowerCase() === want)
      || [...document.querySelectorAll(sel)].find(
      (e) => e.offsetParent !== null && (e.getAttribute("aria-label") || "").toLowerCase() === want)
      || [...document.querySelectorAll(sel)].find(
      (e) => e.offsetParent !== null && (e.textContent || "").trim().toLowerCase().startsWith(want));
  },
  click(el) {
    if (!el) throw new Error("nothing to click");
    // A plain click: synthetic pointerdowns read as "outside" to the app's
    // menus and closed them as they opened.
    el.scrollIntoView({ block: "center" });
    el.click();
    return true;
  },
  type(el, text) {
    if (!el) throw new Error("nothing to type into");
    el.focus();
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, text);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  },
  key(key, el = document.activeElement || document.body) {
    for (const t of ["keydown", "keyup"]) {
      el.dispatchEvent(new KeyboardEvent(t, { key, bubbles: true, cancelable: true }));
    }
    return true;
  },
  async until(fn, ms = 10000) {
    const t0 = performance.now();
    while (performance.now() - t0 < ms) {
      const v = fn();
      if (v) return v;
      await new Promise((r) => setTimeout(r, 50));
    }
    throw new Error("timed out waiting");
  },
  // Wait for the next two frames: the change is on screen.
  async painted() {
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    return true;
  },
  heapMB() { return performance.memory ? performance.memory.usedJSHeapSize / 1e6 : null; },
  // How late the page's own timers run: a stalled main thread (a slow
  // render, a long handler) shows here as lag the operator would feel.
  lag: { max: 0, samples: [] },
  watchLag() {
    if (this._lagTimer) return true;
    let last = performance.now();
    this._lagTimer = setInterval(() => {
      const now = performance.now();
      const late = now - last - 100;
      last = now;
      this.lag.max = Math.max(this.lag.max, late);
      this.lag.samples.push(late);
      if (this.lag.samples.length > 20000) this.lag.samples.shift();
    }, 100);
    return true;
  },
  takeLag() {
    const s = [...this.lag.samples].sort((a, b) => a - b);
    const out = { max: this.lag.max, p95: s.length ? s[Math.floor(0.95 * (s.length - 1))] : 0,
                  over100: s.filter((x) => x > 100).length };
    this.lag = { max: 0, samples: [] };
    return out;
  },
  songRows() {
    return [...document.querySelectorAll("button")].filter(
      (b) => b.offsetParent !== null && / slides?$/.test((b.textContent || "").trim()));
  },
  async dismissWizard() {
    const skip = this.byText("skip") || this.byText("close setup");
    if (skip) { this.click(skip); await new Promise((r) => setTimeout(r, 300)); }
    return true;
  },
  jobs: {},
  start(id, b64) {
    const src = new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const job = (this.jobs[id] = { done: false });
    new AsyncFunction(src)().then(
      (value) => Object.assign(job, { done: true, value }),
      (e) => Object.assign(job, { done: true, error: String(e) + " | " + ((e && e.stack) || "").split("\\n")[0] }),
    );
    return true;
  },
  poll(id) {
    const job = this.jobs[id];
    if (job && job.done) delete this.jobs[id];
    return job || { done: false };
  },
  domNodes() { return document.getElementsByTagName("*").length; },
};
true
"""


@dataclass
class AppCtx:
    args: argparse.Namespace
    bridge: Bridge
    sampler: AppSampler
    result: Result
    proc: Any  # psutil.Process
    marks: dict[str, float] = field(default_factory=dict)

    async def js(self, script: str, window: str = "main", timeout: float = 60) -> Any:
        return await self.bridge.js(script, window, timeout)

    async def timed(self, script: str, timeout: float = 60) -> float:
        """Run an async `script` (it should await its own result being on
        screen) and return how long it took, in ms, measured in the page."""
        ms = await self.js(
            "const __t0 = performance.now(); "
            f"await (async () => {{ {script} }})(); "
            "await window.__perf.painted(); "
            "return performance.now() - __t0;",
            timeout=timeout,
        )
        return float(ms)

    async def ui_stats(self) -> dict[str, float]:
        return await self.js("return { nodes: window.__perf.domNodes() };")

    def mem(self) -> dict[str, float]:
        return self.sampler.now()


async def run_app(args: argparse.Namespace, scenario: str, body: Callable[[AppCtx], Awaitable[None]]) -> Result:
    import psutil

    pr.psutil = psutil
    result = Result(scenario, args.size)
    app = Path(args.app) if args.app else PERF_APP
    exe_dir = app / "Contents" / "MacOS"
    if not exe_dir.is_dir():
        raise SystemExit(f"no perf app at {app}: build it with scripts/build_perf_app.sh")
    exe = next(p for p in exe_dir.iterdir() if p.is_file() and not p.name.startswith("."))
    seed_data()
    # No App Nap for the perf app (its own preferences, not the system's):
    # a napping app's timers fire up to a second late and every UI time
    # would be the nap's.
    subprocess.run(["defaults", "write", "com.churchslides.app.perf", "NSAppSleepDisabled", "-bool", "YES"], check=False)
    # Through LaunchServices, as a person opens it: started from this script
    # instead, macOS makes the terminal its "responsible process", and its
    # WebKit processes can't be told apart from anyone else's.
    subprocess.run(["open", "-n", str(app)], check=True)
    proc = None
    for _ in range(100):
        found = [p for p in psutil.process_iter(["exe"]) if (p.info["exe"] or "") == str(exe.resolve())]
        if found:
            proc = found[0]
            break
        time.sleep(0.1)
    if proc is None:
        raise RuntimeError(f"{app.name} didn't start")
    sampler = AppSampler(proc.pid)
    sampler.start()
    t0 = time.monotonic()
    try:
        bridge = await Bridge.connect(90)
        for attempt in range(60):  # the driver is up before the window is
            try:
                await bridge.install_helpers()
                break
            except RuntimeError:
                if attempt == 59:
                    raise
                await asyncio.sleep(0.5)
        # The engine is up when the app reports its port.
        await bridge.js(
            "return await window.__perf.until(() => window.__TAURI__ && true, 30000);", timeout=40
        )
        result.add("app start (driver up)", time.monotonic() - t0, "s")
        vis = await bridge.js("return document.visibilityState;")
        if vis != "visible":
            result.notes.append(f"the window was {vis}: UI timings are throttled — run with the perf app in front")
        ctx = AppCtx(args, bridge, sampler, result, proc)
        sampler.phase = "settle"
        await asyncio.sleep(5)
        base = sampler.now()
        for g, v in base.items():
            result.add(f"memory at start: {g}", v, "MB")
        await body(ctx)
        end = sampler.now()
        for g, v in end.items():
            result.add(f"memory at end: {g}", v, "MB")
        result.add("memory at end: total", sum(end.values()), "MB")
        for g in ("app", "engine", "ui"):
            peak = max((s.mb.get(g, 0) for s in sampler.samples), default=0)
            result.add(f"memory peak: {g}", peak, "MB")
    finally:
        sampler.stop()
        crashed = not proc.is_running()
        if not crashed:
            proc.terminate()
            try:
                proc.wait(15)
            except psutil.TimeoutExpired:
                proc.kill()
        if crashed:
            result.notes.append("APP EXITED during the run")
    return result


def rounds(ctx_or_args: Any, quick: int, full: int) -> int:
    args = getattr(ctx_or_args, "args", ctx_or_args)
    return quick if args.size == "quick" else full


def summarise_round_memory(ctx: AppCtx, name: str, per_round: list[dict[str, float]]) -> None:
    for g in ("app", "engine", "ui"):
        ctx.result.growth(f"{name} memory ({g})", [r.get(g, 0) for r in per_round], "MB")
    nodes = [r.get("nodes", 0) for r in per_round if "nodes" in r]
    if nodes:
        ctx.result.growth(f"{name} DOM nodes", nodes, "nodes")


SCENARIOS: dict[str, tuple[Callable, str]] = {}


def scenario(name: str, about: str):
    def wrap(fn: Callable[[AppCtx], Awaitable[None]]):
        async def run(args: argparse.Namespace, scenario_name: str) -> Result:
            return await run_app(args, scenario_name, fn)

        SCENARIOS[name] = (run, about)
        return fn

    return wrap


def _cpu_note(ctx: AppCtx, phase: str) -> None:
    for g, v in ctx.sampler.cpu_percent(phase).items():
        ctx.result.add(f"CPU ({phase}): {g}", v, "% core")


async def wait_js(ctx: AppCtx, script: str, timeout: float = 30) -> Any:
    return await ctx.js(script, timeout=timeout)


async def go_home(ctx: AppCtx) -> None:
    await ctx.js('const P=window.__perf; await P.until(() => P.byText("Home"), 30000); '
                 'await P.dismissWizard(); P.click(P.byText("Home")); '
                 'await P.until(() => P.byText("Start from last service") || P.byText("New setlist")); return true;')


SETLIST = "Sunday 4 Oct"
SONG = "Build My LIfe"


def _open_setlist_js(name: str = SETLIST) -> str:
    return (f'const P=window.__perf; const row=[...document.querySelectorAll("button")].find('
            f'b=>b.offsetParent!==null && (b.textContent||"").includes({json.dumps(name)})); P.click(row); '
            'await P.until(() => P.byTestId("setlist-workspace"));')


async def lag_metrics(ctx: AppCtx, name: str) -> None:
    lag = await ctx.js("return window.__perf.takeLag();")
    ctx.result.add(f"{name}: UI stall, worst", lag["max"], "ms")
    ctx.result.add(f"{name}: UI stall, p95", lag["p95"], "ms")


# ------------------------------------------------------------------ scenarios


@scenario("app.idle", "the app left open on Home: CPU and memory while nothing happens")
async def app_idle(ctx: AppCtx) -> None:
    await go_home(ctx)
    # The engine warms the song-search index 30 s after it starts
    # (server_library_search.WARM_DELAY_S): measure after that, not it.
    await asyncio.sleep(35)
    ctx.sampler.phase = "idle"
    seconds = rounds(ctx, 30, 600)
    mem = []
    for _ in range(max(1, seconds // 10)):
        await asyncio.sleep(10)
        mem.append(ctx.mem())
    _cpu_note(ctx, "idle")
    summarise_round_memory(ctx, "idle (per 10 s)", mem)
    # No stall figure: WebKit deliberately slows a quiet page's timers to
    # about one a second to save power, so lateness here measures that.


@scenario("app.navigate", "Home, library, a setlist and the song editor, round after round")
async def app_navigate(ctx: AppCtx) -> None:
    await go_home(ctx)
    await ctx.js("return window.__perf.watchLag();")
    ctx.sampler.phase = "navigate"
    times: dict[str, list[float]] = {"library": [], "setlist": [], "song editor": [], "home": []}
    per_round = []
    for _ in range(rounds(ctx, 4, 40)):
        times["library"].append(await ctx.timed(
            'const P=window.__perf; P.click(P.byText("Library")); await P.until(() => P.songRows().length > 20);'))
        times["home"].append(await ctx.timed(
            'const P=window.__perf; P.click(P.byText("Home")); await P.until(() => P.byText("New setlist"));'))
        times["setlist"].append(await ctx.timed(_open_setlist_js()))
        times["song editor"].append(await ctx.timed(
            'const P=window.__perf; P.click(document.querySelector("[aria-label=\\"Edit song\\"]")); '
            'await P.until(() => P.byTestId("song-body"));'))
        await go_home(ctx)
        per_round.append({**ctx.mem(), **(await ctx.ui_stats())})
    for k, v in times.items():
        ctx.result.latencies(f"open {k}", v)
    summarise_round_memory(ctx, "navigation", per_round)
    _cpu_note(ctx, "navigate")
    await lag_metrics(ctx, "navigate")


APP_SEARCHES = [
    "songs about victory in Jesus", "good songs for Easter", "hymns about the blood of Jesus",
    "songs by Chris Tomlin", "fast songs", "songs in 6/8", "songs that fit with How Great Thou Art",
    "amazing grace", "10,000 Reasons", "worthy of every song",
]


@scenario("app.search", "library search: topics, titles, lyrics and filters typed into the box")
async def app_search(ctx: AppCtx) -> None:
    await go_home(ctx)
    await ctx.js('const P=window.__perf; P.click(P.byText("Library")); await P.until(() => P.songRows().length > 20); P.watchLag(); return true;')
    ctx.sampler.phase = "search"
    ms, per_round = [], []
    for _ in range(rounds(ctx, 1, 12)):
        for q in APP_SEARCHES:
            for text in ("", q):  # clear, then the query: each search changes the list
                ms_one = await ctx.timed(
                    'const P=window.__perf; const box=document.querySelector("input[placeholder^=\\"Search songs\\"]"); '
                    'const top=() => (P.songRows()[0]||{}).textContent || ""; const before=top(); '
                    f'P.type(box, {json.dumps(text)}); '
                    'await P.until(() => top() !== before || document.body.innerText.includes("No songs"), 15000).catch(() => null);',
                    timeout=30)
                if text:
                    ms.append(ms_one)
        per_round.append({**ctx.mem(), **(await ctx.ui_stats())})
    ctx.result.latencies("search to results on screen", ms)
    summarise_round_memory(ctx, "search", per_round)
    _cpu_note(ctx, "search")
    await lag_metrics(ctx, "search")


APP_SCRIPTURE = ["John 3:16", "Romans 8:28-39", "love one another", "the Lord is my shepherd",
                 "Psalm 23:1-6", "faith hope and love"]
PASTOR = ("This week we are reading John 3:16-18 together, and then Romans 8:28 and "
          "Philippians 4:6-7. Next week, Isaiah 40:31 and Hebrews 11:1.")


@scenario("app.scripture", "the setlist's Scripture tab: references, words and a pasted message")
async def app_scripture(ctx: AppCtx) -> None:
    await go_home(ctx)
    await ctx.js(_open_setlist_js() + ' P.click(P.byTestId("setlist-tab-scripture")); '
                 'await P.until(() => P.byTestId("live-scripture-input")); P.watchLag(); return true;')
    ctx.sampler.phase = "scripture"
    ms, pastor_ms, per_round = [], [], []
    for _ in range(rounds(ctx, 1, 15)):
        for q in [*APP_SCRIPTURE, PASTOR]:
            for text in ("", q):
                one = await ctx.timed(
                    'const P=window.__perf; const box=P.byTestId("live-scripture-input"); '
                    'const top=() => (P.byTestId("live-scripture-result")||{}).innerText || ""; const before=top(); '
                    f'P.type(box, {json.dumps(text)}); '
                    'await P.until(() => top() !== before, 15000).catch(() => null);', timeout=30)
                if text:
                    (pastor_ms if q is PASTOR else ms).append(one)
        per_round.append({**ctx.mem(), **(await ctx.ui_stats())})
    ctx.result.latencies("scripture search to results", ms)
    ctx.result.latencies("pasted message to passages", pastor_ms)
    summarise_round_memory(ctx, "scripture", per_round)
    _cpu_note(ctx, "scripture")
    await lag_metrics(ctx, "scripture")


@scenario("app.chords", "the chord editor: show chords, set chords on words, transpose, notation")
async def app_chords(ctx: AppCtx) -> None:
    await go_home(ctx)
    await ctx.js(_open_setlist_js() + ' P.click(document.querySelector("[aria-label=\\"Edit song\\"]")); '
                 'await P.until(() => P.byTestId("song-body")); P.watchLag(); return true;')
    ctx.sampler.phase = "chords"
    show, pick, setc, transpose, per_round = [], [], [], [], []
    for r in range(rounds(ctx, 2, 15)):
        show.append(await ctx.timed(
            'const P=window.__perf; if (!document.querySelector("[data-testid=chord-word-btn]")) P.click(P.byTestId("toggle-chords")); '
            'await P.until(() => document.querySelectorAll("[data-testid=chord-word-btn]").length > 10);'))
        for i in range(8):
            word = 3 + 7 * i + r
            pick.append(await ctx.timed(
                f'const P=window.__perf; P.click(document.querySelectorAll("[data-testid=chord-word-btn]")[{word}]); '
                'await P.until(() => P.byTestId("chord-picker-popover"));'))
            chord = ["C", "D", "Em", "G"][i % 4]
            setc.append(await ctx.timed(
                f'const P=window.__perf; P.click(document.querySelector(\'[data-testid="chord-quick-{chord}"]\')); '
                'await P.until(() => !P.byTestId("chord-picker-popover"));'))
        for direction in ("up", "up", "down", "down"):
            b = await ctx.js(f'return !!window.__perf.byTestId("transpose-{direction}");')
            if b:
                transpose.append(await ctx.timed(
                    f'const P=window.__perf; P.click(P.byTestId("transpose-{direction}")); await P.painted();'))
        await ctx.js('const P=window.__perf; P.click(P.byTestId("toggle-chords")); await P.painted(); return true;')
        per_round.append({**ctx.mem(), **(await ctx.ui_stats())})
    ctx.result.latencies("show chords", show)
    ctx.result.latencies("open the chord picker", pick)
    ctx.result.latencies("set a chord", setc)
    ctx.result.latencies("transpose", transpose)
    if not transpose:
        ctx.result.notes.append("no transpose buttons found in the chord view")
    summarise_round_memory(ctx, "chord editing", per_round)
    _cpu_note(ctx, "chords")
    await lag_metrics(ctx, "chords")


@scenario("app.service", "a service in the operator view, fed a live recording, AI on Auto")
async def app_service(ctx: AppCtx) -> None:
    import websockets

    from perf_suite import SERVICE_RECORDING, EngineClient, _concat

    rec = SERVICE_RECORDING
    if not (rec / "audio.wav").exists():
        raise SystemExit(f"no recording at {rec}")
    quick = ctx.args.size == "quick"
    import tempfile

    tmpd = Path(tempfile.mkdtemp(prefix="perf-app-"))
    # The setlist's two songs (Awesome God, Build My Life) are the first 8
    # minutes of the recording; quick plays Build My Life's 4.
    seconds = _concat(rec / "audio.wav", 275.0 if quick else 0.0, 240.0 if quick else 480.0,
                      1 if quick else ctx.args.loops, tmpd / "service.wav")
    total = seconds * (1 if quick else ctx.args.loops)
    # The app's own AI off, so starting the service never opens the
    # microphone (a fresh perf app has no permission, and a pending prompt
    # hangs the open); the engine's session is started from the recording
    # instead, and the operator view follows it as it follows any session.
    await ctx.js('localStorage.setItem("auto-presenter.ai-enabled", "0"); window.__old = 1; '
                 'location.reload(); return true;')
    for _attempt in range(120):  # until the fresh page (no marker) has loaded
        await asyncio.sleep(0.5)
        try:
            fresh = await ctx.bridge._direct(
                'return !window.__old && document.readyState === "complete";', "main", 5)
        except RuntimeError:
            continue
        if fresh:
            break
    await ctx.bridge.install_helpers()
    await go_home(ctx)
    await ctx.js(_open_setlist_js() + ' document.querySelector("[aria-label=\\"Other ways to start\\"]").click(); '
                 'await new Promise((r) => setTimeout(r, 300)); '
                 'P.click([...document.querySelectorAll("button")].find((b) => (b.textContent||"").trim().startsWith("Operator view"))); '
                 'await P.until(() => P.byTestId("operator-view"), 20000); P.watchLag(); return true;')
    port = await ctx.js('return await window.__TAURI__.core.invoke("get_sidecar_port");')
    recording = json.loads((rec / "recording.json").read_text())
    golden = json.loads((rec / "golden.json").read_text())["ground_truth"]
    # this setlist's two songs, exactly as the app sent them when it recorded
    items = [i for i in recording["setlist_content"] if i["item_idx"] in (0, 1)]
    async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
        c = EngineClient(ws)
        pump = asyncio.create_task(c.pump())
        model, _ = await c.request({"kind": "recommend_model"}, 60)
        session = {
            "kind": "start_audio_session", "backend": model["backend"], "model_size": model["model_size"],
            "record": False, "ai_mode": "active", "setlist_content": {"items": items},
            "start_item_idx": 0 if quick else golden[0]["item_idx"], "start_slide_idx": 0,
        }
        moves: list[float] = []
        c.listeners.append(lambda m: moves.append(c.audio_t) if m.get("kind") == "slide_change" and m.get("source") == "ai" else None)
        t_start = time.monotonic()
        states: list[str] = []
        c.listeners.append(lambda m: states.append(
            f"{time.monotonic() - t_start:.0f}s {m.get('state')} {m.get('error') or ''}".strip())
            if m.get("kind") == "audio_session_state" else None)
        c.stopped.clear()
        await c.request({"kind": "inject_audio", "wav_path": str(tmpd / "service.wav"), "realtime": True,
                         "session": session}, 600)
        ctx.sampler.phase = "service"
        per_min, scripture_ms, click_ms = [], [], []
        t0 = time.monotonic()
        minute = 0
        while not c.stopped.is_set() and time.monotonic() - t0 < total + 60:
            await asyncio.sleep(5)
            if time.monotonic() - t0 >= 60 * (minute + 1):
                minute += 1
                per_min.append({**ctx.mem(), **(await ctx.ui_stats())})
                snap, _ = await c.request({"kind": "session_snapshot"}, 10)
                sn = snap["snapshot"]
                if minute * 60 < total - 10 and (sn.get("audio_source") != "file" or sn.get("session") != "running"):
                    ctx.result.notes.append(f"minute {minute}: session {sn.get('session')}, audio {sn.get('audio_source')}")
                # The operator, once a minute: a passage looked up, then a slide clicked.
                if minute % 2 == 1:
                    scripture_ms.append(await ctx.timed(
                        'const P=window.__perf; P.click(P.byTestId("operator-tab-scripture")); '
                        'const box=await P.until(() => P.byTestId("live-scripture-input")); '
                        'P.type(box, "John 3:16"); await P.until(() => P.byTestId("live-scripture-result"));'
                        ' P.click(P.byTestId("operator-tab-songs"));', timeout=30))
                else:
                    click_ms.append(await ctx.timed(
                        'const P=window.__perf; const cards=document.querySelectorAll("[data-testid=operator-slide-card]"); '
                        'P.click(cards[Math.min(3, cards.length - 1)]); await P.painted();', timeout=30))
                    await c.request({"kind": "set_ai_mode", "mode": "active"}, 10)
        pump.cancel()
    played = time.monotonic() - t0
    if played < total - 30:
        ctx.result.notes.append(f"the session ended after {played / 60:.1f} of {total / 60:.0f} min")
    ctx.result.notes.append("session states: " + ", ".join(states[-8:]))
    ctx.result.add("service length", total / 60, "min", None)
    ctx.result.add("AI slide changes", len(moves), "", None)
    ctx.result.latencies("operator: look up a passage", scripture_ms)
    ctx.result.latencies("operator: click a slide", click_ms)
    summarise_round_memory(ctx, "service (per minute)", per_min)
    _cpu_note(ctx, "service")
    await lag_metrics(ctx, "service")
    await ctx.js('const P=window.__perf; const s=P.byText("Stop presenting"); if (s) P.click(s); return true;')
    shutil.rmtree(tmpd, ignore_errors=True)


_ = statistics
