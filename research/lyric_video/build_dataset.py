"""Read burned-in lyric lines off a worship video and write a research dataset.

This lives entirely under ``research/lyric_video``. It does not import the
app, does not write into the app's recording folder, and does not approve
anything. Each cue is one line, because that is what these videos put on
screen.

    uv run --project sidecar python research/lyric_video/build_dataset.py \\
        --video ~/Downloads/ChurchOnlineWorshipSet_1.mp4 \\
        [--seconds 90] [--out research/lyric_video/out]

Output, per video, under ``--out``:

    <stem>/lines.json    line cues, long gaps, and one-line-per-slide text
    <stem>/audio.wav     16 kHz mono, for a later replay
    <stem>/frames/       the frames that were read (kept so a bad read can be checked)

The Life.Church page's song order is stored as a hint. It is not checked
against the pictures.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BUILD = HERE / ".build"
OCR_SRC = HERE / "ocr_lines.swift"
OCR_BIN = BUILD / "ocr_lines"

# Bottom-center band. The line sits low, but a singer often stands in front
# of it, so the band is tall enough that the words survive a tight crop of
# the performer.
CROP = "crop=iw*0.84:ih*0.30:iw*0.08:ih*0.68,scale=960:-1,format=gray"
SAMPLE_FPS = 4
#: How long the lyric mask must hold still before it is read.
STABLE_FRAMES = 2
#: Mean absolute difference of the lyric mask, above which the words changed.
CHANGE_MAD = 0.18
#: While words are on screen, read again at least this often. A moving
#: performer changes the mask even when the line does not, and waiting for
#: a perfectly still mask drops lines.
REREAD_S = 1.25
#: A blank this long splits the set into another song. Shorter gaps are
#: the instrumental between lines of the same song.
SONG_GAP_S = 18.0
#: Frames to read across one hold, so a foot in front of the words on one
#: frame does not decide the line.
READS_PER_HOLD = 3

#: Order named on the Life.Church page. A hint for whoever checks the read,
#: not something the pictures have confirmed.
EXPECTED_ORDER = {
    "ChurchOnlineWorshipSet_1": [
        "God So Good",
        "The Artist",
        "Heaven's All Around",
    ],
    "ChurchOnlineWorshipSet_4": [
        "God So Good",
        "Never Will",
        "Death of Death",
    ],
    "ChurchOnlineWorshipSet_5-": [
        "Life To Me",
        "Never Will",
        "Grand Design",
    ],
    "Easter_2020_WorshipSet": [
        "Amazing Grace",
        "Grand Design",
        "How Great Thou Art",
        "Heaven's All Around",
    ],
    "ChurchOnlineWorshipSet_3": [
        "All Glory",
        "When I Say Jesus",
        "Grand Design",
        "Death of Death",
    ],
}


@dataclass
class Cue:
    t: float
    text: str
    confidence: float
    frame: str


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def ocr_binary() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    if OCR_BIN.exists() and OCR_BIN.stat().st_mtime >= OCR_SRC.stat().st_mtime:
        return OCR_BIN
    _run(["swiftc", "-O", "-o", str(OCR_BIN), str(OCR_SRC)])
    return OCR_BIN


def crop_size(video: Path) -> tuple[int, int]:
    png = Path(tempfile.mkdtemp()) / "probe.png"
    _run(
        [
            "ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(video),
            "-frames:v", "1", "-vf", CROP, str(png),
        ]
    )
    probe = _run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(png),
        ],
        capture_output=True,
        text=True,
    )
    w, h = (int(x) for x in probe.stdout.strip().split(","))
    png.unlink()
    png.parent.rmdir()
    return w, h


def lyric_thumb(frame: np.ndarray) -> np.ndarray | None:
    """A fixed-size mask of the burned-in line, or None when no line is up.

    The line is white and wide. Stage lights are white too, but they are a
    compact blob, so a row only counts when its bright pixels stretch across
    the band the way a sentence does. The camera can cut behind the words
    without moving this mask.
    """
    white = frame >= 205
    h, w = white.shape
    best_y = -1
    best_score = 0
    for y in range(h):
        xs = np.flatnonzero(white[y])
        if xs.size < 24:
            continue
        span = int(xs[-1] - xs[0])
        if span < w * 0.28:
            continue
        runs = 1 + int(np.count_nonzero(np.diff(xs) > 3))
        score = int(xs.size) * min(runs, 30)
        if score > best_score:
            best_score = score
            best_y = y
    if best_y < 0:
        return None
    y0 = max(0, best_y - 16)
    y1 = min(h, best_y + 16)
    band = white[y0:y1]
    cols = np.flatnonzero(band.any(axis=0))
    if cols.size < 24:
        return None
    x0 = max(0, int(cols[0]) - 6)
    x1 = min(w, int(cols[-1]) + 6)
    band = band[:, x0:x1]
    th, tw = 8, 64
    ys = np.linspace(0, band.shape[0] - 1, th).astype(int)
    xs = np.linspace(0, band.shape[1] - 1, tw).astype(int)
    return band[ys][:, xs].astype(np.float32)


def read_frames(video: Path, fps: int, start: float, seconds: float | None):
    w, h = crop_size(video)
    vf = f"{CROP},fps={fps}"
    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(video)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    assert proc.stdout is not None
    nbytes = w * h
    index = 0
    while True:
        buf = proc.stdout.read(nbytes)
        if len(buf) < nbytes:
            break
        frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w).copy()
        yield start + index / fps, frame
        index += 1
    proc.wait()
    if proc.returncode not in (0, None):
        raise SystemExit(f"ffmpeg exited {proc.returncode} reading {video.name}")


def save_png(frame: np.ndarray, path: Path) -> None:
    h, w = frame.shape
    pgm = path.with_suffix(".pgm")
    pgm.write_bytes(f"P5\n{w} {h}\n255\n".encode() + frame.tobytes())
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(pgm), str(path)])
    pgm.unlink()


@dataclass
class Shot:
    """One moment worth reading: a lyric hold, or a blank gap."""

    t: float
    end: float
    paths: list[Path]
    blank: bool


def stable_frames(
    video: Path, frames_dir: Path, *, fps: int, start: float, seconds: float | None
) -> list[Shot]:
    """Group frames into holds of one lyric line, and the blanks between them.

    The hold opens when the picture settles. A person walking through the
    words keeps it from settling, so the time here is late. ``retime_holds``
    pulls it back to the first frame that actually reads as that line.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    out: list[Shot] = []
    prev: np.ndarray | None = None
    hold = 0
    blank_since: float | None = None
    hold_paths: list[Path] = []
    hold_t: float | None = None
    last_save_t = -999.0
    last_t = start

    def close_hold(end: float) -> None:
        nonlocal hold_paths, hold_t
        if hold_t is not None and hold_paths:
            out.append(Shot(hold_t, end, list(hold_paths), False))
        hold_paths = []
        hold_t = None

    def close_blank(end: float) -> None:
        nonlocal blank_since
        if blank_since is not None and end - blank_since >= 0.4:
            out.append(Shot(blank_since, end, [], True))
        blank_since = None

    for t, frame in read_frames(video, fps, start, seconds):
        last_t = t
        thumb = lyric_thumb(frame)
        if thumb is None:
            close_hold(t)
            hold = 0
            prev = None
            if blank_since is None:
                blank_since = t
            continue
        close_blank(t)
        changed = prev is not None and float(np.abs(thumb - prev).mean()) > CHANGE_MAD
        prev = thumb
        if changed:
            close_hold(t)
            hold = 1
            continue
        hold += 1
        due = t - last_save_t >= REREAD_S
        if hold >= STABLE_FRAMES and (hold_t is None or due) and len(hold_paths) < READS_PER_HOLD:
            path = frames_dir / f"{t:09.2f}.png"
            save_png(frame, path)
            if hold_t is None:
                hold_t = t - (STABLE_FRAMES - 1) / fps
            hold_paths.append(path)
            last_save_t = t
    close_hold(last_t)
    close_blank(last_t)
    return out


def ocr_frames(paths: list[Path]) -> dict[str, list[dict]]:
    if not paths:
        return {}
    proc = subprocess.Popen(
        [str(ocr_binary())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin and proc.stdout
    payload = "\n".join(str(p) for p in paths) + "\n"
    out, _ = proc.communicate(payload)
    if proc.returncode != 0:
        raise SystemExit(f"ocr_lines exited {proc.returncode}")
    found: dict[str, list[dict]] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        found[row["path"]] = row["lines"]
    return found


_WORD = re.compile(r"[^A-Za-z' ]+")


def clean_text(raw: str) -> str:
    text = raw.upper().replace("’", "'").replace("‘", "'")
    text = _WORD.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def best_line(lines: list[dict]) -> tuple[str, float]:
    """The lyric, when the frame has one. Stage clutter reads as a second,
    low-confidence string; the burned-in line is the longer confident one."""
    ranked: list[tuple[float, str, float]] = []
    for hit in lines:
        text = clean_text(hit.get("text") or "")
        conf = float(hit.get("confidence") or 0)
        words = text.split()
        if conf < 0.35 or len(words) < 2 or len(text) < 6:
            continue
        ranked.append((conf * len(text), text, conf))
    if not ranked:
        return "", 0.0
    _, text, conf = max(ranked)
    return text, conf


# The mask stamps a line when the picture settles. A person in front of the
# words keeps it moving, so that stamp is up to about a second late — most of
# a two-second chorus line. Look back this far and take the first frame whose
# reading is the line.
RETIME_LOOKBACK_S = 1.5


def same_lyric(a: str, b: str) -> bool:
    """True when two readings are the same slide.

    "YOU WILL WALK ON" and "YOU WILL WALK ON WITH ME" are different slides:
    the video holds the short line, then adds the rest. A reading that drops
    or mangles one word is still the same slide.
    """
    wa, wb = a.split(), b.split()
    if not wa or not wb or abs(len(wa) - len(wb)) > 1:
        return False
    if wa == wb:
        return True
    if len(wa) != len(wb) and (wa[: len(wb)] == wb or wb[: len(wa)] == wa):
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.72


def retime_holds(cues: list[tuple[float, str]], reads: list[tuple[float, str]]) -> list[float]:
    """Pull each cue back to the first confirmed frame of its own line.

    `reads` is `(t, text)` at the sample rate. Text is empty when the frame
    had no lyric. A cue moves only when a later frame agrees, so one garbled
    frame cannot drag it into the previous line. `cues` times are the late
    mask stamps, and a second pass must use those same stamps.
    """
    new_times: list[float] = []
    prev = -1e9
    ordered = sorted(reads)
    for mask_t, text in cues:
        lo = max(prev + 1.0 / SAMPLE_FPS, mask_t - RETIME_LOOKBACK_S)
        window = [(ft, ftext) for ft, ftext in ordered if lo - 1e-6 <= ft <= mask_t + 1e-6]
        hit: float | None = None
        for i, (ft, ftext) in enumerate(window):
            if not ftext or not same_lyric(ftext, text):
                continue
            agreed = False
            for _, later in window[i + 1 :]:
                if not later:
                    continue
                agreed = same_lyric(later, text)
                break
            if agreed:
                hit = ft
                break
        new_t = mask_t if hit is None or hit <= prev else hit
        new_times.append(new_t)
        prev = new_t
    return new_times


def stamp_ends(cues: list[dict], blanks: list[dict], *, horizon: float = 8.0) -> None:
    """A line stays up until the next line, or until the lyric leaves the screen.

    A blank of a second or more is the line going away. Without that cutoff
    the clock keeps the last line through the instrumental and the talk.
    """
    gaps = sorted(float(b["t"]) for b in blanks if float(b.get("dur") or 0) >= 1.0)
    for i, cue in enumerate(cues):
        nxt = float(cues[i + 1]["t"]) if i + 1 < len(cues) else float(cue["t"]) + horizon
        end = nxt
        for gap in gaps:
            if float(cue["t"]) < gap < end:
                end = gap
                break
        cue["end"] = round(max(end, float(cue["t"])), 3)


def retime_dataset(dest: Path) -> dict:
    """Move existing cue times back to the first frame that reads as the line.

    Leaves audio and the saved hold frames alone. The mask time is kept on
    each cue as ``mask_t`` so a second run starts from the same late stamp.
    """
    doc_path = dest / "lines.json"
    doc = json.loads(doc_path.read_text())
    video = Path(doc["source_video"])
    if not video.exists():
        raise SystemExit(f"no such video: {video}")
    cues = doc["cues"]
    anchors = [float(c.get("mask_t", c["t"])) for c in cues]
    windows = [(t - RETIME_LOOKBACK_S, t) for t in anchors]
    tmp = Path(tempfile.mkdtemp(prefix="lyric-retime-"))
    saved: list[tuple[float, Path]] = []
    try:
        for t, frame in read_frames(video, SAMPLE_FPS, float(doc.get("start_seconds") or 0), doc.get("limit_seconds")):
            if any(lo - 1e-6 <= t <= hi + 1e-6 for lo, hi in windows):
                path = tmp / f"{t:09.2f}.png"
                save_png(frame, path)
                saved.append((t, path))
        reads_raw = ocr_frames([p for _, p in saved])
        reads = [(t, best_line(reads_raw.get(str(p), []))[0]) for t, p in saved]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    pairs = list(zip(anchors, [c["text"] for c in cues]))
    new_times = retime_holds(pairs, reads)
    for cue, mask_t, new_t in zip(cues, anchors, new_times):
        cue["mask_t"] = round(mask_t, 3)
        cue["t"] = round(new_t, 3)
    rebuilt = segments(
        [Cue(c["t"], c["text"], c["confidence"], c["frame"]) for c in cues],
        doc.get("blanks") or [],
    )
    doc["segments"] = rebuilt
    doc["time_source"] = "ocr-confirm"
    stamp_ends(cues, doc.get("blanks") or [])
    doc_path.write_text(json.dumps(doc, indent=2))
    moved = sum(1 for a, b in zip(anchors, new_times) if abs(a - b) >= 0.2)
    earlier = [b - a for a, b in zip(anchors, new_times)]
    print(
        f"{dest.name}: {moved}/{len(cues)} cues moved, "
        f"median {sorted(earlier)[len(earlier)//2]:+.2f}s"
    )
    return doc


def vote(paths: list[Path], reads: dict[str, list[dict]]) -> tuple[str, float, str]:
    """Several frames of one hold. A foot over the words spoils one of them;
    the reading that comes back twice wins, else the longer confident one."""
    options: list[tuple[str, float, str]] = []
    for path in paths:
        text, conf = best_line(reads.get(str(path), []))
        if text:
            options.append((text, conf, path.name))
    if not options:
        return "", 0.0, paths[0].name if paths else ""
    counts: dict[str, int] = {}
    for text, _, _ in options:
        counts[text] = counts.get(text, 0) + 1
    # A cleaner reading beats a repeated garbled one. Repetition is the tiebreak.
    text, conf, frame = max(
        options,
        key=lambda item: (reading_quality(item[0]), counts[item[0]], item[1], len(item[0])),
    )
    return text, conf, frame


def reading_quality(text: str) -> float:
    """Prefer a line broken into ordinary words over one the reader glued together."""
    words = text.split()
    if len(words) < 2:
        return 0.0
    good = sum(1 for w in words if len(w) <= 9)
    glued = sum(1 for w in words if len(w) >= 11)
    return good - 2.5 * glued


def collapse_rereads(cues: list[Cue], window: float = 6.5, ratio: float = 0.55) -> list[Cue]:
    """One line is often read twice, the second time with a foot in the way.

    A later line that continues a clean earlier line ("... LOVE" then
    "... LOVE FOR ME") is kept. A garbled twin is dropped.
    """
    kept: list[Cue] = []
    for cue in cues:
        if not kept or cue.t - kept[-1].t > window:
            kept.append(cue)
            continue
        prev = kept[-1]
        sim = SequenceMatcher(None, prev.text, cue.text).ratio()
        prev_words = prev.text.split()
        new_words = cue.text.split()
        extends = (
            len(new_words) > len(prev_words)
            and new_words[: len(prev_words)] == prev_words
            and reading_quality(prev.text) >= len(prev_words) - 0.5
        )
        if (
            not extends
            and sim >= 0.45
            and reading_quality(cue.text) + 1.5 < reading_quality(prev.text)
        ):
            continue
        if extends or sim < ratio:
            kept.append(cue)
            continue
        if reading_quality(cue.text) > reading_quality(prev.text) + 0.4 or (
            reading_quality(cue.text) >= reading_quality(prev.text) and len(cue.text) > len(prev.text)
        ):
            kept[-1] = Cue(prev.t, cue.text, max(prev.confidence, cue.confidence), cue.frame)
    return kept


def assemble(shots: list[Shot], reads: dict[str, list[dict]]) -> tuple[list[Cue], list[dict]]:
    cues: list[Cue] = []
    blanks: list[dict] = []
    for shot in shots:
        if shot.blank:
            blanks.append({"t": round(shot.t, 3), "end": round(shot.end, 3), "dur": round(shot.end - shot.t, 3)})
            continue
        text, conf, frame = vote(shot.paths, reads)
        if not text:
            continue
        if cues and cues[-1].text == text:
            # Same words still up, or a flicker with no real blank between.
            if conf > cues[-1].confidence:
                cues[-1] = Cue(cues[-1].t, text, conf, frame)
            continue
        cues.append(Cue(shot.t, text, conf, frame))
    return collapse_rereads(cues), blanks


def segments(cues: list[Cue], blanks: list[dict]) -> list[dict]:
    """Split where the lyric was gone for SONG_GAP_S. A breath between lines
    stays inside the song."""
    if not cues:
        return []
    splits = {b["t"] for b in blanks if b["dur"] >= SONG_GAP_S}
    groups: list[list[Cue]] = [[cues[0]]]
    for cur in cues[1:]:
        if any(groups[-1][-1].t < split <= cur.t for split in splits):
            groups.append([cur])
        else:
            groups[-1].append(cur)
    out = []
    for group in groups:
        # One line per slide: a blank line is the song-format slide break.
        body = "\n\n".join(c.text for c in group)
        out.append(
            {
                "start": round(group[0].t, 3),
                "end": round(group[-1].t, 3),
                "lines": len(group),
                "body_text": body,
            }
        )
    return out


def extract_audio(video: Path, dest: Path, start: float, seconds: float | None) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(video)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(dest)]
    _run(cmd)


def build_one(
    video: Path,
    out_root: Path,
    *,
    start: float,
    seconds: float | None,
    with_audio: bool,
) -> Path:
    stem = video.stem
    dest = out_root / stem
    if dest.exists():
        shutil.rmtree(dest)
    frames = dest / "frames"
    samples = stable_frames(video, frames, fps=SAMPLE_FPS, start=start, seconds=seconds)
    pictures = [p for shot in samples for p in shot.paths]
    reads = ocr_frames(pictures)
    cues, blanks = assemble(samples, reads)
    segs = segments(cues, blanks)
    doc = {
        "source_video": str(video),
        "note": (
            "Research dataset from burned-in lyric video. Not an app recording. "
            "Each cue is one line, which is one slide."
        ),
        "sample_fps": SAMPLE_FPS,
        "start_seconds": start,
        "limit_seconds": seconds,
        "expected_order_unverified": EXPECTED_ORDER.get(stem, []),
        "blanks": blanks,
        "cues": [
            {
                "t": round(c.t, 3),
                "text": c.text,
                "confidence": round(c.confidence, 3),
                "frame": c.frame,
            }
            for c in cues
        ],
        "segments": segs,
    }
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "lines.json").write_text(json.dumps(doc, indent=2))
    if with_audio:
        extract_audio(video, dest / "audio.wav", start, seconds)
    retime_dataset(dest)
    return dest


def self_test() -> None:
    cues = [
        Cue(1, "LINE ONE", 0.9, "a.png"),
        Cue(5, "LINE TWO", 0.9, "b.png"),
        Cue(40, "OTHER SONG", 0.9, "c.png"),
    ]
    segs = segments(cues, [{"t": 10.0, "end": 30.0, "dur": 20.0}])
    assert len(segs) == 2, segs
    assert segs[0]["body_text"] == "LINE ONE\n\nLINE TWO"
    assert segs[1]["lines"] == 1
    assert clean_text("Because, of your love!") == "BECAUSE OF YOUR LOVE"
    collapsed = collapse_rereads(
        [
            Cue(1, "YOU WILL WALK ON", 0.9, "a"),
            Cue(2, "YOU WILL WORK ON", 0.9, "b"),
            Cue(4, "YOU WILL WALK ON WITH ME", 0.9, "c"),
            Cue(8, "BECAUSE OF YOUR LOVE", 0.9, "d"),
            Cue(9, "BEGAUSE OF YOUR LOVE", 0.9, "e"),
        ]
    )
    assert [c.text for c in collapsed] == [
        "YOU WILL WALK ON",
        "YOU WILL WALK ON WITH ME",
        "BECAUSE OF YOUR LOVE",
    ], [c.text for c in collapsed]
    assert best_line(
        [
            {"text": "Gretsch", "confidence": 0.4},
            {"text": "Nothing precious or refined", "confidence": 0.9},
        ]
    )[0] == "NOTHING PRECIOUS OR REFINED"
    # The short line and the line that adds the rest are different slides.
    # A one-word garble of the same slide still matches.
    assert not same_lyric("YOU WILL WALK ON", "YOU WILL WALK ON WITH ME")
    assert not same_lyric("WILL WALK ON", "YOU WILL WALK ON WITH ME")
    assert same_lyric("OH WHAT PEACE KVE FOUND", "OH WHAT PEACE I'VE FOUND")
    assert same_lyric("AI PEACE F'VE FOUND", "OH WHAT PEACE I'VE FOUND")
    # The mask opens late. The cue moves to the first frame that reads as
    # the new line, once the next frame agrees. One garbled frame does not.
    on_me = [
        (42.25, "WILL WALK ON"),
        (42.50, "YOU WILL S"),
        (42.75, "YOU WILL WALK ON WITH ME"),
        (43.00, "YOU WILL WALK ON WITH ME"),
        (43.75, "YOU WILL WALK ON WITH ME"),
    ]
    assert retime_holds([(43.75, "YOU WILL WALK ON WITH ME")], on_me) == [42.75]
    joy = [
        (44.25, "YOU WILL WALK ON WITH ME"),
        (44.75, "AI PEACE F'VE FOUND"),
        (45.00, "OHI WHAT PEACE T'YE FOUND"),
        (46.00, "OH WHAT PEACE I'VE FOUND"),
    ]
    assert retime_holds([(46.00, "OH WHAT PEACE I'VE FOUND")], joy) == [44.75]
    assert retime_holds([(43.75, "YOU WILL WALK ON WITH ME")], [(43.75, "YOU WILL WALK ON WITH ME")]) == [43.75]
    # A line ends when the lyric leaves, not when the next line finally arrives.
    held = [{"t": 119.5, "text": "BECAUSE OF YOUR LOVE FOR ME"}, {"t": 149.25, "text": "I'M NOT ALONE"}]
    stamp_ends(held, [{"t": 123.0, "dur": 2.0, "end": 125.0}, {"t": 131.0, "dur": 3.0, "end": 134.0}])
    assert held[0]["end"] == 123.0
    assert held[1]["end"] == 157.25


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--self-test"]:
        self_test()
        print("ok")
        return 0
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", type=Path, action="append", default=[])
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=None, help="Only read this many seconds.")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument(
        "--retime",
        type=Path,
        action="append",
        default=[],
        help="Pull existing cue times in this dataset dir back to the first frame that reads as the line.",
    )
    args = ap.parse_args(argv)
    if not args.video and not args.retime:
        ap.error("pass --video or --retime")
    for dest in args.retime:
        retime_dataset(dest)
    for video in args.video:
        if not video.exists():
            raise SystemExit(f"no such video: {video}")
        dest = build_one(
            video,
            args.out,
            start=args.start,
            seconds=args.seconds,
            with_audio=not args.no_audio,
        )
        doc = json.loads((dest / "lines.json").read_text())
        print(f"{video.name}: {len(doc['cues'])} lines, {len(doc['segments'])} segments -> {dest}")
        for i, seg in enumerate(doc["segments"], 1):
            first = seg["body_text"].split("\n", 1)[0]
            print(f"  {i}. {seg['start']:.0f}s–{seg['end']:.0f}s  {seg['lines']} lines  {first}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
