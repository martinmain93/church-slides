"""Bass spike, step 1: how often do the lead sheets carry slash chords, and where?

    uv run --project sidecar python research/spikes/music_only/bass_sheets.py

For every chord on the 11 lead sheets (the 8 recordings' songs): is it a slash
chord, which section it is in, and how far from the section's end (in bars).
"Section" = the run of bars from one section label to the next.
"""
import glob, json, os, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr

OUT = Path(__file__).resolve().parents[3] / "build/spike_music_only/bass"


def sections(bars):
    """[(label, [bar indices])] in sheet order."""
    out, cur = [], None
    for i, b in enumerate(bars):
        if b.get("section") or cur is None:
            cur = (b.get("section") or "(start)", [])
            out.append(cur)
        cur[1].append(i)
    return out


def main():
    rows, summary = [], []
    tot = Counter()
    for f in sorted(glob.glob(os.path.expanduser("~/Downloads/*-lead-*.pdf"))):
        name = os.path.basename(f)[:-4]
        bars = mr.sheet(name)["bars"]
        n_ch = n_sl = 0
        for label, idx in sections(bars):
            last = idx[-1]
            for i in idx:
                for c in bars[i]["chords"]:
                    n_ch += 1
                    sl = "/" in c["name"]
                    n_sl += sl
                    from_end = last - i  # 0 = last bar of the section
                    tot["chords"] += 1
                    tot["slash"] += sl
                    tot[f"lastbar_{int(sl)}"] += from_end == 0
                    tot[f"last2_{int(sl)}"] += from_end <= 1
                    if sl:
                        rows.append(dict(song=name, section=label, bar=i, bars_from_end=from_end,
                                         section_bars=len(idx), chord=c["name"],
                                         end_repeat=bool(bars[i].get("end_repeat")),
                                         voltas=bars[i].get("voltas")))
        summary.append((name, n_ch, n_sl))
    lines = ["song | chords | slash | share", "---|---|---|---"]
    for n, c, s in summary:
        lines.append(f"{n} | {c} | {s} | {s / max(c, 1):.0%}")
    lines.append("")
    lines.append(f"all: {tot['slash']}/{tot['chords']} slash ({tot['slash']/tot['chords']:.0%})")
    ns = tot["chords"] - tot["slash"]
    lines.append(f"in a section's last bar: slash {tot['lastbar_1']}/{tot['slash']} "
                 f"({tot['lastbar_1']/max(tot['slash'],1):.0%}) vs non-slash {tot['lastbar_0']}/{ns} ({tot['lastbar_0']/ns:.0%})")
    lines.append(f"in a section's last 2 bars: slash {tot['last2_1']}/{tot['slash']} "
                 f"({tot['last2_1']/max(tot['slash'],1):.0%}) vs non-slash {tot['last2_0']}/{ns} ({tot['last2_0']/ns:.0%})")
    lines.append("")
    lines.append("song | section | bar | bars from section end / section bars | chord | repeat/volta")
    lines.append("---|---|---|---|---|---")
    for r in rows:
        lines.append(f"{r['song']} | {r['section']} | {r['bar']} | {r['bars_from_end']}/{r['section_bars']} | {r['chord']} | "
                     f"{'end-repeat ' if r['end_repeat'] else ''}{r['voltas'] or ''}")
    txt = "\n".join(lines)
    print(txt)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sheets_slash.md").write_text(txt + "\n")
    (OUT / "sheets_slash.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
