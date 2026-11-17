"""Score continuous (scrolling) display against slide display, on the room's clock.

Slide mode asks the engine *when to switch*. Continuous mode shows one column
of lyrics and asks it only to keep the sung words near an anchor line, so
being a second late costs nothing as long as the sung line is still on screen.
This measures whether that is true for this engine on the approved goldens.

The question both modes are asked is the same: **at each moment, can the
congregation see the line being sung?** Slide mode passes when the displayed
slide carries that line's text; continuous mode passes when the line's text is
inside the readable part of the viewport. A second question, *can they also
see the next line?*, captures reading ahead.

Timing is on the **availability clock**: every display change is stamped with
when the event that caused it was actually delivered (`available_at` from the
streaming wrapper), not when the words were sung. Scoring on the sung clock
made the shipped config read +214 ms late when the room saw +3035 ms.

    uv run --project sidecar python research/eval/eval_continuous.py \\
        --recordings <dir of approved goldens> \\
        --events-cache <path>.json

**Ground-truth caveat.** Goldens mark slide transitions, not words. The sung
position inside a slide is interpolated uniformly across that slide's time on
screen, so any instrumental tail is spread over the lyric. That blurs *where in
the slide* by up to a slide's length, which is why the headline metrics are
line-level visibility rather than word-level error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from auto_presenter.asr.streaming import SlidingWindowWrapper  # noqa: E402
from auto_presenter.asr.types import TranscriptEvent  # noqa: E402
from auto_presenter.eval.replay import Replayer  # noqa: E402
from auto_presenter.decision.flow import FlowConfig, FlowFollower, FlowTrack  # noqa: E402
from auto_presenter.decision.production import build_engine  # noqa: E402
from auto_presenter.eval.types import Recording  # noqa: E402
from auto_presenter.index.library import build_index  # noqa: E402
from auto_presenter.protocol import ParticleFilterTunables  # noqa: E402
from run_realtime_eval import _content, load_recordings  # noqa: E402

log = logging.getLogger("eval_continuous")


# ----------------------------------------------------------------- transcripts


@dataclass
class TimedEvent:
    event: TranscriptEvent
    #: Audio-timeline second at which this event reached the matcher.
    available_at: float


def transcribe(recording: Recording, backend) -> list[TimedEvent]:  # noqa: ANN001
    """The live wrapper over `backend`, keeping each final's delivery time."""
    out: list[TimedEvent] = []

    def observe(event: TranscriptEvent, available_at: float) -> None:
        if event.is_final:
            out.append(TimedEvent(event, available_at))

    wrapper = SlidingWindowWrapper(backend, emission_observer=observe)
    for _ in wrapper.transcribe(Replayer(recording, realtime=False).audio_frames()):
        pass
    return out


def load_or_transcribe(
    recordings: list[Recording], cache_path: Path, model: str
) -> dict[str, list[TimedEvent]]:
    cache: dict = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    per_model: dict = cache.setdefault(model, {})
    backend = None
    for rec in recordings:
        if rec.id in per_model:
            continue
        if backend is None:
            from auto_presenter.asr.mlx_backend import MLXWhisperBackend

            backend = MLXWhisperBackend(model_size=model)
            backend.warm()
        log.warning("transcribing %s (%.0fs)", rec.id, rec.duration_seconds)
        timed = transcribe(rec, backend)
        per_model[rec.id] = [
            {"event": t.event.model_dump(), "available_at": t.available_at}
            for t in timed
        ]
        # Written after every recording: a crash an hour in keeps the hour.
        cache_path.write_text(json.dumps(cache))
    return {
        rec_id: [
            TimedEvent(TranscriptEvent.model_validate(e["event"]), e["available_at"])
            for e in raw
        ]
        for rec_id, raw in per_model.items()
    }


# ---------------------------------------------------------------------- layout

#: Fallback line length when a slide's line breaks cannot be recovered.
WORDS_PER_LINE = 6


@dataclass(frozen=True)
class Line:
    slide_idx: int
    text: tuple[str, ...]
    #: Global word index of the line's first word, and one past its last.
    start: int
    end: int
    #: Vertical position in line units, counting the one-line gap between
    #: slides, as `FlowStage` lays the column out.
    y: float


@dataclass
class Layout:
    """One item's continuous column: every lyric line with its height."""

    lines: list[Line]

    def y_of(self, word: float) -> float:
        """Line coordinate of a (fractional) global word index."""
        for line in self.lines:
            if word < line.end:
                span = max(1, line.end - line.start)
                return line.y + max(0.0, word - line.start) / span
        return self.lines[-1].y + 1.0 if self.lines else 0.0

    def line_at(self, word: float) -> Line | None:
        for line in self.lines:
            if line.start <= word < line.end:
                return line
        return None


def library_line_breaks(db_path: Path) -> dict[tuple[str, ...], list[int]]:
    """Slide words -> words per displayed line, from every song in the library."""
    import sqlite3

    from auto_presenter.library.parser import parse_body_text, tokenize

    out: dict[tuple[str, ...], list[int]] = {}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        for song_id, body in conn.execute("SELECT id, body_text FROM songs"):
            slides, _ = parse_body_text(body, song_id)
            for slide in slides:
                counts = [len(tokenize(line)) for line in slide.lyrics.split("\n")]
                out.setdefault(tuple(slide.words), [c for c in counts if c])
    return out


def build_layout(slides: list[list[str]], breaks: dict) -> tuple[Layout, int]:
    """Lay out one item; also return how many slides needed the fallback."""
    lines: list[Line] = []
    g, y, fallback = 0, 0.0, 0
    for slide_idx, words in enumerate(slides):
        if not words:
            continue
        counts = breaks.get(tuple(words))
        if not counts or sum(counts) != len(words):
            fallback += 1
            counts = [
                min(WORDS_PER_LINE, len(words) - k)
                for k in range(0, len(words), WORDS_PER_LINE)
            ]
        k = 0
        for c in counts:
            lines.append(Line(slide_idx, tuple(words[k : k + c]), g + k, g + k + c, y))
            k += c
            y += 1.0
        y += 1.0  # gap between slides
        g += len(words)
    return Layout(lines), fallback


# --------------------------------------------------------------------- scoring


@dataclass(frozen=True)
class Viewport:
    lines: float
    #: Fraction of the viewport above the anchor line.
    anchor: float = 0.4
    #: Lines at each edge lost to the fade.
    fade: float = 0.75

    def readable(self, y_anchor: float) -> tuple[float, float]:
        top = y_anchor - self.anchor * self.lines + self.fade
        bottom = y_anchor + (1 - self.anchor) * self.lines - self.fade
        return top, bottom


@dataclass
class Tally:
    samples: int = 0
    sung_visible: int = 0
    next_visible: int = 0
    near_anchor: int = 0
    #: Lines scrolled backwards. Each one costs a reader their place.
    back_travel: float = 0.0
    jumps: int = 0
    #: Total scroll distance in lines, to compare against the lyric's own
    #: length: a column that wanders reads as twice the distance.
    travel: float = 0.0

    def add(self, other: "Tally") -> None:
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(other, k))


@dataclass(frozen=True)
class Sung:
    """Ground truth at one instant: which line is being sung, and the next."""

    item_pos: int
    slide_idx: int
    word: float
    line: Line
    next_line: Line | None


def ground_truth_track(
    rec: Recording, layouts: dict[int, Layout], pos_of: dict[int, int], step: float
) -> list[tuple[float, Sung]]:
    """Sample the sung line every `step` seconds, interpolating inside slides."""
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    out: list[tuple[float, Sung]] = []
    for i, ev in enumerate(gt):
        pos = pos_of.get(ev.item_idx)
        if pos is None:
            continue
        slides = rec.setlist_content[pos].slides
        if not 0 <= ev.slide_idx < len(slides) or not slides[ev.slide_idx]:
            continue
        end = gt[i + 1].t_seconds if i + 1 < len(gt) else rec.duration_seconds
        layout = layouts[pos]
        start_word = sum(len(s) for s in slides[: ev.slide_idx])
        n = len(slides[ev.slide_idx])
        t = ev.t_seconds
        while t < end:
            word = start_word + n * (t - ev.t_seconds) / max(1e-6, end - ev.t_seconds)
            line = layout.line_at(word)
            if line is not None:
                idx = layout.lines.index(line)
                nxt = layout.lines[idx + 1] if idx + 1 < len(layout.lines) else None
                out.append((t, Sung(pos, ev.slide_idx, word, line, nxt)))
            t += step
    return out


def score_slides(
    samples: list[tuple[float, Sung]],
    shown: list[tuple[float, int, int]],
    rec: Recording,
    first: tuple[int, int],
) -> Tally:
    """Slide mode: the line is visible when the displayed slide carries it."""
    tally = Tally()
    k, current = 0, first
    for t, sung in samples:
        while k < len(shown) and shown[k][0] <= t:
            current = (shown[k][1], shown[k][2])
            k += 1
        tally.samples += 1
        item, slide = current
        slides = rec.setlist_content[item].slides if item < len(rec.setlist_content) else []
        words = tuple(slides[slide]) if 0 <= slide < len(slides) else ()
        truth = tuple(rec.setlist_content[sung.item_pos].slides[sung.slide_idx])
        if words and words == truth:
            tally.sung_visible += 1
            if sung.next_line is not None and sung.next_line.slide_idx == sung.slide_idx:
                tally.next_visible += 1
    tally.jumps = len(shown)
    return tally


def score_flow(
    samples: list[tuple[float, Sung]],
    tracks: list[tuple[float, FlowTrack]],
    layouts: dict[int, Layout],
    view: Viewport,
) -> Tally:
    """Continuous mode: the line is visible when its text sits in the readable band."""
    tally = Tally()
    k, current = 0, None
    last_y: float | None = None
    for t, sung in samples:
        while k < len(tracks) and tracks[k][0] <= t:
            current = tracks[k][1]
            if current.jump:
                tally.jumps += 1
                last_y = None
            k += 1
        tally.samples += 1
        if current is None:
            continue
        layout = layouts[current.item_idx]
        y = layout.y_of(current.at(t))
        if last_y is not None:
            tally.back_travel += max(0.0, last_y - y)
            tally.travel += abs(y - last_y)
        last_y = y
        if current.item_idx != sung.item_pos:
            continue
        top, bottom = view.readable(y)

        def visible(line: Line) -> bool:
            # Text, not position: a repeated chorus shows the same words
            # wherever the column happens to hold them.
            return any(
                top <= other.y and other.y + 1.0 <= bottom and other.text == line.text
                for other in layout.lines
            )

        if visible(sung.line):
            tally.sung_visible += 1
            if sung.next_line is not None and visible(sung.next_line):
                tally.next_visible += 1
        if abs(sung.line.y - y) <= 2.0:
            tally.near_anchor += 1
    return tally


def replay(
    rec: Recording, events: list[TimedEvent], flow_config: FlowConfig
) -> tuple[list[tuple[float, int, int]], list[tuple[float, FlowTrack]]]:
    """Both modes over the same events, stamped on the availability clock."""
    indexed = build_index(_content(rec))
    slide_engine = build_engine(indexed, ParticleFilterTunables())
    flow_engine = build_engine(indexed, ParticleFilterTunables())
    follower = FlowFollower(setlist=indexed, config=flow_config)

    shown: list[tuple[float, int, int]] = []
    tracks: list[tuple[float, FlowTrack]] = []
    for timed in events:
        decision = slide_engine.update(timed.event)
        if decision is not None:
            shown.append((timed.available_at, decision.item_idx, decision.slide_idx))
        flow_engine.update(timed.event)
        follower.observe_words([w.t_end for w in timed.event.words])
        if not timed.event.words:
            continue
        track = follower.update(
            flow_engine.filter.filter._alpha,
            t_observed=timed.event.words[-1].t_end,
            t_now=timed.available_at,
        )
        if track is not None:
            tracks.append((timed.available_at, track))
    return shown, tracks


def pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:5.1f}%" if d else "  n/a"


# ------------------------------------------------------------------------ main


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--recordings", type=Path, required=True)
    p.add_argument("--events-cache", type=Path, required=True)
    p.add_argument("--model", default="small.en")
    p.add_argument("--transcribe-only", action="store_true")
    p.add_argument(
        "--library",
        type=Path,
        default=Path.home() / "Library/Application Support/com.churchslides.app/library.db",
        help="Songs DB, for the real line breaks inside each slide.",
    )
    p.add_argument(
        "--viewport-lines",
        type=float,
        nargs="+",
        default=[7.0, 10.0],
        help="Lines the continuous viewport shows. 7 is roughly slide-mode "
             "font size on a 16:9 screen; 10 trades size for context.",
    )
    p.add_argument("--step", type=float, default=0.25)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    recordings = load_recordings(args.recordings)
    timed = load_or_transcribe(recordings, args.events_cache, args.model)
    if args.transcribe_only:
        return 0

    breaks = library_line_breaks(args.library)
    views = [Viewport(lines=n) for n in args.viewport_lines]
    slide_total = Tally()
    flow_totals = {v.lines: Tally() for v in views}
    rows: list[str] = []
    fallback_slides = total_slides = 0

    for rec in recordings:
        pos_of = {s.item_idx: i for i, s in enumerate(rec.setlist_content)}
        layouts: dict[int, Layout] = {}
        for i, item in enumerate(rec.setlist_content):
            layouts[i], fb = build_layout(item.slides, breaks)
            fallback_slides += fb
            total_slides += sum(1 for s in item.slides if s)
        samples = ground_truth_track(rec, layouts, pos_of, args.step)
        shown, tracks = replay(rec, timed[rec.id], FlowConfig())
        s = score_slides(samples, shown, rec, first=(0, 0))
        slide_total.add(s)
        cells = [f"{rec.id[4:]:<20}", pct(s.sung_visible, s.samples), pct(s.next_visible, s.samples)]
        for v in views:
            f = score_flow(samples, tracks, layouts, v)
            flow_totals[v.lines].add(f)
            cells += [pct(f.sung_visible, f.samples), pct(f.next_visible, f.samples)]
        rows.append(" | ".join(cells))

    head = ["recording           ", "slide sung", "slide next"]
    for v in views:
        head += [f"flow{v.lines:g} sung", f"flow{v.lines:g} next"]
    print(f"model {args.model}, {len(recordings)} recordings, "
          f"{fallback_slides}/{total_slides} slides without recovered line breaks")
    print(" | ".join(head))
    for r in rows:
        print(r)
    total = ["TOTAL               ", pct(slide_total.sung_visible, slide_total.samples),
             pct(slide_total.next_visible, slide_total.samples)]
    for v in views:
        f = flow_totals[v.lines]
        total += [pct(f.sung_visible, f.samples), pct(f.next_visible, f.samples)]
    print(" | ".join(total))
    print()
    print(f"slide mode: {slide_total.jumps} slide changes")
    for v in views:
        f = flow_totals[v.lines]
        print(
            f"flow {v.lines:g} lines: near anchor (±2 lines) {pct(f.near_anchor, f.samples)}, "
            f"{f.jumps} jumps, {f.back_travel:.0f} lines scrolled backwards, "
            f"travel {f.travel:.0f} lines"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
