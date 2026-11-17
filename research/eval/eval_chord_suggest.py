"""How often is the editor's next-chord suggestion right?

Replays charting on real songs from the library: the top of each chart is
shown as already charted, the rest has its chords hidden, and the model is
asked what comes next — exactly the request the song editor sends after a
chord is placed. Its reply goes through the same gate (`chords.suggest`),
then is scored against the hidden chords.

    uv run --project sidecar python research/eval/eval_chord_suggest.py --songs 30

Reads library.db read-only. Uses the Groq key saved in Settings → AI. Like
the editor, each call sees a 24-line window around the cut. Groq's free
tier allows 8,000 tokens a minute on this model (a call is ~1,500 in plus
up to 1,024 out), so calls are spaced ~25 s apart and a 429 is retried once
after a minute — 30 songs take about 13 minutes.

Metrics, per song:
  answered      the gate let at least one placement through
  next_exact    the first placement is the hidden next chord, on its word
  next_root     ...or at least the right root on the right word
  precision     share of every returned placement that matches the hidden chart
A baseline that copies the chords of an identical earlier line (the local
rules' main trick) is scored on the same cuts for comparison.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sidecar"))

from auto_presenter.chords import words  # noqa: E402
from auto_presenter.chords.suggest import RATE_LIMITED, suggest  # noqa: E402
from auto_presenter.config import database_path  # noqa: E402
from auto_presenter.protocol import ChordSuggestLine  # noqa: E402

GROQ_GAP_SECONDS = 25.0
WINDOW = 24


@dataclass
class Line:
    line_id: str
    section: str
    text: str
    #: (word_index, chord) in order.
    chords: list[tuple[int, str]]


def word_starts(text: str) -> list[int]:
    starts = []
    for match in re.finditer(r"\S+", text):
        if words.norm(match.group(0)):
            starts.append(match.start())
    return starts


def chart_lines(chart: dict) -> list[Line]:
    out: list[Line] = []
    for si, section in enumerate(chart.get("sections", [])):
        li = 0
        for stanza in section.get("stanzas", []):
            for row in stanza:
                text = (row.get("text") or "").strip()
                if not text or not words.line_words(text):
                    continue
                starts = word_starts(row.get("text") or "")
                pairs = []
                for chord in row.get("chords", []):
                    before = [i for i, start in enumerate(starts) if start <= chord["pos"]]
                    pairs.append((before[-1] if before else 0, chord["chord"]))
                out.append(Line(f"s{si}-l{li}", section.get("label", "").lower(), text, pairs))
                li += 1
    return out


def norm_chord(chord: str) -> str:
    return chord.strip().replace("♯", "#").replace("♭", "b")


def root(chord: str) -> str:
    match = re.match(r"[A-G][#b]?", norm_chord(chord))
    return match.group(0) if match else ""


def payload(lines: list[Line], cut: int) -> list[ChordSuggestLine]:
    out = []
    for n, line in enumerate(lines):
        shown = line.chords if n < cut else []
        tokens = words.line_words(line.text)
        chorded = [index for index, _ in shown]
        out.append(
            ChordSuggestLine(
                line_id=line.line_id,
                section=line.section,
                line=line.text,
                chords=[chord for _, chord in shown],
                chorded_words=[tokens[index] for index in chorded if index < len(tokens)],
                chorded_word_indexes=chorded,
                role="charted" if set(chorded) >= set(range(len(tokens))) else "target",
            )
        )
    return out


def baseline(lines: list[Line], cut: int) -> tuple[str, int, str] | None:
    """Copy the first chord of an identical, already-charted line."""
    target = next((line for line in lines[cut:] if line.chords), None)
    if target is None:
        return None
    for earlier in reversed(lines[:cut]):
        if earlier.chords and words.norm(earlier.text) == words.norm(target.text):
            index, chord = earlier.chords[0]
            return target.line_id, index, chord
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--songs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--db", type=Path, default=database_path())
    parser.add_argument("--out", type=Path, default=None, help="Write per-song rows as JSON")
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT s.title, c.original_key, c.chart_json FROM song_charts c JOIN songs s ON s.id = c.song_id"
    ).fetchall()
    rng = random.Random(args.seed)
    candidates = []
    for title, key, chart_json in rows:
        lines = chart_lines(json.loads(chart_json))
        charted = sum(1 for line in lines if line.chords)
        if len(lines) >= 8 and charted >= 0.6 * len(lines):
            candidates.append((title, key or json.loads(chart_json).get("key") or "", lines))
    rng.shuffle(candidates)
    candidates = candidates[: args.songs]
    print(f"{len(candidates)} charted songs (of {len(rows)} charts)", file=sys.stderr)

    results = []
    for n, (title, key, lines) in enumerate(candidates):
        cut = rng.randint(2, len(lines) - 2)
        truth = {(line.line_id, i): chord for line in lines[cut:] for i, chord in line.chords}
        nxt = next(((line.line_id, *line.chords[0]) for line in lines[cut:] if line.chords), None)
        start = max(0, min(cut - WINDOW // 3, len(lines) - WINDOW))
        window = payload(lines, cut)[start : start + WINDOW]
        placements, phrase, error = suggest(key, window)
        if error == RATE_LIMITED:
            time.sleep(60)
            placements, phrase, error = suggest(key, window)
        if error:
            print(f"  {title}: {error}", file=sys.stderr)
        got = [(p.line_id, p.word_index, p.chord) for p in placements]
        hits = [norm_chord(truth.get((lid, wi), "")) == norm_chord(chord) for lid, wi, chord in got]
        first = got[0] if got else None
        base = baseline(lines, cut)
        row = {
            "title": title,
            "cut": cut,
            "error": error,
            "phrase": phrase,
            "answered": bool(got),
            "next": nxt,
            "first": first,
            "next_exact": bool(first and nxt and first[:2] == nxt[:2] and norm_chord(first[2]) == norm_chord(nxt[2])),
            "next_root": bool(first and nxt and first[:2] == nxt[:2] and root(first[2]) == root(nxt[2])),
            "placed": len(got),
            "correct": sum(hits),
            "baseline_answered": base is not None,
            "baseline_exact": bool(base and nxt and base[:2] == nxt[:2] and norm_chord(base[2]) == norm_chord(nxt[2])),
        }
        results.append(row)
        print(
            f"{n + 1:>3} {title[:32]:<32} cut={cut:<3} placed={row['placed']:<2} "
            f"correct={row['correct']:<2} next={'Y' if row['next_exact'] else '-'}",
            file=sys.stderr,
        )
        time.sleep(GROQ_GAP_SECONDS)

    ok = [r for r in results if not r["error"]]
    answered = [r for r in ok if r["answered"]]
    placed = sum(r["placed"] for r in ok)
    summary = {
        "songs": len(results),
        "errors": len(results) - len(ok),
        "answered": f"{len(answered)}/{len(ok)}",
        "next_exact_when_answered": f"{sum(r['next_exact'] for r in answered)}/{len(answered)}",
        "next_root_when_answered": f"{sum(r['next_root'] for r in answered)}/{len(answered)}",
        "placement_precision": f"{sum(r['correct'] for r in ok)}/{placed}",
        "phrases": sum(r["phrase"] for r in ok),
        "baseline_answered": f"{sum(r['baseline_answered'] for r in ok)}/{len(ok)}",
        "baseline_exact": f"{sum(r['baseline_exact'] for r in ok)}/{len(ok)}",
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "rows": results}, indent=2))


if __name__ == "__main__":
    main()
