"""Record engine tapes: each golden through the real engine, in real time, with
nobody at the controls — the input AI-assisted mode sees.

    caffeinate -i uv run --project sidecar python research/eval/record_engine_tapes.py \\
        [--recording <dir> ...] [--out-dir eval_tapes] [--model small.en] [--backend mlx]

In Assist the engine runs free (operator clicks are not fed back), exactly as
in Auto mode with nobody touching anything. So a tape of that run *is* what
the assist layer gets, and `research/eval/eval_assist.py` can replay it with any
number of simulated operators in seconds. See `Plan/AI_ASSISTED_MODE.md`.

Re-record after an engine upgrade; the assist layer itself doesn't change.

**Decoy songs.** Most goldens are one song, which can't test a missed song
change. Each single-song golden is run with another golden's song put first in
the setlist and the session started on it — the band starts a song the screen
(and the engine) aren't on, and the engine has to find it. The tape records the
mapping from the golden's item numbers to the tape's (`item_map`).

`--assist-tier` records what a slower computer's Assist sees instead: the
Assist-only tier (`server_follower.assist_tier`: short-context speech every
2 s, no score follower), with `--backend whisper`. Its tapes go in their own
folder, and `eval_assist.py --set next_tier=0` replays them as that tier.

Config: the score follower without lead sheets and with no learning database
(`nosheet` in `run_follower_eval.py`) — production minus what the dev Mac's
own `learning.db` learned, which may have learned from these very recordings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "sidecar"))

from run_follower_eval import Golden, run_once  # noqa: E402

from auto_presenter.eval.replay import load_recording  # noqa: E402

RECORDINGS = Path("~/Library/Application Support/com.churchslides.app/recordings").expanduser()
CONFIG = "nosheet"


def goldens(explicit: list[Path]) -> list[Path]:
    if explicit:
        return [p.expanduser().resolve() for p in explicit]
    return sorted(p.parent for p in RECORDINGS.glob("rec_*/golden.json") if (p.parent / "audio.wav").exists())


def _item_payload(item, item_idx: int) -> dict:
    return {
        "item_idx": item_idx,
        "song_id": item.song_id,
        "title": getattr(item, "title", "") or "",
        "slides": item.slides,
        "slide_sections": list(getattr(item, "slide_sections", []) or []),
    }


def plan(rec_dir: Path, all_dirs: list[Path]) -> tuple[list[dict], dict[int, int], int]:
    """The setlist to run, golden item -> tape item, and the start item."""
    rec = load_recording(rec_dir)
    own = [_item_payload(i, i.item_idx) for i in rec.setlist_content]
    first = min(rec.ground_truth, key=lambda g: g.t_seconds).item_idx
    if len(rec.setlist_content) > 1:
        return own, {i["item_idx"]: i["item_idx"] for i in own}, first
    # A decoy: the next golden (cyclically) whose song differs.
    song = rec.setlist_content[0].song_id
    k = all_dirs.index(rec_dir)
    for other in all_dirs[k + 1 :] + all_dirs[:k]:
        o = load_recording(other)
        if len(o.setlist_content) == 1 and o.setlist_content[0].song_id != song:
            decoy = _item_payload(o.setlist_content[0], 0)
            mine = _item_payload(rec.setlist_content[0], 1)
            return [decoy, mine], {rec.setlist_content[0].item_idx: 1}, 0
    raise SystemExit(f"{rec_dir.name}: no decoy song available")


async def record(
    rec_dir: Path, all_dirs: list[Path], out_dir: Path, model: str, backend: str, port: int,
    config: str = CONFIG,
) -> None:
    items, item_map, start = plan(rec_dir, all_dirs)
    tape: list = []
    started = time.monotonic()
    await run_once(
        Golden(rec_dir, {}), config, model, port, backend=backend, tape=tape,
        session_patch={
            "setlist_content": {"items": [{k: v for k, v in i.items() if k != "title"} for i in items]},
            "start_item_idx": start,
            "start_slide_idx": 0,
        },
    )
    out = {
        "recording": rec_dir.name,
        "config": config,
        "model": model,
        "backend": backend,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M"),
        "setlist": items,
        "item_map": {str(k): v for k, v in item_map.items()},
        "start_item_idx": start,
        "events": [{"t": t, **m} for t, m in tape],
    }
    path = out_dir / f"{rec_dir.name}.json"
    path.write_text(json.dumps(out))
    moves = sum(1 for _, m in tape if m.get("kind") == "slide_change")
    print(f"  {rec_dir.name}: {moves} moves, {len(tape) - moves} beliefs, "
          f"{time.monotonic() - started:.0f}s -> {path}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recording", action="append", type=Path, default=[])
    ap.add_argument("--out-dir", type=Path, default=REPO / "eval_tapes")
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--backend", default="mlx")
    ap.add_argument("--port", type=int, default=8794)
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--assist-tier", action="store_true",
                    help="record as a computer in the Assist-only tier (use with --backend whisper)")
    args = ap.parse_args()
    config = CONFIG
    if args.assist_tier:
        import run_follower_eval

        run_follower_eval.TUNABLE_OVERRIDES["assist_tier"] = True
        config = "off"  # the tier runs no score follower
    args.out_dir.mkdir(parents=True, exist_ok=True)
    every = goldens([])
    chosen = goldens(args.recording) if args.recording else every
    for rec_dir in chosen:
        if args.skip_existing and (args.out_dir / f"{rec_dir.name}.json").exists():
            continue
        asyncio.run(
            record(rec_dir, every, args.out_dir, args.model, args.backend, args.port, config)
        )
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
