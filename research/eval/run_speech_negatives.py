"""Talk only, through the real engine: nothing here may change a slide.

    uv run --project sidecar --no-sync python research/eval/run_speech_negatives.py \\
        --setlist-from "<recording dir>" [--audio a.wav b.mp3 ...] [--out results.json]

With no `--audio`, plays every file in the public speech set
(`research/data/fetch_public_worship.py --sets speech`). Each file is played in
real time into a fresh sidecar (`inject_audio`, as in `run_follower_eval.py`)
with the production tunables, the AI in active mode and the setlist taken from
`--setlist-from` (a real service's `recording.json`), sitting on its first
item's leading blank slide. Every `slide_change` the engine broadcasts that a
person didn't make is a false trigger; so is the display blanking. The aim is
zero.

Per file it writes `<out stem>/<file>.jsonl`: every broadcast except the
high-rate ones, on the audio clock, so a false trigger can be traced to the
words that caused it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sidecar"))

import websockets  # noqa: E402

from auto_presenter.protocol import ParticleFilterTunables  # noqa: E402

#: the research folder sits beside the checkout; a worktree is nested inside it
DEFAULT_DATA = next(
    (p / "auto-presenter-research" for p in REPO.parents if (p / "auto-presenter-research").is_dir()),
    REPO.parent / "auto-presenter-research",
) / "public_worship" / "speech"
#: Too frequent to keep, and not evidence of a trigger.
SKIP_KINDS = {"audio_level", "belief_state", "pipeline_health", "music_state", "chord_state"}
TRIGGER_SOURCES = {"ai", "voice"}


def add_song_library(db: Path, src: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_follower_eval import add_song_library as add

    add(db, src.expanduser().resolve())


def as_wav(src: Path, cache: Path) -> Path:
    """16 kHz mono WAV, as the engine's capture produces."""
    if src.suffix.lower() == ".wav":
        return src
    dest = cache / (src.stem + ".wav")
    if not dest.exists():
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(dest)],
                       check=True)
    return dest


def load_setlist(rec_dir: Path) -> list[dict]:
    rec = json.loads((rec_dir / "recording.json").read_text())
    sc = rec["setlist_content"]
    items = sc["items"] if isinstance(sc, dict) else sc
    return [{k: it[k] for k in ("item_idx", "song_id", "slides", "slide_sections", "spoken") if k in it}
            for it in items]


async def play(wav: Path, items: list[dict], model: str, backend: str, port: int, log_path: Path,
               overrides: dict | None = None) -> dict:
    tunables = ParticleFilterTunables(learned_collect=False).model_dump()  # production, but don't learn
    tunables.update(overrides or {})
    session = {
        "kind": "start_audio_session", "backend": backend, "model_size": model, "record": False,
        "setlist_content": {"items": items}, "ai_mode": "active", "tunables": tunables,
        "start_item_idx": items[0]["item_idx"], "start_slide_idx": 0,
    }
    t_audio = 0.0
    triggers, blanks, kinds = [], 0, {}
    started = time.monotonic()
    with log_path.open("w") as log:
        async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "kind": "inject_audio", "wav_path": str(wav),
                                      "realtime": True, "session": session}))
            running = False
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("kind")
                if kind == "audio_level":
                    t_audio = float(msg["t_seconds"])
                if kind in SKIP_KINDS:
                    continue
                kinds[kind] = kinds.get(kind, 0) + 1
                log.write(json.dumps({"t_audio": round(t_audio, 2), **msg}) + "\n")
                if kind == "import_library_song":
                    # a library song put into the setlist by itself: a false trigger too
                    triggers.append((round(t_audio, 1), "import", msg.get("song_id"), msg.get("slide_idx")))
                elif kind == "slide_change" and msg.get("source") in TRIGGER_SOURCES:
                    triggers.append((round(t_audio, 1), msg["source"], msg["item_idx"], msg["slide_idx"]))
                elif kind == "singing_state" and msg.get("blanking"):
                    blanks += 1
                elif kind == "error" or (kind == "inject_audio_response" and msg.get("error")):
                    raise SystemExit(f"engine error: {msg}")
                elif kind == "audio_session_state":
                    if msg["state"] == "running":
                        running = True
                    elif running and msg["state"] in ("stopped", "error"):
                        break
    return {"file": wav.name, "minutes": round(t_audio / 60, 1), "triggers": triggers,
            "blank_messages": blanks, "kinds": kinds, "wall_s": round(time.monotonic() - started)}


async def run_file(wav: Path, items, args, log_path: Path) -> dict:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # an empty library: no lead sheets, no learned rows, nothing from the real app
        db = tmp / "library.db"
        if args.song_library is not None:
            # songs only, so the off-setlist watcher can import (and so be caught doing it)
            add_song_library(db, args.song_library)
        (tmp / "recordings").mkdir()
        proc = subprocess.Popen(
            [sys.executable, "-m", "auto_presenter", "--port", str(args.port), "--db-path", str(db), "--dev-tools"],
            cwd=REPO / "sidecar", stdout=subprocess.PIPE, stderr=open(tmp / "sidecar.log", "w"), text=True,
        )
        try:
            for line in proc.stdout:
                if '"listening"' in line:
                    break
            return await play(wav, items, args.model, args.backend, args.port, log_path, args.overrides)
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
            err = (tmp / "sidecar.log").read_text()
            # keep the engine's own log beside the broadcasts: it says which rule moved the screen
            log_path.with_suffix(".sidecar.log").write_text(err)
            if "Traceback" in err:
                print(err[err.index("Traceback"):][:3000], flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--setlist-from", type=Path, required=True, help="Recording dir whose setlist to load.")
    ap.add_argument("--audio", type=Path, nargs="*", help=f"Files to play (default: everything in {DEFAULT_DATA}).")
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--backend", default="mlx")
    ap.add_argument("--port", type=int, default=8794)
    ap.add_argument("--out", type=Path, default=Path("speech_negatives.json"))
    ap.add_argument("--song-library", type=Path,
                    help="a library.db whose songs the engine may import (default: none, so off-setlist import never runs)")
    ap.add_argument("--tunable", action="append", default=[], metavar="KEY=VALUE",
                    help="override a ParticleFilterTunables field (JSON value)")
    args = ap.parse_args()
    args.overrides = {}
    for kv in args.tunable:
        k, _, v = kv.partition("=")
        if k not in ParticleFilterTunables.model_fields:
            raise SystemExit(f"unknown tunable {k}")
        args.overrides[k] = json.loads(v)

    # smallest first, so the first results arrive early
    files = args.audio or sorted((p for p in DEFAULT_DATA.rglob("*") if p.suffix.lower() in (".wav", ".mp3")),
                                 key=lambda p: p.stat().st_size)
    if not files:
        raise SystemExit(f"no audio: fetch it with research/data/fetch_public_worship.py --sets speech")
    items = load_setlist(args.setlist_from.expanduser())
    logs = args.out.with_suffix("")
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    for f in files:
        wav = as_wav(f, logs / "wav")
        print(f"== {f.name}", flush=True)
        r = asyncio.run(run_file(wav, items, args, logs / f"{wav.stem}.jsonl"))
        r["source"] = str(f)
        results.append(r)
        print(f"   {r['minutes']} min: {len(r['triggers'])} slide changes, {r['blank_messages']} blanking"
              f" messages  {r['triggers'][:8]}", flush=True)
        args.out.write_text(json.dumps({"setlist_from": str(args.setlist_from), "model": args.model,
                                        "tunables": args.overrides,
                                        "results": results}, indent=1))
    total_min = sum(r["minutes"] for r in results)
    n = sum(len(r["triggers"]) for r in results)
    print(f"\n{n} slide changes in {total_min:.0f} min of talk ({n / max(total_min, 1e-9) * 60:.1f} per hour)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
