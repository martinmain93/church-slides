"""Streaming ASR CLI — `--input`, `--backend`, `--model`, prints events to stdout.

Two main uses:

  # Run Whisper-tiny over a WAV file at fast-as-possible speed.
  uv run --project sidecar python research/eval/run_asr.py \\
      --input file:///path/to/recording.wav --backend whisper --model tiny.en

  # Pipe a recording through the streaming wrapper exactly the way the
  # live audio session does, dumping committed-only events.
  uv run --project sidecar python research/eval/run_asr.py \\
      --input file:///path/recording.wav --backend whisper --streaming --final-only

The script is deliberately I/O-thin: it constructs a backend, a
capture source, optionally wraps the backend in `SlidingWindowWrapper`,
and prints each `TranscriptEvent` as JSON to stdout. Phase 3's eval
pipeline pipes into this for batch experiments.

Microphone capture (`--input mic`) requires sounddevice + a working
audio device — same constraint as the live audio session in the app.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.asr.backend import ASRBackend, MockASRBackend, schedule_from_words
from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.audio.capture import FileCapture
from auto_presenter.audio.types import AudioFrame

log = logging.getLogger("run_asr")


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--input",
        required=True,
        help="`mic` (live capture) or `file:///abs/path.wav` (or a bare path).",
    )
    parser.add_argument(
        "--backend",
        choices=("whisper", "mock"),
        default="whisper",
    )
    parser.add_argument(
        "--model",
        default="tiny.en",
        help="Whisper model size: tiny.en, small, medium, large-v3.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="`auto`, `cpu`, `cuda`, `mps`.",
    )
    parser.add_argument(
        "--compute-type",
        default="default",
        help="faster-whisper compute_type (e.g. int8, float16).",
    )
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Wrap the backend in SlidingWindowWrapper (matches live mode).",
    )
    parser.add_argument(
        "--window-seconds",
        type=float,
        default=6.0,
    )
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=1.0,
    )
    parser.add_argument(
        "--final-only",
        action="store_true",
        help="Suppress is_final=False (partial) events.",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Pace file capture at wall-clock (matches a live mic).",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=None,
        help="sounddevice index when --input=mic.",
    )
    parser.add_argument(
        "--language",
        default="en",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="Stop after this many seconds (live mic safety valve).",
    )
    parser.add_argument(
        "--emit-time",
        action="store_true",
        help=(
            "Annotate each output event with `emit_t_seconds` (wall-clock "
            "monotonic since transcription start) and `audio_t_seconds` "
            "(t_end of the latest audio frame consumed at emit time). With "
            "--realtime, `(emit_t_seconds - audio_t_seconds)` is the "
            "utterance-to-event latency Phase 4's < 2s criterion measures."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
    )
    return parser


def build_capture(args: argparse.Namespace) -> Iterable[AudioFrame]:
    target = args.input
    if target == "mic":
        # Imported lazily so users without sounddevice can still run file mode.
        from auto_presenter.audio.live_capture import LiveCapture

        cap = LiveCapture(device=args.device_index)
        cap.start()
        # Install a clean Ctrl-C handler so mic capture stops promptly.
        _install_sigint_handler(cap.stop)
        return _limit(cap.stream(), max_seconds=args.max_seconds)
    if target.startswith("file://"):
        path = Path(target[len("file://"):])
    else:
        path = Path(target)
    if not path.exists():
        raise SystemExit(f"input file does not exist: {path}")
    cap = FileCapture(path, realtime=args.realtime)
    return _limit(cap.stream(), max_seconds=args.max_seconds)


def build_backend(args: argparse.Namespace) -> ASRBackend:
    if args.backend == "whisper":
        from auto_presenter.asr.whisper_backend import WhisperBackend

        return WhisperBackend.load(
            model_size=args.model,
            device=args.device,
            compute_type=args.compute_type,
            language=args.language,
        )
    if args.backend == "mock":
        # A trivially-deterministic schedule so the CLI is exercisable
        # without any model. Real eval should use --backend whisper.
        schedule = schedule_from_words(
            [("amazing", 0.5, 1.0), ("grace", 1.0, 1.4), ("how", 1.4, 1.7), ("sweet", 1.7, 2.1)]
        )
        return MockASRBackend(schedule=schedule)
    raise SystemExit(f"unknown backend: {args.backend}")


class _FrameWatcher:
    """Pass-through iterator that records the most recently consumed frame.

    Wrapping the audio iterator (rather than the backend) keeps the
    instrumentation independent of which backend is in use — including
    `SlidingWindowWrapper`, which buffers internally. `last_frame_t_end`
    is the audio-timeline mark Whisper "saw" at the moment we yielded a
    frame; pair it with `time.monotonic()` to compute wall-clock latency
    when the emit-time annotation is on.
    """

    def __init__(self, frames: Iterable[AudioFrame]) -> None:
        self._frames = frames
        self.last_frame_t_end: float = 0.0

    def __iter__(self) -> Iterator[AudioFrame]:
        for f in self._frames:
            self.last_frame_t_end = f.t_end
            yield f


def emit_events(
    events: Iterator[TranscriptEvent],
    *,
    final_only: bool,
    emit_time: bool = False,
    t0_monotonic: float | None = None,
    frame_watcher: _FrameWatcher | None = None,
) -> None:
    if emit_time and t0_monotonic is None:
        t0_monotonic = time.monotonic()
    for ev in events:
        if final_only and not ev.is_final:
            continue
        payload = _to_json_dict(ev)
        if emit_time:
            assert t0_monotonic is not None
            payload["emit_t_seconds"] = time.monotonic() - t0_monotonic
            payload["audio_t_seconds"] = (
                frame_watcher.last_frame_t_end if frame_watcher else None
            )
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _to_json_dict(event: TranscriptEvent) -> dict:
    return {
        "segment_id": event.segment_id,
        "is_final": event.is_final,
        "text": event.text,
        "t_start": event.t_start,
        "t_end": event.t_end,
        "words": [
            {"text": w.text, "t_start": w.t_start, "t_end": w.t_end, "confidence": w.confidence}
            for w in event.words
        ],
    }


def _limit(
    frames: Iterable[AudioFrame], *, max_seconds: float | None
) -> Iterator[AudioFrame]:
    if max_seconds is None:
        yield from frames
        return
    for frame in frames:
        yield frame
        if frame.t_end >= max_seconds:
            return


def _install_sigint_handler(stop_fn) -> None:
    def _handler(signum, frame) -> None:  # type: ignore[no-untyped-def]
        del signum, frame
        log.info("SIGINT received — stopping capture")
        stop_fn()

    signal.signal(signal.SIGINT, _handler)


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    backend = build_backend(args)
    if args.streaming:
        backend = SlidingWindowWrapper(
            backend=backend,
            window_seconds=args.window_seconds,
            step_seconds=args.step_seconds,
        )

    log.info("using backend: %s", backend.name())
    frames = build_capture(args)
    watcher: _FrameWatcher | None = None
    if args.emit_time:
        watcher = _FrameWatcher(frames)
        frames = iter(watcher)
    events = backend.transcribe(frames)
    emit_events(
        events,
        final_only=args.final_only,
        emit_time=args.emit_time,
        t0_monotonic=time.monotonic() if args.emit_time else None,
        frame_watcher=watcher,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
