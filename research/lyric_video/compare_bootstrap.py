"""Compare the audio aligner with the lyric-video line clock.

The video says which line was on screen. ``bootstrap_golden``'s Viterbi
aligner hears the audio and picks a line from that same list. This writes
where those two disagree, for a person to check before anyone approves a
golden.

Nothing here is imported by the app, and nothing is marked approved.

    uv run --project sidecar python research/lyric_video/compare_bootstrap.py \\
        research/lyric_video/out/ChurchOnlineWorshipSet_1

A line is "close" when the aligner is on that same line within 2.5 s of the
video putting it up. Later or earlier than that, or a different line, is a
check. Word onset against the burn-in, with no operator lead subtracted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
#: Beyond this, a boundary is worth a person's eyes. Inside it, the burn-in
#: and a sung word onset are the same cue.
CLOSE_S = 2.5


@dataclass(frozen=True)
class Line:
    t: float
    text: str
    frame: str


@dataclass(frozen=True)
class Check:
    t: float
    end: float
    kind: str
    video: str
    bootstrap: str
    lag_s: float | None
    frame: str


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def check_reason(check: Check) -> str:
    """Why a row is on the review list.

    Same letters means the aligner is on another copy of this line (a
    repeated chorus). Close spelling is the same line with a bad read.
    """
    if check.kind in ("late", "early"):
        return check.kind
    video = _letters(check.video)
    heard = _letters(check.bootstrap)
    if video and video == heard:
        return "same letters, other copy"
    if video and heard and SequenceMatcher(None, video, heard).ratio() >= 0.72:
        return "close spelling"
    if not heard:
        return "aligner missed the line"
    return "different lyric"


def shown(events: list[tuple[float, int]], t: float) -> int | None:
    cur: int | None = None
    for et, slide in events:
        if et <= t + 1e-6:
            cur = slide
        else:
            break
    return cur


def first_time(events: list[tuple[float, int]], slide: int, after: float) -> float | None:
    for et, s in events:
        if s == slide and et >= after - 1e-6:
            return et
    return None


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def time_on_slide(
    events: list[tuple[float, int]],
    slide: int,
    t0: float,
    t1: float,
    blanks: list[tuple[float, float]],
    letters: list[str] | None = None,
) -> tuple[float, float, float]:
    """Seconds on this slide index, seconds scored, seconds on the same letters.

    Blanks between songs are not scored. Same letters counts a repeated
    chorus line whose text matches even when the index does not.
    """
    if t1 <= t0:
        return 0.0, 0.0, 0.0
    cuts = [t0, t1]
    for et, _ in events:
        if t0 < et < t1:
            cuts.append(et)
    for b0, b1 in blanks:
        if t0 < b0 < t1:
            cuts.append(b0)
        if t0 < b1 < t1:
            cuts.append(b1)
    cuts = sorted(set(cuts))
    on = scored = same_letters = 0.0
    key = letters[slide] if letters else ""
    for a, b in zip(cuts, cuts[1:]):
        if any(overlap(a, b, b0, b1) > 0 and b0 <= a and b1 >= b for b0, b1 in blanks):
            continue
        scored += b - a
        cur = shown(events, a)
        if cur == slide:
            on += b - a
            same_letters += b - a
        elif key and cur is not None and 0 <= cur < len(letters or []) and letters[cur] == key:
            same_letters += b - a
    return on, scored, same_letters


def compare(
    lines: list[Line],
    events: list[tuple[float, int]],
    blanks: list[tuple[float, float]],
    end: float,
    *,
    close_s: float = CLOSE_S,
) -> tuple[list[Check], dict]:
    """Checks a person should look at, plus the time-agreement fraction."""
    checks: list[Check] = []
    on_total = scored_total = letters_total = 0.0
    letter_keys = [_letters(line.text) for line in lines]
    lags: list[float] = []
    for i, line in enumerate(lines):
        t1 = lines[i + 1].t if i + 1 < len(lines) else end
        # Don't score across a song gap; the next line starts the next song.
        for b0, b1 in blanks:
            if line.t < b0 < t1:
                t1 = b0
                break
        on, scored, same_letters = time_on_slide(
            events, i, line.t, t1, blanks, letter_keys
        )
        on_total += on
        scored_total += scored
        letters_total += same_letters
        heard = first_time(events, i, line.t - close_s)
        lag = None if heard is None else heard - line.t
        if lag is not None and abs(lag) <= 30:
            lags.append(lag)
        wrong = scored - on
        other = shown(events, line.t + 0.05)
        other_text = lines[other].text if other is not None and 0 <= other < len(lines) else ""
        if heard is None or (scored > 0 and on < 0.5 * scored and wrong >= close_s):
            checks.append(
                Check(line.t, t1, "different line", line.text, other_text, lag, line.frame)
            )
        elif lag is not None and lag > close_s:
            covered = sum(overlap(line.t, line.t + lag, b0, b1) for b0, b1 in blanks)
            if lag - covered > close_s:
                checks.append(
                    Check(line.t, min(t1, line.t + lag), "late", line.text, line.text, lag, line.frame)
                )
        elif lag is not None and lag < -close_s:
            checks.append(Check(heard, line.t, "early", line.text, line.text, lag, line.frame))
    lags_sorted = sorted(lags)
    median = lags_sorted[len(lags_sorted) // 2] if lags_sorted else None
    summary = {
        "video_lines": len(lines),
        "aligner_cues": len(events),
        "scored_seconds": round(scored_total, 1),
        "seconds_on_same_line": round(on_total, 1),
        "same_line": round(on_total / scored_total, 3) if scored_total else None,
        "seconds_on_same_letters": round(letters_total, 1),
        "same_letters": round(letters_total / scored_total, 3) if scored_total else None,
        "median_lag_s": None if median is None else round(median, 2),
        "checks": len(checks),
        "check_reasons": dict(Counter(check_reason(c) for c in checks)),
    }
    return checks, summary


def load_lines(doc: dict) -> tuple[list[Line], list[tuple[float, float]], float]:
    cues = [Line(c["t"], c["text"], c.get("frame") or "") for c in doc["cues"]]
    blanks = [(b["t"], b["end"]) for b in doc.get("blanks") or [] if b.get("dur", 0) >= 8]
    end = cues[-1].t if cues else 0.0
    if doc.get("limit_seconds"):
        end = max(end, float(doc["start_seconds"]) + float(doc["limit_seconds"]))
    return cues, blanks, end


def write_review(dest: Path, summary: dict, checks: list[Check], lines: list[Line]) -> None:
    payload = {
        "note": (
            "Aligner versus the lyric video. Not approved. "
            f"A check is a boundary more than {CLOSE_S:g} s off, or a different line."
        ),
        **summary,
        "checks": [c.__dict__ for c in checks],
    }
    (dest / "bootstrap_review.json").write_text(json.dumps(payload, indent=2))
    rows = []
    for c in checks:
        img = f"frames/{c.frame}" if c.frame else ""
        thumb = f'<img src="{img}" alt="">' if img else ""
        reason = check_reason(c)
        rows.append(
            f'<tr class="{_css(reason)}">'
            f"<td>{c.t:.1f}–{c.end:.1f}</td>"
            f"<td>{_esc(reason)}</td>"
            f"<td>{'' if c.lag_s is None else f'{c.lag_s:+.1f} s'}</td>"
            f"<td>{_esc(c.video)}</td>"
            f"<td>{_esc(c.bootstrap)}</td>"
            f"<td>{thumb}</td>"
            "</tr>"
        )
    lag = "—" if summary["median_lag_s"] is None else f'{summary["median_lag_s"]:+.2f} s'
    same = "—" if summary["same_line"] is None else f'{summary["same_line"]:.0%}'
    letters = "—" if summary.get("same_letters") is None else f'{summary["same_letters"]:.0%}'
    reasons = summary.get("check_reasons") or {}
    reason_s = ", ".join(f"{n} {name}" for name, n in sorted(reasons.items(), key=lambda kv: -kv[1]))
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Aligner check</title>
<style>
body {{ font: 15px/1.4 -apple-system, sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border-bottom: 1px solid #ccc; text-align: left; vertical-align: top; padding: 8px; }}
img {{ width: 280px; }}
tr.different td {{ background: #fff4f4; }}
tr.spelling td {{ background: #fff8e8; }}
</style></head><body>
<h1>Where the aligner disagrees with the video</h1>
<p>Same line index for {same} of the scored time. Same letters
(a repeated chorus still counts) for {letters}. Median lag {lag}
(positive means the aligner is later than the burn-in).
{summary["checks"]} checks{(': ' + reason_s) if reason_s else ''}. Not approved.</p>
<p>Start with the pink rows, marked different lyric. Yellow rows are a
misspelled read of the same line. The frame is the video at that moment.</p>
<table>
<tr><th>Time</th><th>Check</th><th>Lag</th><th>Video</th><th>Aligner</th><th>Frame</th></tr>
{"".join(rows) if rows else "<tr><td colspan=6>No disagreements past the threshold.</td></tr>"}
</table>
</body></html>
"""
    (dest / "bootstrap_review.html").write_text(html)
    del lines


def _css(reason: str) -> str:
    if reason == "different lyric":
        return "different"
    if reason == "close spelling":
        return "spelling"
    return ""


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def align_audio(wav: Path, lines: list[Line], model: str) -> list[tuple[float, int]]:
    sys.path.insert(0, str(REPO / "research" / "eval"))
    import bootstrap_golden as bg

    hyp = bg.transcribe_cached(wav, model, wav.with_name(f"transcript_{model}.json"))
    slides = [_words(line.text) or [" "] for line in lines]
    path = bg.decode_slides(hyp, slides, blank_indices=set())
    return bg.runs_to_events(path, hyp)


def run_dir(folder: Path, model: str) -> dict:
    doc = json.loads((folder / "lines.json").read_text())
    lines, blanks, end = load_lines(doc)
    wav = folder / "audio.wav"
    if not wav.exists():
        raise SystemExit(f"{folder.name}: no audio.wav next to lines.json")
    if not lines:
        raise SystemExit(f"{folder.name}: no lyric lines")
    events = align_audio(wav, lines, model)
    checks, summary = compare(lines, events, blanks, end if end > lines[-1].t else lines[-1].t + 8)
    write_review(folder, summary, checks, lines)
    (folder / "bootstrap_cues.json").write_text(
        json.dumps([{"t": round(t, 3), "line": i, "text": lines[i].text} for t, i in events], indent=2)
    )
    return summary


def self_test() -> None:
    lines = [
        Line(0, "ALPHA ONE", "a.png"),
        Line(10, "BETA TWO", "b.png"),
        Line(20, "GAMMA THREE", "c.png"),
    ]
    # On time, then 4 s late, then a wrong line for the whole third cue.
    events = [(0.4, 0), (14.0, 1), (20.2, 1)]
    checks, summary = compare(lines, events, [], 30)
    kinds = [c.kind for c in checks]
    assert kinds == ["late", "different line"], kinds
    assert summary["video_lines"] == 3
    assert summary["same_line"] is not None and summary["same_line"] < 0.8
    assert check_reason(checks[0]) == "late"
    assert check_reason(checks[1]) == "different lyric"
    spelled = Check(0, 1, "different line", "BECAUGE OF YOUR LOVE", "BECAUSE OF YOUR LOVE", 0, "")
    assert check_reason(spelled) == "close spelling"
    copied = Check(0, 1, "different line", "YOU ARE GOOD", "YOU ARE GOOD", 0, "")
    assert check_reason(copied) == "same letters, other copy"
    # A blank covers the late boundary, so that line is not scored late.
    checks2, _ = compare(lines, events, [(10, 16)], 30)
    assert [c.kind for c in checks2] == ["different line"], [c.kind for c in checks2]


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--self-test"]:
        self_test()
        print("ok")
        return 0
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset", type=Path, nargs="+")
    ap.add_argument("--model", default="small.en")
    args = ap.parse_args(argv)
    for folder in args.dataset:
        summary = run_dir(folder, args.model)
        same = summary["same_line"]
        same_s = "—" if same is None else f"{same:.0%}"
        letters = summary.get("same_letters")
        letters_s = "—" if letters is None else f"{letters:.0%}"
        reasons = summary.get("check_reasons") or {}
        reason_s = ", ".join(f"{n} {name}" for name, n in sorted(reasons.items(), key=lambda kv: -kv[1]))
        print(
            f"{folder.name}: same line {same_s}, same letters {letters_s} "
            f"of {summary['scored_seconds']} s, "
            f"median lag {summary['median_lag_s']} s, {summary['checks']} checks"
            + (f" ({reason_s})" if reason_s else "")
        )
        print(f"  {folder / 'bootstrap_review.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
