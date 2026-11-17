"""Post-hoc annotation CLI — mark slide changes against a recorded audio file.

Two modes, picked by `--mode`:

  # Interactive: play the audio (via afplay on macOS / aplay on Linux /
  # the system player on Windows) and stream the elapsed-seconds clock.
  # Press ENTER each time the operator changes slide; type "next-song"
  # to advance the item index; type "save" to write the recording.
  uv run --project sidecar python research/eval/annotate.py \\
      --recording data/recordings/last_sunday \\
      --mode interactive

  # CSV: convert a hand-written timestamp log into ground_truth events.
  # The file format is one event per line:
  #     <t_seconds_or_mmss>  <item_idx>  <slide_idx>
  # Lines starting with `#` and blank lines are ignored.
  uv run --project sidecar python research/eval/annotate.py \\
      --recording data/recordings/last_sunday \\
      --mode csv \\
      --input timeline.txt

Both modes write `recording.json` next to the original audio. By default
existing ground truth is replaced; pass `--append` to merge instead.

For the killer property described in PHASE_3.md — every real service
auto-recorded — this CLI is the fallback for recordings imported from
outside the app (e.g. last week's audio rescued from a separate
recorder). Live services capture ground truth at the source.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.eval.replay import load_recording, save_recording
from auto_presenter.eval.types import GroundTruthEvent, Recording

log = logging.getLogger("annotate")


# --------------------------------------------------------------------- timestamp parsing


def parse_timestamp(text: str) -> float:
    """Accept seconds (`12.5`) or `mm:ss` / `hh:mm:ss`."""
    text = text.strip()
    if not text:
        raise ValueError("empty timestamp")
    if ":" not in text:
        return float(text)
    parts = text.split(":")
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    raise ValueError(f"unrecognized timestamp: {text!r}")


def parse_csv_lines(lines: Iterable[str]) -> list[GroundTruthEvent]:
    """Convert a CSV/whitespace timeline file into `GroundTruthEvent`s.

    Format per line: `<t> <item_idx> <slide_idx>` separated by whitespace
    or commas. `#` introduces a comment.
    """
    out: list[GroundTruthEvent] = []
    for line_no, raw in enumerate(lines, start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p for p in line.replace(",", " ").split() if p]
        if len(parts) < 3:
            raise ValueError(f"line {line_no}: expected `<t> <item_idx> <slide_idx>`, got {raw!r}")
        try:
            t = parse_timestamp(parts[0])
            item = int(parts[1])
            slide = int(parts[2])
        except ValueError as exc:
            raise ValueError(f"line {line_no}: {exc}") from exc
        out.append(GroundTruthEvent(t_seconds=t, item_idx=item, slide_idx=slide))
    out.sort(key=lambda g: g.t_seconds)
    return out


# --------------------------------------------------------------------- audio playback


def _player_command(audio_path: Path) -> list[str] | None:
    """Pick a system audio player. Returns None if nothing's available.

    We intentionally use whatever's already on the host rather than
    pulling in another Python audio playback dep — that means desktops
    without the usual tools just fall back to "no playback, type
    timestamps manually" mode, which is still useful for blind
    annotation against a stopwatch.
    """
    if sys.platform == "darwin" and shutil.which("afplay"):
        return ["afplay", str(audio_path)]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(audio_path)]
    if shutil.which("aplay"):
        return ["aplay", "-q", str(audio_path)]
    if sys.platform == "win32":
        return ["powershell", "-c", f"(New-Object Media.SoundPlayer '{audio_path}').PlaySync()"]
    return None


class _BackgroundPlayer:
    """Spawn a playback subprocess; terminate it on close."""

    def __init__(self, audio_path: Path) -> None:
        self.audio_path = audio_path
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        cmd = _player_command(self.audio_path)
        if cmd is None:
            log.warning(
                "no system audio player found (afplay/ffplay/aplay) — "
                "playback disabled; annotate against an external stopwatch"
            )
            return
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.warning("failed to launch audio player: %s", exc)

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=1.0)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        finally:
            self.process = None


# --------------------------------------------------------------------- interactive loop


def run_interactive(
    recording: Recording,
    *,
    play_audio: bool = True,
) -> list[GroundTruthEvent]:
    """Drive the operator through an interactive annotation session.

    Commands typed at the prompt:
      <ENTER>            — mark a slide change at the current elapsed time
      n / next-song      — increment item_idx, reset slide_idx to 0
      p / prev-slide     — decrement slide_idx for the next entry
      back               — undo the most recent event
      save               — finish, returning the collected events
      quit / abort       — finish, discarding the collected events
    """
    audio_path = Path(recording.audio_path) if recording.audio_path else None
    if play_audio and audio_path and audio_path.exists():
        player = _BackgroundPlayer(audio_path)
        player.start()
    else:
        player = None
        if play_audio and audio_path and not audio_path.exists():
            log.warning("audio file missing: %s", audio_path)

    events: list[GroundTruthEvent] = []
    item_idx = 0
    slide_idx = 0
    t0 = time.monotonic()
    stop_requested = threading.Event()

    def handle_sigint(signum, frame):  # type: ignore[no-untyped-def]
        del signum, frame
        stop_requested.set()
        print("\n(use `save` or `quit` to finish)", flush=True)

    prev_handler = signal.signal(signal.SIGINT, handle_sigint)

    try:
        print(
            "Annotation mode. ENTER = mark slide change. "
            "Type `n` next-song, `p` prev-slide, `back` undo, "
            "`save` finish, `quit` abort.",
            flush=True,
        )
        while True:
            try:
                line = input(_prompt(item_idx, slide_idx, t0))
            except EOFError:
                break
            cmd = line.strip().lower()
            now = time.monotonic() - t0
            if cmd in ("", "mark"):
                events.append(
                    GroundTruthEvent(t_seconds=now, item_idx=item_idx, slide_idx=slide_idx)
                )
                slide_idx += 1
            elif cmd in ("n", "next", "next-song"):
                item_idx += 1
                slide_idx = 0
                events.append(
                    GroundTruthEvent(t_seconds=now, item_idx=item_idx, slide_idx=slide_idx)
                )
                slide_idx += 1
            elif cmd in ("p", "prev", "prev-slide"):
                slide_idx = max(0, slide_idx - 2)
                events.append(
                    GroundTruthEvent(t_seconds=now, item_idx=item_idx, slide_idx=slide_idx)
                )
                slide_idx += 1
            elif cmd == "back":
                if events:
                    removed = events.pop()
                    item_idx = removed.item_idx
                    slide_idx = removed.slide_idx
                    print(f"  undid {removed.t_seconds:.2f}s", flush=True)
            elif cmd in ("save", "done"):
                break
            elif cmd in ("quit", "abort"):
                events = []
                break
            else:
                print(f"  unknown command `{cmd}` (ENTER to mark, `save` to finish)")
    finally:
        signal.signal(signal.SIGINT, prev_handler)
        if player is not None:
            player.stop()

    events.sort(key=lambda g: g.t_seconds)
    return events


def _prompt(item_idx: int, slide_idx: int, t0: float) -> str:
    return f"[item {item_idx} slide {slide_idx} t≈{time.monotonic() - t0:5.1f}s] > "


# --------------------------------------------------------------------- main


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--recording",
        required=True,
        help="Recording directory or recording.json path.",
    )
    parser.add_argument(
        "--mode",
        choices=("interactive", "csv"),
        default="interactive",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="CSV/timeline file (required in --mode csv).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Where to write the updated recording.json. Defaults to overwriting the input.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Merge with existing ground_truth instead of replacing it.",
    )
    parser.add_argument(
        "--no-playback",
        action="store_true",
        help="In interactive mode, skip launching the system audio player.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    rec_path = Path(args.recording)
    recording = load_recording(rec_path)

    if args.mode == "csv":
        if not args.input:
            raise SystemExit("--mode csv requires --input <file>")
        text = Path(args.input).read_text().splitlines()
        new_events = parse_csv_lines(text)
    else:
        new_events = run_interactive(recording, play_audio=not args.no_playback)

    if not new_events:
        log.warning("no events captured; not writing recording")
        return 1

    if args.append:
        merged = sorted(
            list(recording.ground_truth) + new_events, key=lambda g: g.t_seconds
        )
    else:
        merged = new_events

    updated = recording.model_copy(update={"ground_truth": merged})

    if args.output:
        out_path = Path(args.output)
    elif rec_path.is_dir():
        out_path = rec_path / "recording.json"
    elif rec_path.name == "recording.json":
        out_path = rec_path
    else:
        out_path = rec_path.with_suffix(".json")

    save_recording(updated, out_path)
    log.info("wrote %d ground-truth events to %s", len(merged), out_path)
    print(f"wrote {len(merged)} events → {out_path}")
    # Echo a JSON summary so callers / dashboards can pick it up easily.
    print(
        json.dumps(
            {
                "recording_id": updated.id,
                "ground_truth_count": len(merged),
                "output": str(out_path),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
