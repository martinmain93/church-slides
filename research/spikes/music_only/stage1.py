"""Stage 1: bind a recording's slides to its lead sheet, and group the slides
that sound identical.

    uv run --no-project python research/spikes/music_only/stage1.py \
        score.json ~/Library/.../recordings/rec_20260818-214759_057 [-o classes.json]

Prints each slide with where it is sung in the unrolled sheet, then the
musical classes: slides whose notes (pitch and rhythm, from the first word to
the word after the last) are identical. A matcher that hears only music can
at best name the class.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import score as sc


def load_items(rec_dir: Path) -> list[list[list[str]]]:
    rec = json.loads((rec_dir / "recording.json").read_text())
    return [[list(s) for s in item["slides"]] for item in rec["setlist_content"]]


def song_item(s: sc.Score, rec_dir: Path) -> int:
    """The setlist item this lead sheet is: the one whose slides it sings."""
    items = load_items(rec_dir)
    if len(items) == 1:
        return 0
    def bound(slides):
        sp = sc.bind_slides(s, slides)
        return sum(1 for v in sp.values() if v)
    return max(range(len(items)), key=lambda i: bound(items[i]))


def load_slides(rec_dir: Path, s: "sc.Score | None" = None) -> list[list[str]]:
    items = load_items(rec_dir)
    return items[song_item(s, rec_dir) if s is not None else 0]


def load_sections(rec_dir: Path, item: int) -> list[str]:
    rec = json.loads((rec_dir / "recording.json").read_text())
    return list(rec["setlist_content"][item].get("slide_sections") or [])


def song_spans(s: sc.Score, rec_dir: Path, slides: list[list[str]]) -> dict[int, list[sc.Span]]:
    """Slide spans, pruned, with unprinted verses filled in.

    A hymn's lead sheet prints one verse; the others are sung to the same
    tune. A slide the sheet does not carry takes the spans of a bound slide
    with the same section type in the library ("verse:3" -> the verse that is
    printed), choosing the one closest in word count."""
    import gt

    spans = gt.prune_spans(s, sc.bind_slides(s, slides))
    sections = load_sections(rec_dir, song_item(s, rec_dir))
    kind = [sec.split(":")[0] if sec else "" for sec in sections]
    for si, sw in enumerate(slides):
        if spans.get(si) or not sw or si >= len(kind):
            continue
        same = [sj for sj in spans if spans[sj] and sj < len(kind) and kind[sj] == kind[si]]
        if same:
            best = min(same, key=lambda sj: abs(len(slides[sj]) - len(sw)))
            spans[si] = list(spans[best])
    return spans


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("score", type=Path)
    ap.add_argument("recording", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    args = ap.parse_args()
    s = sc.load(args.score)
    slides = load_slides(args.recording.expanduser(), s)
    spans = song_spans(s, args.recording.expanduser(), slides)

    def where(sp: sc.Span) -> str:
        a, b = s.words[sp.w0], s.words[sp.w1]
        return f"b{a.bar}.{a.beat:g}-b{b.bar}.{b.beat:g}"

    print(f"{len(s.played)} bars unrolled, {len(s.words)} words, {len(slides)} slides\n")
    sig_of: dict[int, set[tuple]] = {}
    for si, sw in enumerate(slides):
        sps = spans.get(si, [])
        sig_of[si] = {sc.span_signature(s, sp) for sp in sps}
        text = " ".join(sw)[:48]
        print(f"slide {si:2d} {text:50s} {len(sps)}x  " + " ".join(where(sp) for sp in sps))

    # which slides could be on screen at each canonical position
    cov = {si: set().union(*(sc.coverage(s, sp) for sp in sps)) if sps else set() for si, sps in spans.items()}
    canon = sc.canonical_bars(s)
    dup = {b: c for b, c in canon.items() if b != c}
    print(f"\n{len(set(canon.values()))} distinct bars of {len(canon)}; repeats of earlier bars: {dup}")
    print("\nslides sharing music (share of the slide's positions also covered by the other):")
    for si in sorted(cov):
        if not cov[si]:
            continue
        shared = []
        for sj in sorted(cov):
            if sj != si and cov[sj]:
                f = len(cov[si] & cov[sj]) / len(cov[si])
                if f >= 0.5:
                    shared.append(f"{sj}({f:.0%})")
        print(f"  {si:2d} {' '.join(slides[si][:5]):34s} " + (" ".join(shared) or "-- unique"))
    unbound = [i for i, sw in enumerate(slides) if sw and not spans.get(i)]
    print(f"\nunbound slides: {unbound}")
    if args.out:
        args.out.write_text(json.dumps({
            "spans": {si: [{"start": sp.start, "end": sp.end, "w0": sp.w0, "w1": sp.w1} for sp in sps] for si, sps in spans.items()},
        }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
