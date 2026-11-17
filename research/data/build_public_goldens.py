"""Turn JamendoLyrics' hand-timed lines into goldens the evals can replay.

Each song becomes one recording folder (audio.wav, recording.json, golden.json)
in a staging folder outside the app, so the app's own recordings are never
touched. Only public data goes in.

Slides are laid out the way a church library writes a song: lines in pairs
(a stanza's odd last line joins the pair before it), and each distinct slide
written once. A repeated slide in the performance is a cue back to the first
copy, the same jump the follower has to make on a repeated chorus. A
stanza with any repeated slide is a chorus, the rest are verses.

Ground truth: a cue at the hand-timed start of each slide's first line. No
blanks inside a song (an operator leaves the last line up through a solo).
Nothing is marked approved: the timings are the dataset's, not ours.

    uv run --project sidecar --no-sync python research/data/build_public_goldens.py
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path

from auto_presenter.eval.types import (
    GoldenAnnotation,
    GroundTruthEvent,
    Recording,
    SetlistItemSnapshot,
)
from auto_presenter.library.parser import tokenize

DATA = Path(__file__).resolve().parents[3] / "auto-presenter-research" / "public_worship"
OUT = Path(__file__).resolve().parents[3] / "auto-presenter-research" / "public_goldens"


def stanzas(txt: Path) -> list[list[str]]:
    blocks, cur = [], []
    for line in txt.read_text().splitlines():
        if line.strip():
            cur.append(line.strip())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    n = sum(len(b) for b in blocks)
    # No usable stanza breaks (one block, or a break after every line):
    # treat the song as one block and pair its lines.
    if len(blocks) == 1 or len(blocks) > 0.6 * n:
        blocks = [[x for b in blocks for x in b]]
    return blocks


def pairs(block: list[str]) -> list[list[str]]:
    out = [block[i : i + 2] for i in range(0, len(block), 2)]
    if len(out) > 1 and len(out[-1]) == 1:
        last = out.pop()
        out[-1] = out[-1] + last
    return out


def build(name: str, meta: dict, jam: Path, out: Path) -> dict:
    timed = list(csv.DictReader(open(jam / "lines" / f"{name}.csv")))
    blocks = stanzas(jam / "lyrics" / f"{name}.txt")
    # performance order: (stanza index, line indices) per sung slide
    seq, li = [], 0
    for bi, block in enumerate(blocks):
        for p in pairs(block):
            seq.append((bi, p, li))
            li += len(p)
    assert li == len(timed), (name, li, len(timed))
    key = lambda p: tuple(tokenize(" ".join(p)))
    counts: dict[tuple, int] = {}
    for _, p, _ in seq:
        counts[key(p)] = counts.get(key(p), 0) + 1
    chorus_blocks = {bi for bi, p, _ in seq if counts[key(p)] > 1}
    slides, sections, where = [[]], ["other:0"], {}
    nv = nc = 0
    label_of = {}
    for bi, p, _ in seq:
        if bi not in label_of:
            if bi in chorus_blocks:
                nc += 1
                label_of[bi] = f"chorus:{nc}"
            else:
                nv += 1
                label_of[bi] = f"verse:{nv}"
        k = key(p)
        if k not in where:
            where[k] = len(slides)
            slides.append(list(k))
            sections.append(label_of[bi])
    gt = [
        GroundTruthEvent(t_seconds=round(float(timed[i]["start_time"]), 2), item_idx=0, slide_idx=where[key(p)])
        for _, p, i in seq
    ]
    rid = "rec_pub-jl_" + name.replace(" ", "_")
    d = out / rid
    d.mkdir(parents=True, exist_ok=True)
    wav = d / "audio.wav"
    if not wav.exists():
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(jam / "mp3" / f"{name}.mp3"),
             "-ac", "1", "-ar", "16000", str(wav)],
            check=True,
        )
    import soundfile as sf

    info = sf.info(str(wav))
    note = (
        f"JamendoLyrics: {meta['Artist']} - {meta['Title']} ({meta['Genre']}, CC {meta['LicenseType']}, "
        f"{meta['URL']}). Cues from the dataset's hand-made line timings; slides built by "
        "research/data/build_public_goldens.py. Not reviewed."
    )
    rec = Recording(
        id=rid,
        setlist_id="public_jamendolyrics",
        started_at_ms=0,
        duration_seconds=info.frames / info.samplerate,
        ground_truth=gt,
        audio_path=str(wav),
        notes=note,
        setlist_content=[
            SetlistItemSnapshot(
                item_idx=0, song_id="jl_" + name, slides=slides, slide_sections=sections, title=meta["Title"]
            )
        ],
    )
    (d / "recording.json").write_text(rec.model_dump_json(indent=1))
    golden = GoldenAnnotation(recording_id=rid, ground_truth=gt, notes=note, edited_at_ms=0, approved=False)
    (d / "golden.json").write_text(golden.model_dump_json(indent=1))
    jumps = sum(b.slide_idx <= a.slide_idx for a, b in zip(gt, gt[1:]))
    return {"rid": rid, "slides": len(slides) - 1, "cues": len(gt), "jumps_back": jumps,
            "polyphonic": meta["Polyphonic"], "nonlexical": meta["NonLexical"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    jam = a.data / "jamalt"
    meta = {r["Filepath"][:-4]: r for r in csv.DictReader(open(jam / "JamendoLyrics.csv"))}
    for csv_path in sorted((jam / "lines").glob("*.csv")):
        name = csv_path.stem
        print(json.dumps(build(name, meta[name], jam, a.out / "jamendolyrics")))


if __name__ == "__main__":
    main()
