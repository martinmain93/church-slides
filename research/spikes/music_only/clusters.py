"""Slide clusters: runs of slides that are one section of the song.

Inside a cluster the next slide is not a choice — line 2 of a chorus follows
line 1 — so anticipating it is always safe. At a cluster's *end* the band
chooses: the next section, the same chorus again, a vamp, a tag. That is
where anticipation guesses.

From a lead sheet, a slide ends its cluster when, between its last sung word
and the next sung word in the roadmap, the music:
* reaches a section label (VERSE 2, CHORUS, BRIDGE, ...),
* takes a jump (a repeat sign, a numbered ending, D.S., To Coda — any
  played bar that is not the written bar after the one before),
* or ends.

A recorded reference will supply the same thing from operators: slides the
band has always taken in the same order across services are a cluster; a
slide whose successor has varied ends one.

    uv run --project sidecar python research/spikes/music_only/clusters.py

checks the sheet's clusters against the goldens: every place the operator
left the written order should be a cluster end.
"""
from __future__ import annotations

import numpy as np

import score as sc


def span_ends_cluster(s: sc.Score, sp: sc.Span) -> bool:
    if sp.w1 + 1 >= len(s.words):
        return True
    last, nxt = s.words[sp.w1], s.words[sp.w1 + 1]
    # a line that starts on a pickup ("Let the | King") puts the section
    # label and the repeat sign on the bar *after* its first word
    stop = nxt.played + (1 if nxt.beat >= s.meter / 2 else 0)
    for p in range(last.played + 1, min(len(s.played) - 1, stop) + 1):
        if s.played[p].bar != s.played[p - 1].bar + 1:
            return True  # a jump: repeat, ending, D.S., coda
        if s.bar(s.played[p].bar).get("section"):
            return True
    return False


def sheet_cluster_ends(s: sc.Score, spans: dict[int, list[sc.Span]]) -> dict[int, bool]:
    """slide -> True if any pass of it in the roadmap ends a cluster: the
    band could leave from there (a vamp block's first pass runs straight on,
    its last jumps back)."""
    return {si: any(span_ends_cluster(s, sp) for sp in sps) for si, sps in spans.items() if sps}


def main() -> int:
    import json

    import cv
    from auto_presenter.eval.metrics import identical_slide_groups
    from auto_presenter.eval.replay import load_recording
    import run_joint as rj

    tot = {"dev": 0, "dev_at_end": 0, "kept": 0, "kept_at_end": 0, "slides": 0, "ends": 0}
    for key in ("wabn", "rl", "komh", "isa"):
        song = cv.Song(key)
        rec = load_recording(cv.S / key)
        item = cv.stage1.song_item(song.s, cv.S / key)
        ig = identical_slide_groups(rec.setlist_content)
        grp = lambda sl: min({sl} | set(ig.get((item, sl), ())))
        ends = sheet_cluster_ends(song.s, song.spans)
        # a group ends a cluster if any of its copies does
        gend: dict[int, bool] = {}
        for sl, e in ends.items():
            gend[grp(sl)] = gend.get(grp(sl), False) or e
        road = rj.roadmap_sequence(song)
        # the written order: the sheet's roadmap, and library order (a hymn
        # sheet prints one verse; the band sings the library's verses in order)
        lib = [i for i in range(1, len(song.slides)) if song.slides[i]]
        written = {(grp(a), grp(b)) for seq_ in (road, lib) for a, b in zip(seq_, seq_[1:])}
        seq = [c.slide_idx for c in sorted(rec.ground_truth, key=lambda c: c.t_seconds) if c.item_idx == item and c.slide_idx > 0]
        lines = []
        for a, b in zip(seq, seq[1:]):
            ga, gb = grp(a), grp(b)
            if ga == gb:
                continue
            dev = (ga, gb) not in written
            at_end = gend.get(ga, True)
            tot["dev" if dev else "kept"] += 1
            tot["dev_at_end" if dev else "kept_at_end"] += at_end
            if dev:
                lines.append(f"{a}->{b}{'' if at_end else ' (MID-CLUSTER)'}")
        clus, cur = [], []
        for sl in road:
            cur.append(sl)
            if gend.get(grp(sl), True):
                clus.append(cur)
                cur = []
        if cur:
            clus.append(cur)
        tot["slides"] += len(gend)
        tot["ends"] += sum(gend.values())
        print(f"{key:5s} sheet clusters: " + " | ".join(" ".join(map(str, c)) for c in clus))
        print(f"      operator left the written order: {', '.join(lines) or 'never'}")
    print(f"\ndeviations at a cluster end: {tot['dev_at_end']}/{tot['dev']}; written-order moves at a cluster end:"
          f" {tot['kept_at_end']}/{tot['kept']}; slide groups that end a cluster: {tot['ends']}/{tot['slides']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
