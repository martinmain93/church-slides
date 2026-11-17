"""Bass spike, step 3 (sheet side): does the bass line tell apart slides the chords don't?

    uv run --project sidecar python research/spikes/music_only/bass_sheet_discrim.py

For each song with a recording, every slide span on the lead sheet (where the
slide's words are sung on the unrolled sheet) gets two sequences:
  chords: the chord roots + major/minor over the span, bass ignored
  bass:   the full chord with its bass note (slash or root)
Pairs of DIFFERENT slides whose chord sequences are identical are the ones
chords cannot separate; we count how many of those the bass separates.
Also: the same slide sung at two places on the unrolled sheet (repeat signs,
voltas) - does its bass line change between passes (e.g. a last-time ending)?
"""
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bass_lib as bl  # noqa: E402
import score as sc  # noqa: E402
import stage1  # noqa: E402


def seqs(chords, a, b):
    cs = [c for c in chords if c[1] > a and c[0] < b]
    tri = tuple(f"{c[2].root}{'m' if c[2].minor else ''}" for c in cs)
    full = tuple(f"{c[2].root}{'m' if c[2].minor else ''}/{c[2].bass}" for c in cs)
    names = tuple(c[2].name for c in cs)
    return tri, full, names


def main():
    L = []
    tot = dict(pairs=0, bass_sep=0, rep=0, rep_bass_diff=0, rep_chord_diff=0)
    for case, (rdir, sheets) in bl.CASES.items():
        rec = bl.S / rdir
        items = stage1.load_items(rec)
        for sheet, item in sheets.items():
            s = bl.score_for(sheet)
            it = stage1.song_item(s, rec) if item is None else item
            slides = items[it]
            spans = sc.bind_slides(s, slides)
            chords = bl.played_chords(s)
            per = {}
            for si, sps in spans.items():
                for sp in sps:
                    per.setdefault(si, []).append(seqs(chords, sp.start, sc.span_end(s, sp)))
            # different slides, same chords
            firsts = {si: v[0] for si, v in per.items() if v and v[0][0]}
            n_pairs = n_sep = 0
            ex = []
            for a, b in combinations(sorted(firsts), 2):
                if " ".join(slides[a]).lower() == " ".join(slides[b]).lower():
                    continue  # identical slides: nothing to separate
                if firsts[a][0] == firsts[b][0]:
                    n_pairs += 1
                    if firsts[a][1] != firsts[b][1]:
                        n_sep += 1
                        ex.append(f"slide {a} {' '.join(firsts[a][2])}  vs  slide {b} {' '.join(firsts[b][2])}")
            # same slide, different passes
            n_rep = n_rep_b = n_rep_c = 0
            rex = []
            for si, v in per.items():
                for x, y in combinations(v, 2):
                    n_rep += 1
                    if x[0] != y[0]:
                        n_rep_c += 1
                    elif x[1] != y[1]:
                        n_rep_b += 1
                        rex.append(f"slide {si}: {' '.join(x[2])}  vs  {' '.join(y[2])}")
            tot["pairs"] += n_pairs; tot["bass_sep"] += n_sep
            tot["rep"] += n_rep; tot["rep_bass_diff"] += n_rep_b; tot["rep_chord_diff"] += n_rep_c
            L.append(f"### {sheet}  ({case})")
            L.append(f"- slide pairs with identical chords: {n_pairs}; bass tells apart: {n_sep}")
            L += [f"  - {e}" for e in ex[:6]]
            L.append(f"- same slide sung twice on the sheet: {n_rep} pairs; chords differ {n_rep_c}; chords same but bass differs {n_rep_b}")
            L += [f"  - {e}" for e in rex[:6]]
    L.insert(0, f"TOTAL: {tot['pairs']} pairs of different slides share the chord sequence; the bass separates {tot['bass_sep']}. "
                f"Repeated passes of one slide: {tot['rep']} pairs, chords differ in {tot['rep_chord_diff']}, only the bass differs in {tot['rep_bass_diff']}.\n")
    txt = "\n".join(L)
    print(txt)
    (bl.OUT / "sheet_discrim.md").write_text(txt + "\n")


if __name__ == "__main__":
    main()
