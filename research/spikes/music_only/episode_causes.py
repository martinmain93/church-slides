"""Why the long within-song mis-slides happen, classified from the evidence.

    uv run --project sidecar python research/spikes/music_only/episode_causes.py

For every within-song episode >= 3 s (from error_anatomy), using the live
trace's scorer steps that arrived during it:

  ignored clear evidence  a scorer step named the band's slide (and only slides
                          carrying the same words) clearly (best >= -2.0), and we
                          still didn't show it within 3 s of that arrival
  shared words            the clear best span is on both the band's slide and ours
  band waiting            nothing clear heard and the band's slide ran > 1.6x its
                          song's median slide length (an instrumental stretch / hold)
  nothing heard           nothing clear heard otherwise
  evidence misled         clear evidence for some other slide
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag_live_trace as d  # noqa: E402
import error_anatomy as ea  # noqa: E402
import multi_replay as mr  # noqa: E402
import music_replay as mrp  # noqa: E402
from auto_presenter.eval.metrics import identical_slide_groups  # noqa: E402
from auto_presenter.follower.scorer import spans_for  # noqa: E402

CLEAR = -2.0


def main() -> int:
    tally = collections.Counter()
    examples = collections.defaultdict(list)
    for name in sys.argv[1:] or list(mr.CASES):
        seq, gt = ea.anatomy(name)
        dec, rec = mrp.decisions(name, mrp.env_arm())
        rows = d.load_trace(mr.CASES[name][1])
        equiv = identical_slide_groups(rec.setlist_content)
        # median slide length per song (golden)
        lens = collections.defaultdict(list)
        for i, g in enumerate(gt[:-1]):
            lens[g.item_idx].append(gt[i + 1].t_seconds - g.t_seconds)
        med = {k: float(np.median(v)) for k, v in lens.items()}
        owners = {}
        for it in {g.item_idx for g in gt}:
            snap = [x for x in rec.setlist_content if x.item_idx == it]
            if snap:
                o = spans_for(snap[0].slides)
                owners[it] = (o, sorted(set(o.values())))
        for a, dur, cls in ea.episodes(seq):
            if dur < 3 or cls in ("wrong song", None):
                continue
            g = max((x for x in gt if x.t_seconds <= a + 1e-6), key=lambda x: x.t_seconds)
            nxt = [x.t_seconds for x in gt if x.t_seconds > g.t_seconds]
            g_len = (nxt[0] if nxt else a + dur) - g.t_seconds
            it, band = g.item_idx, g.slide_idx
            same = {band} | set(equiv.get((it, band), ()))
            shown = None
            for x in dec:
                if x.t_seconds <= a + 1e-6:
                    shown = (x.item_idx, x.slide_idx)
            ours = {shown[1]} | set(equiv.get(shown, ())) if shown and shown[0] == it else set()
            clear_for = []
            if it in owners:
                owner, spans = owners[it]
                for r in rows:
                    if r["e"] != "scored" or r.get("item") != it or not (a - 2 <= r["arr"] <= a + dur):
                        continue
                    sc = np.asarray(r["scores"])
                    b = int(np.argmax(sc))
                    slides = {k[0] for k, v in owner.items() if v == spans[b] and k[1] > 0}
                    if sc[b] >= CLEAR and slides:
                        clear_for.append((r["arr"], slides))
            if not clear_for:
                cause = "band waiting" if g_len > 1.6 * med.get(it, g_len) else "nothing heard"
            else:
                unique = [t for t, sl in clear_for if sl <= same]
                shared = [t for t, sl in clear_for if band in sl and not sl <= same]
                if unique:
                    cause = "evidence arrived late" if unique[0] > a + dur - 3 else "ignored clear evidence"
                elif shared:
                    cause = "line on several slides"
                else:
                    cause = "evidence misled"
            tally[cause] += dur
            examples[cause].append(f"{name}@{a:.0f}({dur:.0f}s,{cls})")
    total = sum(tally.values())
    print(f"within-song episodes >= 3 s: {total:.0f} s")
    for k, v in tally.most_common():
        print(f"  {k:24s} {v:5.0f} s {v / total:5.1%}   e.g. {' '.join(examples[k][:6])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
