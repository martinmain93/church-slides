"""Score recordings through the REAL ASR → matcher pipeline.

Unlike `run_baseline.py` / `sweep_pf.py`, which feed the matcher a
hermetic transcript synthesized from ground-truth timing, this script
runs the recording's actual `audio.wav` through Whisper (faster-whisper)
exactly the way the live app does — `SlidingWindowWrapper` over a
`WhisperBackend` — then replays the resulting transcript events through
the matcher engine and scores the AI's slide-change decisions against
the recorded ground truth.

This is "run the eval with the AI slide switching" on real audio.

    uv run --project sidecar python research/eval/run_realtime_eval.py \\
        --recordings ~/Library/Application\\ Support/com.churchslides.app/recordings \\
        --model tiny.en \\
        --output docs/done/REALTIME_EVAL.md

The Whisper model is loaded once and reused across recordings, and the
transcript is computed once per recording and cached, so re-scoring after a
tracker or scheduler change is cheap. (The Phase 5 naive-matcher baseline
arm went with the naive matcher on 2026-10-19:
research/dead_ends/naive_matcher.) Matcher params mirror the production defaults
(`ParticleFilterTunables`, `SlidingWindowWrapper` defaults).
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.engine import MatchingEngine
from auto_presenter.decision.production import build_engine
from auto_presenter.eval.compare import PipelineRun, evaluate
from auto_presenter.eval.metrics import DEFAULT_DEVIATION_GRACE_S
from auto_presenter.eval.replay import Replayer, load_recording
from auto_presenter.eval.types import Decision, Recording
from auto_presenter.index.library import build_index
from auto_presenter.protocol import (
    ParticleFilterTunables,
    SetlistContent,
    SetlistItemContent,
)

log = logging.getLogger("run_realtime_eval")


# --------------------------------------------------------------------- ASR

# Production streaming defaults (SlidingWindowWrapper field defaults).
WINDOW_SECONDS = 6.0
STEP_SECONDS = 1.0
STABLE_MARGIN_SECONDS = 1.5


def _cache_key(model: str, window: float, step: float, margin: float) -> str:
    """Everything that changes the transcript, and nothing that does not."""
    return f"{model}|w{window:g}|s{step:g}|m{margin:g}"


def load_event_cache(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        log.warning("ignoring unreadable event cache at %s", path)
        return {}


def events_to_json(events: list[TranscriptEvent]) -> list[dict]:
    return [
        {"t_end": e.t_end, "segment_id": e.segment_id,
         "words": [{"text": w.text, "t_start": w.t_start, "t_end": w.t_end}
                   for w in e.words]}
        for e in events
    ]


def events_from_json(raw: list[dict]) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            words=[Word(text=w["text"], t_start=w["t_start"], t_end=w["t_end"])
                   for w in e["words"]],
            is_final=True,
            segment_id=e["segment_id"],
        )
        for e in raw
    ]


def transcribe_recording(
    recording: Recording,
    backend,  # noqa: ANN001 - WhisperBackend (or any ASRBackend)
    *,
    window: float = WINDOW_SECONDS,
    step: float = STEP_SECONDS,
    margin: float = STABLE_MARGIN_SECONDS,
) -> list[TranscriptEvent]:
    """Run the recording's audio through Whisper, return final events.

    Mirrors the live `AudioSession` composition exactly: a fresh
    `SlidingWindowWrapper` (state is per-session) over the shared backend,
    fed the recording's audio frames non-realtime (as fast as possible).
    """
    wrapper = SlidingWindowWrapper(
        backend,
        window_seconds=window,
        step_seconds=step,
        stable_margin_seconds=margin,
    )
    frames = Replayer(recording, realtime=False).audio_frames()
    return [e for e in wrapper.transcribe(frames) if e.is_final]


# --------------------------------------------------------------------- matchers


def _content(recording: Recording) -> SetlistContent:
    return SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=s.item_idx,
                song_id=s.song_id,
                slides=list(s.slides),
                slide_sections=list(s.slide_sections),
                title=s.title,
                spoken=s.spoken,
            )
            for s in recording.setlist_content
        ]
    )


def _decisions_from_engine(
    engine: MatchingEngine, events: list[TranscriptEvent]
) -> list[Decision]:
    out: list[Decision] = []
    for event in events:
        decision = engine.update(event)
        if decision is not None:
            out.append(
                Decision(
                    t_seconds=decision.t_seconds,
                    item_idx=decision.item_idx,
                    slide_idx=decision.slide_idx,
                    source="ai",
                )
            )
    return out


def build_pf_engine(recording: Recording, tunables: ParticleFilterTunables) -> MatchingEngine:
    """The engine the app runs, built the one way it is built.

    This used to reconstruct a `ParticleFilter` by hand under the heading
    "Particle filter (production default)". It kept reporting numbers long
    after the default became the forward filter — 0.844 wrong-slide for a
    matcher nothing runs — which is the failure mode `decision.production`
    exists to end.
    """
    return build_engine(build_index(_content(recording)), tunables)


# --------------------------------------------------------------------- driver


@dataclass
class RecordingResult:
    recording: Recording
    transcript: list[TranscriptEvent]
    pf_decisions: list[Decision]
    #: The lead the scheduler had derived by the end of this recording, in
    #: seconds. Not a setting — `delivery_lag + gap_to_next_update +
    #: target_margin_ms`, of which only the last term was chosen.
    derived_lead_seconds: float = 0.0


def run(
    recordings: list[Recording],
    *,
    model: str,
    device: str,
    compute_type: str,
    tunables: ParticleFilterTunables,
    window: float = WINDOW_SECONDS,
    step: float = STEP_SECONDS,
    margin: float = STABLE_MARGIN_SECONDS,
    cache_path: Path | None = None,
) -> list[RecordingResult]:
    # Transcription is ~100x the cost of everything else here: 35 minutes of
    # audio becomes ~2000 Whisper passes, because a 6 s window stepped by 1 s
    # transcribes every second of audio six times over and pays the full 30 s
    # encoder toll each time. Replaying both engines over the resulting events
    # takes about 7 seconds.
    #
    # So a change that cannot affect the transcript — anything in the tracker,
    # the scheduler, the metrics — has no business re-running Whisper. The
    # cache is keyed on exactly the things that *do* change it, and a run whose
    # ASR settings differ simply misses and re-transcribes.
    key = _cache_key(model, window, step, margin)
    cache = load_event_cache(cache_path)
    cached = cache.get(key, {})
    needed = [r for r in recordings if r.id not in cached]

    backend = None
    if needed:
        from auto_presenter.asr.whisper_backend import WhisperBackend

        log.info("loading whisper backend model=%s device=%s", model, device)
        backend = WhisperBackend.load(
            model_size=model, device=device, compute_type=compute_type,
            language="en",
        )
    elif recordings:
        log.info("every recording served from the event cache (%s)", key)

    results: list[RecordingResult] = []
    for rec in recordings:
        if not rec.setlist_content:
            log.warning("skipping %s: no setlist_content", rec.id)
            continue
        if rec.id in cached:
            events = events_from_json(cached[rec.id])
            log.info("cache hit %s (%d events)", rec.id, len(events))
        else:
            log.info("transcribing %s (%.1fs)", rec.id, rec.duration_seconds)
            events = transcribe_recording(
                rec, backend, window=window, step=step, margin=margin
            )
            cached[rec.id] = events_to_json(events)
        pf_engine = build_pf_engine(rec, tunables)
        pf = _decisions_from_engine(pf_engine, events)
        results.append(
            RecordingResult(
                recording=rec,
                transcript=events,
                pf_decisions=pf,
                derived_lead_seconds=pf_engine.scheduler.effective_lead_seconds(),
            )
        )
    if cache_path is not None:
        cache[key] = cached
        cache_path.write_text(json.dumps(cache))
    return results


# --------------------------------------------------------------------- report


def _fmt_decisions(decisions: list[Decision]) -> str:
    if not decisions:
        return "_(none)_"
    return ", ".join(
        f"`t={d.t_seconds:.2f}s → (item {d.item_idx}, slide {d.slide_idx})`"
        for d in decisions
    )


def _median_lead_ms(results: list[RecordingResult]) -> str:
    leads = [r.derived_lead_seconds for r in results]
    return f"{statistics.median(leads) * 1000:.0f} ms" if leads else "—"


def _fmt_anticip(values: list[float]) -> str:
    if not values:
        return "— (no matched slide)"
    med = statistics.median(values)
    return f"{med:+.0f} ms (negative = anticipated early)"


def render_markdown(
    results: list[RecordingResult],
    *,
    model: str,
    tunables: ParticleFilterTunables,
    window: float = WINDOW_SECONDS,
    step: float = STEP_SECONDS,
    margin: float = STABLE_MARGIN_SECONDS,
    forgive_unpredictable: bool = True,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Realtime eval — real Whisper ASR → matcher",
        "",
        f"_Generated: {now}_",
        # Report what was actually run, never the module defaults — a header
        # that describes a configuration the run did not use is how three
        # earlier harnesses came to report numbers for the wrong thing.
        f"_ASR: faster-whisper `{model}`, sliding window "
        f"{window:g}s / step {step:g}s / stable margin {margin:g}s"
        + (
            " (production defaults)"
            if (window, step, margin)
            == (WINDOW_SECONDS, STEP_SECONDS, STABLE_MARGIN_SECONDS)
            else " (**overridden**)"
        )
        + "_",
        # (n_particles went with the particle filter, 2026-10-19.)
        f"_Matcher tunables: "
        f"target_margin_ms={tunables.target_margin_ms}, "
        f"marginal_threshold={tunables.marginal_threshold}_",
        # The lead is not a tunable any more, so quoting one would describe a
        # setting that does not exist. It is derived per run from the measured
        # delivery lag and belief-update gap; report what the runs arrived at.
        f"_Lead time: derived (`delivery_lag + gap_to_next_update + "
        f"target_margin_ms`), median across runs "
        f"{_median_lead_ms(results)}_",
        f"_Recordings scored: **{len(results)}**_",
        # Which scoring mode produced these numbers, in the header, for the
        # same reason the ASR settings are: a figure whose scoring rule is
        # implicit gets compared against one measured a different way.
        "_Scoring: "
        + (
            "unpredictable jumps get a "
            f"{DEFAULT_DEVIATION_GRACE_S:g}s recovery window (default)"
            if forgive_unpredictable
            else "**strict** — no recovery window, comparable with earlier baselines"
        )
        + "_",
        "",
    ]

    pf_runs = evaluate(
        [r.recording for r in results],
        _pipeline_from(results, "pf"),
        forgive_unpredictable=forgive_unpredictable,
    )

    lines += ["## Forward filter (production default)", ""]
    lines += _pipeline_section(pf_runs)

    lines += ["", "## Per-recording detail", ""]
    for r in results:
        rec = r.recording
        gt = ", ".join(
            f"`t={g.t_seconds:.2f}s → (item {g.item_idx}, slide {g.slide_idx})`"
            for g in rec.ground_truth
        )
        transcript_text = " ".join(
            w.text for e in r.transcript for w in e.words
        )
        lines += [
            f"### `{rec.id}`",
            "",
            f"- Duration: {rec.duration_seconds:.1f}s · "
            f"ground-truth changes: {len(rec.ground_truth)}",
            f"- Ground truth: {gt or '_(none)_'}",
            f"- PF decisions: {_fmt_decisions(r.pf_decisions)}",
            f"- Transcript (final): _{transcript_text}_",
            "",
        ]

    if any(len(r.recording.ground_truth) < 2 for r in results):
        lines += [
            "> **Note:** `wrong_slide_rate` is defined as 0.0 for recordings "
            "with fewer than 2 ground-truth changes (no interval to score "
            "over). For such clips the meaningful signals are the "
            "anticipation error and the decision list above.",
            "",
        ]
    return "\n".join(lines) + "\n"


def _pipeline_from(results: list[RecordingResult], which: str):
    by_id = {r.recording.id: r.pf_decisions for r in results}

    def pipeline(recording: Recording) -> list[Decision]:
        return by_id.get(recording.id, [])

    return pipeline


def _pipeline_section(runs: list[PipelineRun]) -> list[str]:
    anticip = [v for r in runs for v in r.anticipation_error_ms]
    n_decisions = sum(len(r.decisions) for r in runs)
    matched = len(anticip)
    out = [
        "| Metric | Value |",
        "|---|---:|",
        f"| total AI decisions | {n_decisions} |",
        f"| slides matched to ground truth | {matched} |",
        f"| anticipation error (median) | {_fmt_anticip(anticip)} |",
        f"| wrong_slide_rate (mean) | "
        f"{statistics.fmean(r.wrong_slide_rate for r in runs):.3f} |"
        if runs
        else "| wrong_slide_rate | n/a |",
    ]
    return out


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--recordings", required=True)
    parser.add_argument("--output", default=str(REPO_ROOT / "docs" / "done" / "REALTIME_EVAL.md"))
    parser.add_argument("--json", default=None)
    parser.add_argument("--model", default="tiny.en", help="Whisper model size.")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="default")
    parser.add_argument("--window", type=float, default=WINDOW_SECONDS)
    parser.add_argument("--step", type=float, default=STEP_SECONDS)
    parser.add_argument("--margin", type=float, default=STABLE_MARGIN_SECONDS)
    parser.add_argument(
        "--events-cache",
        type=Path,
        help="Reuse (and update) transcripts here, keyed on the ASR settings. "
             "Turns a tracker-only change from ~12 minutes into ~7 seconds.",
    )
    parser.add_argument(
        "--strict-scoring",
        action="store_true",
        help=(
            "Score the band's unpredictable jumps unforgivingly, with no "
            "recovery window. Off by default: recovering from a jump inside "
            "a couple of seconds is the tracker doing its job, not an error. "
            "Turn it on to compare against this repo's earlier baselines, or "
            "to measure a model that claims it can *predict* those jumps — "
            "the forgiving mode excuses exactly the events such a model "
            "would be showing skill on."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def load_recordings(root: Path) -> list[Recording]:
    if not root.exists():
        raise SystemExit(f"recordings dir does not exist: {root}")
    out: list[Recording] = []
    for entry in sorted(root.iterdir()):
        if (entry / "recording.json").exists():
            try:
                out.append(load_recording(entry))
            except Exception as exc:
                log.warning("skipping %s: %s", entry, exc)
    if not out:
        raise SystemExit(f"no recordings found under {root}")
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    recordings = load_recordings(Path(args.recordings))
    tunables = ParticleFilterTunables()  # production defaults
    results = run(
        recordings,
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        tunables=tunables,
        window=args.window,
        step=args.step,
        margin=args.margin,
        cache_path=args.events_cache,
    )

    report = render_markdown(
        results, model=args.model, tunables=tunables,
        window=args.window, step=args.step, margin=args.margin,
        forgive_unpredictable=not args.strict_scoring,
    )
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"wrote {out_path}")
    print()
    print(report)

    if args.json:
        payload = {
            "model": args.model,
            "recordings": [
                {
                    "recording_id": r.recording.id,
                    "ground_truth": [
                        {"t_seconds": g.t_seconds, "item_idx": g.item_idx, "slide_idx": g.slide_idx}
                        for g in r.recording.ground_truth
                    ],
                    "pf_decisions": [
                        {"t_seconds": d.t_seconds, "item_idx": d.item_idx, "slide_idx": d.slide_idx}
                        for d in r.pf_decisions
                    ],
                }
                for r in results
            ],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
