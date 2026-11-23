"""The score follower through the real server, in real time — the shipping gate.

    uv run --project sidecar python research/eval/run_follower_eval.py \\
        --golden "<recording dir>:0=<lead sheet pdf>" [--golden ...] \\
        [--configs on,off] [--model small.en] [--backend mlx|whisper] [--out results.json]

Each `--golden` is a recording directory (audio.wav + recording.json with the
operator's slide changes) and, per setlist item, the SongSelect lead-sheet PDF
for that song (`item=pdf`, several separated by `,`).

For each recording and configuration this starts a fresh sidecar with dev
tools and a throwaway library holding the lead sheets — read by
`parse_lead_sheet` and stored the way the importer stores them — then plays
the recording's audio into it in real time (`inject_audio`). Everything the
live app runs then acts: MLX Whisper, the span scorer sharing its GPU
queue, the chord analyser, the singing detector, the doubtful-word filter,
the Director. Every AI `slide_change` broadcast is a decision, stamped with
the audio clock from the latest `audio_level` (the room's clock), and scored
with the harness's own `wrong_slide_rate` (forgiving, identical slides),
`perceptual_score` on the band's sung timing, and `anticipation_error_ms`.

Configurations: `on` — the follower engages for songs with a lead sheet;
`off` — the same server and pipeline with the `score_follower` kill switch
off, i.e. production; `nosheet` — the follower on with no lead sheets in the
library and `score_follower_without_sheet` on, so every song is followed by
its library slides alone; `learned` — `nosheet` with `--learning-db` copied
in beside the library, so the follower also takes what those past services
taught (seed it from *other* services of the song, never the one being
scored); `learning` — `nosheet` with `in_service_learning` on, so each song
learns its slides' rhythm and chords from this service as it is sung. Runs are sequential: two at once would share the GPU
and slow each other, which is not what a service looks like.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
#: Set from --learning-db.
LEARNING_DB: Path | None = None
#: Set from --onnx-scorer / --onnx-threads.
ONNX_SCORER: Path | None = None
ONNX_THREADS = 0
#: Set from --capture: broadcasts worth reading after the run, on the audio clock.
CAPTURE: Path | None = None
#: a real library.db whose songs go into every run's library (--song-library)
SONG_LIBRARY: Path | None = None
#: Set from --tunable key=value: production tunables to override for every run.
TUNABLE_OVERRIDES: dict = {}
CAPTURE_KINDS = ("singing_state", "transcript_partial", "spoken_instruction", "slide_change", "flow_position")
#: Set from --no-blank: run with `blank_when_idle` off (on by default since
#: 2026-10-16), to measure what holding the AI's moves while talking costs.
NO_BLANK = False
sys.path.insert(0, str(REPO / "sidecar"))

import websockets  # noqa: E402

from auto_presenter.eval.metrics import (  # noqa: E402
    anticipation_error_ms,
    perceptual_score,
    wrong_slide_rate,
)
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.eval.types import Decision  # noqa: E402
from auto_presenter.leadsheet import parse_lead_sheet  # noqa: E402
from auto_presenter.protocol import ParticleFilterTunables  # noqa: E402


@dataclass
class Golden:
    rec_dir: Path
    sheets: dict[int, Path]


@dataclass
class RunResult:
    name: str
    config: str
    wrong_slide: float
    anticipation_ms: float
    decisions: int
    unhealthy_reports: int
    dropped_chunks: int
    seconds: float
    #: Pipeline health over the run: ASR lag and ASR pass time, median / p90.
    lag_s: tuple[float, float] = (0.0, 0.0)
    asr_ms: tuple[float, float] = (0.0, 0.0)
    #: The engine process's CPU (% of one core), median / p90.
    cpu_percent: tuple[float, float] = (0.0, 0.0)
    #: `COST_KEYS` -> median / p90 over the run's health windows.
    cost: dict[str, tuple[float, float]] = field(default_factory=dict)
    trace: list[tuple[float, int, int]] = field(default_factory=list)
    #: `perceptual_score` on the band's sung timing, and its late part.
    perceived: float = float("nan")
    perceived_late: float = float("nan")


#: What the music follower and chord analyser cost, from pipeline health.
COST_KEYS = (
    "follower_ms_per_s", "follower_frame_ms_p95", "chord_ms_per_s", "chord_frame_ms_p95", "scorer_ms",
    "next_line_ms", "next_line_skipped",
)


def parse_golden(spec: str) -> Golden:
    rec, _, sheets = spec.partition(":")
    out = {}
    for part in filter(None, sheets.split(",")):
        item, _, pdf = part.partition("=")
        out[int(item)] = Path(pdf).expanduser().resolve()
    # absolute: the engine runs from sidecar/, where a relative path means nothing
    return Golden(Path(rec).expanduser().resolve(), out)


def make_library(tmp: Path, rec, golden: Golden, sheets: bool = True) -> Path:
    """A library.db holding the lead sheets for this recording's songs (none
    when not `sheets`)."""
    db = tmp / "library.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE song_references (id TEXT PRIMARY KEY, song_id TEXT, kind TEXT, "
        "source_name TEXT, data_json TEXT, summary_json TEXT, created_at INT, updated_at INT)"
    )
    for item in rec.setlist_content:
        pdf = golden.sheets.get(item.item_idx) if sheets else None
        if pdf is None:
            continue
        sheet = parse_lead_sheet(pdf.read_bytes(), pdf.name)
        if not sheet.is_lead_sheet:
            raise SystemExit(f"{pdf}: not read as a lead sheet ({sheet.reason})")
        con.execute(
            "INSERT INTO song_references VALUES (?, ?, 'lead_sheet', ?, ?, '{}', 0, 0)",
            (f"ref_{item.item_idx}", item.song_id, pdf.name, sheet.score_json()),
        )
    con.commit()
    con.close()
    if SONG_LIBRARY is not None:
        add_song_library(db, SONG_LIBRARY)
    (tmp / "recordings").mkdir(exist_ok=True)
    return db


def add_song_library(db: Path, src: Path) -> None:
    """Copy a real library's songs (not its lead sheets) into `db`, so the
    off-setlist watcher has something to search. Without this every gate runs
    with an empty song table and the watcher never does anything."""
    s = sqlite3.connect(f"{src.as_uri()}?mode=ro", uri=True)
    rows = s.execute("SELECT id, title, body_text, slide_order FROM songs").fetchall()
    s.close()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS songs (id TEXT PRIMARY KEY, title TEXT, body_text TEXT, slide_order TEXT)")
    con.executemany("INSERT OR REPLACE INTO songs VALUES (?, ?, ?, ?)", rows)
    con.commit()
    con.close()


async def run_once(
    golden: Golden, config: str, model: str, port: int, trace_dir: Path | None = None,
    backend: str = "mlx", session_patch: dict | None = None, tape: list | None = None,
) -> RunResult:
    # "<config>+loops" / "+noloops": the same with counted loops forced on /
    # off (reference.LOOPS; on by default since 2026-10-10)
    # "+short": the CPU speech engine's short-context passes (asr_short_context)
    short = config.endswith("+short")
    config_ = config.removesuffix("+short")
    loops = "1" if config_.endswith("+loops") else "0" if config_.endswith("+noloops") else None
    base = config_.removesuffix("+loops").removesuffix("+noloops")
    rec = load_recording(golden.rec_dir)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = make_library(tmp, rec, golden, sheets=base not in ("nosheet", "learned", "learning"))
        if base == "learned":
            if LEARNING_DB is None:
                raise SystemExit("config 'learned' needs --learning-db")
            shutil.copy(LEARNING_DB, tmp / "learning.db")
        env = dict(os.environ)
        if ONNX_SCORER is not None:
            # off Apple silicon: the follower's span scorer on ONNX Runtime
            env["AUTO_PRESENTER_ONNX_SCORER"] = str(ONNX_SCORER)
            env["AUTO_PRESENTER_ONNX_THREADS"] = str(ONNX_THREADS)
        if loops is not None:
            env["AUTO_PRESENTER_LOOPS"] = loops
        if trace_dir is not None:
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace = trace_dir / f"{golden.rec_dir.name}_{config}.jsonl"
            trace.unlink(missing_ok=True)
            env["AUTO_PRESENTER_FOLLOWER_TRACE"] = str(trace)
        proc = subprocess.Popen(
            [sys.executable, "-m", "auto_presenter", "--port", str(port), "--db-path", str(db), "--dev-tools"],
            env=env,
            cwd=REPO / "sidecar",
            stdout=subprocess.PIPE,
            stderr=open(tmp / "sidecar.log", "w"),  # noqa: SIM115 - lives as long as the process
            text=True,
        )
        try:
            for line in proc.stdout:  # wait for the server to announce itself
                if '"listening"' in line:
                    break
            result = await _drive(golden, rec, base, model, port, backend, short, session_patch, tape)
            result.config = config
            return result
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
            log = (tmp / "sidecar.log").read_text()
            keep = os.environ.get("KEEP_SIDECAR_LOG")
            if keep:
                Path(keep).mkdir(parents=True, exist_ok=True)
                (Path(keep) / f"{golden.rec_dir.name}_{config}.log").write_text(log)
            for pattern in ("score follower", "ONNX span scorer", "key [+-]", "load level", "music change", "song change", "song [0-9]+ heard", "opening lines", "learning", "learned slides", "look-back", "Traceback"):
                for m in re.finditer(rf".*{pattern}.*", log):
                    print(f"      [sidecar] {m.group(0)[-160:]}", flush=True)
            if "Traceback" in log:
                tb = log[log.index("Traceback") :].splitlines()[:40]
                print("\n".join(f"      [sidecar] {line}" for line in tb), flush=True)


async def _drive(
    golden: Golden, rec, config: str, model: str, port: int, backend: str, short: bool = False,
    session_patch: dict | None = None, tape: list | None = None,
) -> RunResult:
    """`session_patch` overrides keys of the `start_audio_session` request (a
    different setlist, start item...); `tape`, if given, collects every AI
    `slide_change` and `belief_state` as `(t_audio, message)` — the engine's
    side of a run, for `research/eval/record_engine_tapes.py`."""
    tunables = ParticleFilterTunables(
        score_follower=config in ("on", "nosheet", "learned", "learning"),
        # off Apple silicon's speech engine the follower runs only with this on
        score_follower_cpu=backend != "mlx" and config != "off",
        asr_short_context=short,
        score_follower_without_sheet=config in ("nosheet", "learned", "learning"),
        in_service_learning=config == "learning",
        # the run's own session must not write a row the next run reads
        learned_collect=False,
    ).model_dump()
    tunables.update(TUNABLE_OVERRIDES)
    first = min(rec.ground_truth, key=lambda g: g.t_seconds)
    session = {
        "kind": "start_audio_session",
        "backend": backend,
        "model_size": model,
        "record": False,
        "setlist_content": {
            "items": [
                {"item_idx": i.item_idx, "song_id": i.song_id, "slides": i.slides,
                 "slide_sections": list(getattr(i, "slide_sections", []) or [])}
                for i in rec.setlist_content
            ]
        },
        "ai_mode": "active",
        "tunables": tunables,
        "start_item_idx": first.item_idx,
        "start_slide_idx": 0,
    }
    session.update(session_patch or {})
    decisions: list[Decision] = []
    trace: list[tuple[float, int, int]] = []
    t_audio = 0.0
    unhealthy = dropped = 0
    lags: list[float] = []
    asr: list[float] = []
    cpu: list[float] = []
    #: per health window: the follower's and the chord analyser's ms of work
    #: per second of audio, and their p95 frame (`pipeline.stage_cost`)
    cost: dict[str, list[float]] = {k: [] for k in COST_KEYS}
    captured: list[dict] = []
    started = time.monotonic()
    async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
        if NO_BLANK:
            await ws.send(json.dumps({"id": 0, "kind": "set_blank_when_idle", "enabled": False}))
        await ws.send(json.dumps({"id": 1, "kind": "inject_audio", "wav_path": str(golden.rec_dir / "audio.wav"),
                                  "realtime": True, "session": session}))
        seen_running = False
        async for raw in ws:
            msg = json.loads(raw)
            kind = msg.get("kind")
            if CAPTURE is not None and kind in CAPTURE_KINDS:
                captured.append({"t_audio": t_audio, **msg})
            if kind == "audio_level":
                t_audio = float(msg["t_seconds"])
            elif kind == "belief_state" and tape is not None:
                tape.append((t_audio, msg))
            elif kind == "slide_change" and msg.get("source") == "ai":
                if tape is not None:
                    tape.append((t_audio, msg))
                # the app reports every move it shows; without this the
                # engine takes the move as declined and re-anchors (1.5 s)
                await ws.send(json.dumps({"kind": "record_slide_change", "item_idx": msg["item_idx"],
                                          "slide_idx": msg["slide_idx"], "source": "ai"}))
                decisions.append(Decision(t_seconds=t_audio, item_idx=msg["item_idx"],
                                          slide_idx=msg["slide_idx"], source="ai"))
                trace.append((t_audio, msg["item_idx"], msg["slide_idx"]))
            elif kind == "pipeline_health":
                if not msg.get("healthy", True):
                    unhealthy += 1
                dropped = max(dropped, int(msg.get("dropped_chunks", 0)))
                lags.append(float(msg.get("lag_seconds", 0.0)))
                if msg.get("asr_ms"):
                    asr.append(float(msg["asr_ms"]))
                if msg.get("cpu_percent"):
                    cpu.append(float(msg["cpu_percent"]))
                for k in COST_KEYS:
                    if msg.get(k):
                        cost[k].append(float(msg[k]))
            elif kind == "error":
                raise SystemExit(f"engine error: {msg.get('message') or msg}")
            elif kind == "inject_audio_response" and msg.get("error"):
                raise SystemExit(f"inject failed: {msg['error']}")
            elif kind == "audio_session_state":
                if msg["state"] == "running":
                    seen_running = True
                elif seen_running and msg["state"] in ("stopped", "error"):
                    if msg["state"] == "error":
                        print(f"      session error: {msg.get('error')}", flush=True)
                    break
    if CAPTURE is not None:
        # a folder (no suffix): one file per recording and config, so a
        # multi-golden run keeps every run's words for offline replay
        out = CAPTURE / f"{golden.rec_dir.name}.{config}.jsonl" if not CAPTURE.suffix else CAPTURE
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(m) for m in captured))
    wrong = wrong_slide_rate(decisions, rec.ground_truth, None, forgive_unpredictable=True,
                             setlist_content=rec.setlist_content, forgive_identical_slides=True)
    ant = anticipation_error_ms(decisions, rec.ground_truth)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sung_timing_check

    perc = perceptual_score(decisions, rec.ground_truth, rec.setlist_content,
                            timings=sung_timing_check.timings(golden.rec_dir, rec))
    return RunResult(golden.rec_dir.name, config, wrong, float(np.median(ant)) if ant else float("nan"),
                     len(decisions), unhealthy, dropped, time.monotonic() - started, trace=trace,
                     lag_s=_spread(lags), asr_ms=_spread(asr), cpu_percent=_spread(cpu),
                     cost={k: _spread(v) for k, v in cost.items()},
                     perceived=perc.score, perceived_late=perc.late_rate)


def _spread(xs: list[float]) -> tuple[float, float]:
    return (float(np.median(xs)), float(np.percentile(xs, 90))) if xs else (0.0, 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", action="append", required=True)
    ap.add_argument("--configs", default="on,off")
    ap.add_argument("--model", default="small.en")
    ap.add_argument(
        "--backend",
        default="mlx",
        choices=("mlx", "whisper"),
        help="speech engine: mlx (Apple silicon's GPU) or whisper (faster-whisper on the "
        "processor, as on an Intel Mac or Windows; the follower then runs without its span "
        "scorer, via score_follower_cpu)",
    )
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--learning-db", type=Path, help="learning.db for the 'learned' config")
    ap.add_argument(
        "--onnx-scorer",
        type=Path,
        help="with --backend whisper: the ONNX span scorer's model directory "
        "(scripts/export_whisper_scorer_onnx.py), e.g. build/onnx/base.en-int8",
    )
    ap.add_argument("--onnx-threads", type=int, default=0, help="ONNX Runtime threads (0: its default)")
    ap.add_argument("--no-blank", action="store_true", help="blank_when_idle off for the run")
    ap.add_argument("--capture", type=Path, help="write singing/transcript/slide broadcasts here (a .jsonl file, or a folder: one file per run)")
    ap.add_argument("--song-library", type=Path, help="a library.db whose songs every run's library holds, so off-setlist import can fire")
    ap.add_argument("--trace-dir", type=Path, help="write the follower's evidence trace per run (diagnostics)")
    ap.add_argument("--tunable", action="append", default=[], metavar="KEY=VALUE",
                    help="override a ParticleFilterTunables field for every run (JSON value)")
    args = ap.parse_args()
    for kv in args.tunable:
        k, _, v = kv.partition("=")
        if k not in ParticleFilterTunables.model_fields:
            raise SystemExit(f"unknown tunable {k}")
        TUNABLE_OVERRIDES[k] = json.loads(v)
    global LEARNING_DB, ONNX_SCORER, ONNX_THREADS, CAPTURE, NO_BLANK, SONG_LIBRARY
    SONG_LIBRARY = args.song_library.expanduser().resolve() if args.song_library else None
    LEARNING_DB = args.learning_db
    CAPTURE = args.capture
    NO_BLANK = args.no_blank
    ONNX_SCORER = args.onnx_scorer.resolve() if args.onnx_scorer else None
    ONNX_THREADS = args.onnx_threads
    if args.trace_dir is not None:
        args.trace_dir = args.trace_dir.resolve()  # the engine runs from sidecar/
    goldens = [parse_golden(g) for g in args.golden]
    results: list[RunResult] = []
    run = 0
    for g in goldens:
        for config in args.configs.split(","):
            # a port per run: the previous engine may still be shutting down
            port = args.port + run
            run += 1
            print(f"== {g.rec_dir.name} [{config}]", flush=True)
            r = asyncio.run(run_once(g, config, args.model, port, args.trace_dir, args.backend))
            results.append(r)
            print(f"   wrong-slide {r.wrong_slide:.3f}  perceived {r.perceived:.3f} (late {r.perceived_late:.3f})"
                  f"  anticipation {r.anticipation_ms:+.0f} ms  "
                  f"{r.decisions} decisions  unhealthy {r.unhealthy_reports}  dropped {r.dropped_chunks}  "
                  f"({r.seconds:.0f} s)\n   ASR lag {r.lag_s[0]:.2f} / {r.lag_s[1]:.2f} s  ASR pass {r.asr_ms[0]:.0f} / {r.asr_ms[1]:.0f} ms "
                  "(median / p90)", flush=True)
            c = r.cost
            print(f"   engine CPU {r.cpu_percent[0]:.0f} / {r.cpu_percent[1]:.0f} % of a core   "
                  + "   ".join(f"{k} {c[k][0]:.1f} / {c[k][1]:.1f}" for k in COST_KEYS if k in c and c[k][1]),
                  flush=True)
    print("\nsummary:")
    for config in args.configs.split(","):
        rs = [r for r in results if r.config == config]
        print(f"  {config:4s} mean wrong-slide {np.mean([r.wrong_slide for r in rs]):.3f}  "
              f"perceived {np.mean([r.perceived for r in rs]):.3f}  "
              + "  ".join(f"{r.name[-12:]} {r.wrong_slide:.3f}" for r in rs))
        print(f"       anticipation median {np.nanmedian([r.anticipation_ms for r in rs]):+.0f} ms  "
              f"engine CPU {np.mean([r.cpu_percent[0] for r in rs]):.0f}% of a core  "
              f"ASR lag p90 {max(r.lag_s[1] for r in rs):.2f} s  "
              f"dropped {sum(r.dropped_chunks for r in rs)}  unhealthy {sum(r.unhealthy_reports for r in rs)}  "
              f"follower {np.mean([r.cost.get('follower_ms_per_s', (0, 0))[0] for r in rs]):.1f} ms/s  "
              f"chords {np.mean([r.cost.get('chord_ms_per_s', (0, 0))[0] for r in rs]):.1f} ms/s")
    if args.out:
        args.out.write_text(json.dumps([r.__dict__ for r in results], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
