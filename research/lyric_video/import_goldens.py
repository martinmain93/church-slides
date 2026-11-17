"""Copy Life.Church lyric-video sets into the app as unapproved goldens.

Ground truth is the burned-in clock in ``lines.json``: a cue when the line
changes, and a blank only when the screen has no lyric for 5 seconds or
more. Nothing here is marked approved.

    uv run --project sidecar python research/lyric_video/import_goldens.py
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import wave
from difflib import SequenceMatcher
from pathlib import Path

from auto_presenter.eval.types import (
    GoldenAnnotation,
    GroundTruthEvent,
    Recording,
    SetlistItemSnapshot,
)
from auto_presenter.library.sequence import resolve_slide_sequence, section_key_of

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research" / "lyric_video" / "out"
OCR_LINES = Path("/tmp/lyric_slides.json")
APP_SUPPORT = Path.home() / "Library/Application Support/com.churchslides.app"
LIBRARY_DB = APP_SUPPORT / "library.db"
RECORDINGS = APP_SUPPORT / "recordings"

# A breath between lines is not its own slide. Only a real empty screen is.
BLANK_MIN_S = 5.0
MATCH_MIN = 0.8

# Video time windows, checked against the burned-in lines. How Great Thou Art
# is in the Easter set so the whole recording stays one file; its library
# slides group several lines, and the video reveals a few words at a time.
SETS = [
    {
        "dir": "ChurchOnlineWorshipSet_1",
        "rec_id": "rec_20261027-173001_001",
        "label": "Church Online Worship Set 1",
        "songs": [
            ("God So Good", 13.0, 409.0),
            ("The Artist", 409.0, 744.0),
            ("Heaven's All Around", 748.0, 1e9),
        ],
    },
    {
        "dir": "ChurchOnlineWorshipSet_3",
        "rec_id": "rec_20261027-173002_001",
        "label": "Church Online Worship Set 3",
        "songs": [
            ("All Glory", 15.0, 216.0),
            ("When I Say Jesus", 235.0, 524.0),
            ("Grand Design", 545.0, 1e9),
        ],
    },
    {
        "dir": "ChurchOnlineWorshipSet_4",
        "rec_id": "rec_20261027-173003_001",
        "label": "Church Online Worship Set 4",
        "songs": [
            ("God So Good", 15.0, 366.0),
            ("Never Will", 379.0, 638.0),
            ("Death of Death", 655.0, 1e9),
        ],
    },
    {
        "dir": "ChurchOnlineWorshipSet_5-",
        "rec_id": "rec_20261027-173004_001",
        "label": "Church Online Worship Set 5",
        "songs": [
            ("Life To Me", 17.0, 199.0),
            ("Never Will", 207.0, 469.0),
            ("Grand Design", 488.0, 1e9),
        ],
    },
    {
        "dir": "Easter_2020_WorshipSet",
        "rec_id": "rec_20261027-173005_001",
        "label": "Easter 2020 Worship Set",
        "songs": [
            ("Grand Design", 5.0, 364.0),
            ("How Great Thou Art", 368.0, 621.0),
            ("Heaven's All Around", 626.0, 1e9),
        ],
    },
]


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _lyric_text(lines: list[str]) -> str:
    kept: list[str] = []
    for line in lines:
        low = line.lower()
        if any(s in low for s in ("ccli", "license", "life.church", "copyright", "©")):
            continue
        if "|" in line:
            continue
        if len(_letters(line)) < 3:
            continue
        kept.append(line)
    return " ".join(kept)


def _score(video: str, slide: str) -> float:
    v, k = _letters(video), _letters(slide)
    if len(v) < 8 or len(k) < 8:
        return 0.0
    if "cclilicense" in v or "lifechurch" in v:
        return 0.0
    if v == k:
        return 1.0
    if v in k or k in v:
        return min(len(v), len(k)) / max(len(v), len(k))
    return SequenceMatcher(None, v, k).ratio()


def _best_slide(texts: list[str], slides, cursor: int) -> int | None:
    scored: list[tuple[int, float]] = []
    for i, slide in enumerate(slides):
        if not slide.words:
            continue
        sc = max((_score(text, slide.lyrics) for text in texts if text), default=0.0)
        if sc >= MATCH_MIN:
            scored.append((i, sc))
    if not scored:
        return None
    best = max(sc for _, sc in scored)
    cands = [i for i, sc in scored if sc >= best - 0.02]
    ahead = [i for i in cands if i > cursor]
    return min(ahead) if ahead else min(cands)


def _load_song(con: sqlite3.Connection, title: str):
    rows = con.execute(
        "SELECT id, title, body_text, slide_order FROM songs WHERE title = ?",
        (title,),
    ).fetchall()
    if title == "How Great Thou Art":
        rows = [r for r in rows if r[0] == "song_pp_dd4ab81d049c1af2"]
    if len(rows) != 1:
        raise SystemExit(f"{title!r} matched {len(rows)} library songs")
    song_id, song_title, body, slide_order = rows[0]
    slides = resolve_slide_sequence(
        body, song_id, slide_order or None, leading_blank=True
    )
    return song_id, song_title, slides


def _ocr_by_set() -> dict[str, list[dict]]:
    if not OCR_LINES.exists():
        return {}
    grouped: dict[str, list[dict]] = {}
    for row in json.loads(OCR_LINES.read_text()):
        grouped.setdefault(row["set"], []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda r: float(r["t"]))
    return grouped


def _texts_for(cue: dict, ocr_rows: list[dict]) -> list[str]:
    texts = [cue.get("text") or ""]
    anchor = float(cue.get("mask_t") or cue["t"])
    nearest = min(ocr_rows, key=lambda r: abs(float(r["t"]) - anchor), default=None)
    if nearest is not None and abs(float(nearest["t"]) - anchor) <= 0.6:
        lines = [str(x) for x in nearest.get("lines") or []]
        joined = _lyric_text(lines)
        if joined:
            texts.append(joined)
        texts.extend(_lyric_text([line]) for line in lines)
    return [t for t in texts if t]


def _song_index(t: float, windows: list[tuple[float, float]]) -> int:
    idx = 0
    for i, (start, _end) in enumerate(windows):
        if t >= start:
            idx = i
    return idx


def _events_for(spec: dict, songs: list[tuple], ocr_rows: list[dict]):
    data = json.loads((OUT / spec["dir"] / "lines.json").read_text())
    cues = data["cues"]
    windows = [(a, b) for _title, a, b in spec["songs"]]
    cursor = [-1] * len(songs)
    events: list[tuple[float, int, int, str]] = []
    matched = [0] * len(songs)
    considered = [0] * len(songs)

    for cue in cues:
        t = float(cue["t"])
        item = _song_index(t, windows)
        start, end = windows[item]
        if not (start <= t < end):
            continue
        considered[item] += 1
        _song_id, _title, slides = songs[item]
        slide = _best_slide(_texts_for(cue, ocr_rows), slides, cursor[item])
        if slide is None:
            continue
        cursor[item] = slide
        matched[item] += 1
        events.append((t, item, slide, "line"))

    for blank in data.get("blanks") or []:
        dur = float(blank.get("dur") or 0)
        if dur < BLANK_MIN_S:
            continue
        t = float(blank["t"])
        item = _song_index(t, windows)
        events.append((t, item, 0, "blank"))

    events.sort(key=lambda e: (e[0], e[3] == "blank"))
    kept: list[tuple[float, int, int]] = []
    for t, item, slide, kind in events:
        t = round(t, 3)
        if kept and abs(kept[-1][0] - t) < 0.02:
            if kind == "line":
                kept[-1] = (t, item, slide)
            continue
        if kept and kept[-1][1] == item and kept[-1][2] == slide:
            continue
        kept.append((t, item, slide))
    return kept, matched, considered


def _started_ms(rec_id: str) -> int:
    stamp = rec_id.removeprefix("rec_")
    day, rest = stamp.split("-", 1)
    clock, millis = rest.split("_")
    parsed = time.strptime(day + clock, "%Y%m%d%H%M%S")
    return int(time.mktime(parsed) * 1000) + int(millis)


def _duration(wav: Path) -> float:
    with wave.open(str(wav)) as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != 16000:
            raise SystemExit(f"{wav} is not 16 kHz mono")
        if handle.getsampwidth() != 2:
            raise SystemExit(f"{wav} is not 16-bit PCM")
        return handle.getnframes() / handle.getframerate()


def _write_one(spec: dict, songs, ocr_rows: list[dict]) -> dict:
    src = OUT / spec["dir"] / "audio.wav"
    rec_dir = RECORDINGS / spec["rec_id"]
    golden_path = rec_dir / "golden.json"
    if golden_path.exists():
        existing = json.loads(golden_path.read_text())
        if existing.get("approved"):
            raise SystemExit(f"{spec['rec_id']} is already approved; leaving it")

    kept, matched, considered = _events_for(spec, songs, ocr_rows)
    gt = [
        GroundTruthEvent(t_seconds=t, item_idx=item, slide_idx=slide)
        for t, item, slide in kept
    ]
    snapshots = []
    for i, (song_id, title, slides) in enumerate(songs):
        snapshots.append(
            SetlistItemSnapshot(
                item_idx=i,
                song_id=song_id,
                title=title,
                slides=[list(s.words) for s in slides],
                slide_sections=[section_key_of(s) for s in slides],
            )
        )
    note = (
        f"Prefilled from the Life.Church lyric video ({spec['label']}). "
        "Each time is when the burned-in line changes. "
        "Blank slides are only where the screen has no lyric for 5 seconds or more. "
        "Not reviewed."
    )
    duration = _duration(src)
    recording = Recording(
        id=spec["rec_id"],
        setlist_id="lyric_" + spec["dir"],
        started_at_ms=_started_ms(spec["rec_id"]),
        duration_seconds=round(duration, 3),
        ground_truth=gt,
        audio_path=str(rec_dir / "audio.wav"),
        notes=note,
        setlist_content=snapshots,
    )
    golden = GoldenAnnotation(
        recording_id=spec["rec_id"],
        ground_truth=gt,
        notes=note,
        edited_at_ms=recording.started_at_ms,
        approved=False,
    )
    rec_dir.mkdir(parents=True, exist_ok=True)
    dest = rec_dir / "audio.wav"
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, dest)
    payload = {"indent": 2, "exclude_none": True}
    (rec_dir / "recording.json").write_text(recording.model_dump_json(**payload))
    golden_path.write_text(golden.model_dump_json(**payload))
    blanks = sum(1 for _t, _i, slide in kept if slide == 0)
    return {
        "id": spec["rec_id"],
        "label": spec["label"],
        "events": len(kept),
        "blanks": blanks,
        "matched": matched,
        "considered": considered,
        "titles": [title for _id, title, _slides in songs],
        "dir": str(rec_dir),
    }


def main() -> None:
    ocr = _ocr_by_set()
    con = sqlite3.connect(f"file:{LIBRARY_DB}?mode=ro", uri=True)
    reports = []
    for spec in SETS:
        songs = [_load_song(con, title) for title, _a, _b in spec["songs"]]
        reports.append(_write_one(spec, songs, ocr.get(spec["dir"], [])))
    con.close()
    for report in reports:
        parts = [
            f"{title} {hit}/{total}"
            for title, hit, total in zip(
                report["titles"], report["matched"], report["considered"], strict=True
            )
        ]
        print(
            f"{report['label']}: {report['events']} cues, "
            f"{report['blanks']} blanks (>= {BLANK_MIN_S:.0f}s) — " + "; ".join(parts)
        )
        print(f"  {report['dir']}")


if __name__ == "__main__":
    main()
