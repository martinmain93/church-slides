"""Check the sung timing behind the perceptual scorer, on every golden.

    uv run --project sidecar python research/eval/sung_timing_check.py [-v]

For each golden: how many cues had their first word / last line measured
(rather than estimated), and where the operator's click sits against the
band's first word. `-v` lists every cue.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sidecar"))

from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.eval.sung_timing import cue_timings, slide_lines  # noqa: E402

APP = Path.home() / "Library/Application Support/com.churchslides.app"
REC = APP / "recordings"
#: Recordings whose transcript lives in the repo rather than beside the audio.
EXTRA_TRANSCRIPTS = {"rec_20261006-225539_315": REPO / "research/spikes/music_only/transcripts/bml.json"}


def song_bodies() -> dict[str, str]:
    db = sqlite3.connect(f"file:{APP / 'library.db'}?mode=ro", uri=True)
    return {r[0]: r[1] for r in db.execute("select id, body_text from songs")}


def transcript(rdir: Path):
    f = rdir / "transcript.json"
    if not f.exists():
        f = EXTRA_TRANSCRIPTS.get(rdir.name)
    if f is None or not Path(f).exists():
        return None
    return [(w["start"], w["end"], w["word"]) for w in json.loads(Path(f).read_text())["words"]]


_bodies = None


def timings(rdir: Path, rec=None):
    """The recording's `CueTiming`s (its golden, its transcript, library lines)."""
    global _bodies
    if _bodies is None:
        _bodies = song_bodies()
    rec = rec or load_recording(rdir)
    lines = slide_lines(rec.setlist_content, _bodies)
    return cue_timings(rec.ground_truth, rec.setlist_content, transcript(rdir), lines)


def goldens(approved_only: bool = False) -> list[Path]:
    out = []
    for g in sorted(REC.glob("*/golden.json")):
        if approved_only and not json.loads(g.read_text()).get("approved"):
            continue
        out.append(g.parent)
    return out


def main() -> int:
    verbose = "-v" in sys.argv
    allf, alll = [], []
    for rdir in goldens():
        approved = json.loads((rdir / "golden.json").read_text()).get("approved")
        rec = load_recording(rdir)
        ts = timings(rdir, rec)
        fm = [t for t in ts if t.first_word_measured]
        lm = [t for t in ts[1:] if t.last_line_measured]
        off = [t.first_word - t.cue for t in fm]
        win = [t.first_word - t.last_line for t in ts[1:]]
        if approved:
            allf += off
            alll += win
        print(f"{rdir.name} {'approved' if approved else 'estimate'}  cues {len(ts):3d}  first word measured {len(fm):3d}"
              f"  last line {len(lm):3d}  click->word median {statistics.median(off) if off else float('nan'):+.2f} s"
              f"  last-line window median {statistics.median(win) if win else 0:.1f} s")
        if verbose:
            for t in ts:
                print(f"    cue {t.cue:7.2f}  first word {t.first_word:7.2f}{'' if t.first_word_measured else '*'}"
                      f"  last line {t.last_line:7.2f}{'' if t.last_line_measured else '*'}")
    q = statistics.quantiles(allf, n=10)
    print(f"\napproved: click->first word median {statistics.median(allf):+.2f} s, p10 {q[0]:+.2f}, p90 {q[-1]:+.2f}"
          f" (n {len(allf)}); last-line window median {statistics.median(alll):.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
