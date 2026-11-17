"""Bootstrap a golden recording from a studio/live audio file + a library song.

Turns "an mp3 of a worship song" into a recording the Golden Recording
Editor can open, with slide-transition ground truth already roughed in so
the human only has to nudge markers rather than place every one by hand.

Pipeline
--------
1. Resolve the song from the app's SQLite library (`--title`, fuzzy).
2. Parse `body_text` into slides with the canonical Python parser, and
   prepend the synthetic leading blank slide the app always shows
   (`buildSetlistContent(..., {leadingBlank: true})`) so slide indices
   line up with a real capture.
3. Transcode the audio to 16 kHz mono PCM WAV (the format `RecordingSink`
   writes and the editor's WebAudio decode expects).
4. Transcribe with faster-whisper, word timestamps on.
5. Decode which slide the band is on for every transcript word, with a
   Viterbi pass whose emissions are IDF-weighted `phonetic_match_score`
   matches (the matcher's own scorer) and whose state carries how long the
   slide has been held. Revisiting a slide is an ordinary transition, so
   repeated choruses come out as repeated cues; the dwell term is what keeps
   songs built from near-identical slides from freezing on one of them.
   Scored against two hand-annotated recordings, this lifts mean F1 from
   0.75 (the older single-pass aligner, `--engine linear`) to 0.91.
6. Re-time each cue onto the sung vocal: Demucs separates the vocal and a
   wav2vec2 CTC model forces the slide's words onto it, in a window around
   the decoded cue (`retime_cues`; `--no-retime` skips it). Whisper's
   timestamps land a line a median 0.37 s off; this lands it 0.08 s off.
7. Write `recording.json` (raw) + `golden.json` (the roughed-in ground
   truth) into a new recording directory.

The `golden.json` is what makes this safe: `eval.replay.load_recording`
overlays it, and the editor loads it for tweaking, while `recording.json`
keeps a machine-generated baseline to diff against.

Usage
-----
    uv run --project sidecar python research/eval/bootstrap_golden.py \
        --audio "~/Music/.../01 Reckless Love.m4a" \
        --title "Reckless Love" \
        --model small.en
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sidecar"))

from auto_presenter.eval.types import (  # noqa: E402
    GoldenAnnotation,
    GroundTruthEvent,
    Recording,
    SetlistItemSnapshot,
)
from auto_presenter.index.phonetic import (  # noqa: E402
    normalize,
    phonetic_match_score,
)
from auto_presenter.library.parser import parse_body_text  # noqa: E402

DEFAULT_LIBRARY = Path.home() / (
    "Library/Application Support/com.churchslides.app/library.db"
)
DEFAULT_RECORDINGS = Path.home() / (
    "Library/Application Support/com.churchslides.app/recordings"
)
SAMPLE_RATE = 16_000

# Alignment scoring. `phonetic_match_score` returns [0, 1]; anything under
# MATCH_BIAS is treated as evidence *against* pairing the two words.
MATCH_BIAS = 0.45
GAP_REF = -0.35  # a reference word nobody sang / ASR missed
GAP_HYP = -0.35  # an ASR word with no home in the lyrics (ad-libs, noise)


# --------------------------------------------------------------------- library


def _norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).lower()
    s = re.sub(r"\(.*?\)", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


@dataclass
class LibrarySong:
    id: str
    title: str
    body_text: str


def find_song(db_path: Path, title: str) -> LibrarySong:
    """Look up a song by title — exact first, then normalized, then substring.

    Read-only (`mode=ro`), not `immutable=1`: immutable tells SQLite the file
    will never change and skips checking the WAL, which silently reads a
    stale snapshot of a library the app still has open. Measured: a song
    added minutes earlier sat only in `library.db-wal`; `immutable=1` missed
    it entirely and `find_song` fell through to substring matching, which
    collided two unrelated titles onto the same row ("The Blood" resolved to
    "Nothing But The Blood (Plainfield)"). `mode=ro` still merges the WAL —
    it just can't write — which is exactly what a read-only lookup needs.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = con.execute("select id, title, body_text from songs").fetchall()
    con.close()

    want = _norm_title(title)
    exact = [r for r in rows if r[1].lower() == title.lower()]
    if exact:
        return LibrarySong(*exact[0])
    normed = [r for r in rows if _norm_title(r[1]) == want]
    if normed:
        return LibrarySong(*normed[0])
    partial = [r for r in rows if want and want in _norm_title(r[1])]
    if len(partial) == 1:
        return LibrarySong(*partial[0])
    if partial:
        names = ", ".join(sorted(r[1] for r in partial))
        raise SystemExit(f"ambiguous title {title!r} — candidates: {names}")
    raise SystemExit(f"no song in the library matches {title!r}")


def build_slides(song: LibrarySong) -> tuple[list[list[str]], list[str]]:
    """Parsed slides + parallel section keys, with the leading blank prepended.

    Mirrors `buildSetlistContent` in `src/lib/setlistContent.ts`, which
    always passes `{leadingBlank: true}` — slide 0 is the wordless blank
    every song opens on, so ground-truth slide indices start at 1.
    """
    slides, _sections = parse_body_text(song.body_text, song.id)
    words = [[]] + [list(s.words) for s in slides]
    keys = ["other:0"] + [f"{s.section.type}:{s.section.number}" for s in slides]
    return words, keys


def flatten_songs(
    songs: list[LibrarySong],
) -> tuple[list[list[str]], list[str], list[int], list[int], set[int]]:
    """Concatenate several songs' slides into one flat sequence, for a
    recording that moves between songs — a real setlist, or a leader
    deviating from the planned order into a song sung nowhere near where it
    was expected.

    Returns `(slides, sections, item_of, local_of, blank_indices)` — the
    flat slide/section lists `decode_slides` scans over, parallel arrays
    mapping each flat index back to `(item_idx, local slide_idx)`, and the
    set of flat indices that are a song's own leading blank.

    Each song keeps its own blank; all of them stay excluded from the
    decodable state space, the same way the single-song blank always was.
    That exclusion is what makes a song boundary behave correctly with zero
    other changes to the decoder: the sequential-`advance` bonus only fires
    when the *previous* flat index was itself a scored state, and a blank
    never is — so arriving at another song's first real slide is only ever
    reachable through the jump mechanism, which is what a leader actually
    switching songs is.
    """
    flat_slides: list[list[str]] = []
    flat_sections: list[str] = []
    item_of: list[int] = []
    local_of: list[int] = []
    blanks: set[int] = set()
    for item_idx, song in enumerate(songs):
        slides, sections = build_slides(song)
        blanks.add(len(flat_slides))
        for local_idx, (words, key) in enumerate(zip(slides, sections, strict=True)):
            flat_slides.append(words)
            flat_sections.append(key)
            item_of.append(item_idx)
            local_of.append(local_idx)
    return flat_slides, flat_sections, item_of, local_of, blanks


# ----------------------------------------------------------------------- audio


def transcode(src: Path, dest: Path) -> float:
    """ffmpeg → 16 kHz mono 16-bit PCM WAV. Returns duration in seconds."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16",
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    with wave.open(str(dest), "rb") as w:
        return w.getnframes() / float(w.getframerate())


@dataclass
class HypWord:
    word: str
    start: float
    end: float


def transcribe_cached(
    wav_path: Path, model_size: str, cache: Path | None
) -> list[HypWord]:
    """`transcribe`, memoised to a JSON file beside the recording.

    ASR is by far the slowest step and is a pure function of (audio, model),
    so caching it makes re-annotation with a changed aligner effectively
    free — which is the difference between iterating on alignment in seconds
    and in tens of minutes.
    """
    if cache and cache.exists():
        try:
            data = json.loads(cache.read_text())
            if data.get("model") == model_size and data.get("slicing") == _SLICE_TAG:
                return [HypWord(**w) for w in data["words"]]
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            log_warn(f"ignoring unreadable transcript cache {cache}: {exc}")
    words = transcribe(wav_path, model_size)
    if cache:
        cache.write_text(
            json.dumps(
                {
                    "model": model_size,
                    "slicing": _SLICE_TAG,
                    "words": [w.__dict__ for w in words],
                },
                indent=1,
            )
        )
    return words


def log_warn(msg: str) -> None:
    print(f"  !! {msg}")


# Whisper reads a buffer and drops two places: the back of a 30 s window,
# and the start of a slice that begins in the middle of a phrase. A line
# sung at 832 s was missing from a 16 s slice that ended on it, and missing
# again from the next slice because that one opened on the line and skipped
# ahead to the next phrase. Each slice therefore starts 8 s before the words
# we keep and runs 6 s past them, and the kept stretches tile the file.
_SLICE_S = 22.0
_SKIP_S = 8.0
_KEEP_S = 8.0
_SLICE_TAG = "22s/skip8/keep8/gaps"


def _wav_audio(wav_path: Path) -> tuple["np.ndarray", float]:
    import numpy as np

    with wave.open(str(wav_path)) as handle:
        rate = handle.getframerate()
        if rate != SAMPLE_RATE or handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise SystemExit(f"{wav_path} is not {SAMPLE_RATE} Hz mono 16-bit")
        samples = np.frombuffer(handle.readframes(handle.getnframes()), np.int16)
    audio = samples.astype(np.float32) / 32768.0
    return audio, len(audio) / SAMPLE_RATE


def _slice_words(model: object, audio: "np.ndarray", t0: float, dur: float) -> list[HypWord]:
    import numpy as np

    chunk = np.ascontiguousarray(audio[int(t0 * SAMPLE_RATE) : int((t0 + dur) * SAMPLE_RATE)])
    segments, _info = model.transcribe(  # type: ignore[attr-defined]
        chunk,
        word_timestamps=True,
        # VAD off on purpose: Silero reads sustained instrumentation as
        # non-speech and drops most of a sung track (measured: 60 words
        # kept out of 349 on a 5-minute worship song).
        vad_filter=False,
        beam_size=5,
        condition_on_previous_text=False,
    )
    out: list[HypWord] = []
    for seg in segments:
        for word in seg.words or []:
            token = re.sub(r"[^a-z0-9']+", "", word.word.strip().lower())
            if token:
                out.append(HypWord(token, t0 + float(word.start), t0 + float(word.end)))
    return out


def transcribe(wav_path: Path, model_size: str) -> list[HypWord]:
    """faster-whisper with word timestamps. Returns a flat word stream."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    audio, duration = _wav_audio(wav_path)
    kept: list[HypWord] = []
    t_keep = 0.0
    tail = _SLICE_S - _SKIP_S - _KEEP_S
    while t_keep < duration - 0.3:
        audio_start = max(0.0, t_keep - _SKIP_S)
        audio_end = min(duration, t_keep + _KEEP_S + tail)
        keep_hi = min(duration, t_keep + _KEEP_S)
        for word in _slice_words(model, audio, audio_start, audio_end - audio_start):
            if t_keep - 1e-3 <= word.start < keep_hi - 1e-3:
                kept.append(word)
        if audio_end >= duration - 0.05:
            break
        t_keep += _KEEP_S
    kept.sort(key=lambda word: word.start)
    # A word on the seam is heard by both slices. Keep the earlier one.
    merged = _dedup(kept)
    # A slice that opens on a hallucination, or on the middle of a phrase,
    # can skip a whole kept stretch. Re-read any hole longer than a line.
    return _fill_gaps(model, audio, duration, merged)


def _dedup(words: list[HypWord]) -> list[HypWord]:
    out: list[HypWord] = []
    for word in words:
        if out and word.word == out[-1].word and word.start - out[-1].start < 0.4:
            continue
        out.append(word)
    return out


def _fill_gaps(model: object, audio: "np.ndarray", duration: float, words: list[HypWord]) -> list[HypWord]:
    """Re-read any stretch longer than a sung line that came back empty.

    The second read starts a few seconds into the hole, because a read that
    begins on the previous token (or on the "thanks for watching" hallucination
    at the top of a file) skips the line that follows.
    """
    extra: list[HypWord] = []
    # A hallucinated token can be given an end time many seconds later, which
    # hides the sung line underneath it. A real word is not that long.
    edges = [HypWord("", 0.0, 0.0)]
    for word in words:
        edges.append(HypWord(word.word, word.start, min(word.end, word.start + 1.2)))
    edges.append(HypWord("", duration, duration))
    for prev, nxt in zip(edges, edges[1:]):
        if nxt.start - prev.end < 6.0:
            continue
        best: list[HypWord] = []
        for offset in (0.0, 4.0):
            t0 = prev.end + offset
            if t0 >= nxt.start - 1.0:
                continue
            dur = min(20.0, nxt.start - t0 + 4.0)
            heard = [
                word
                for word in _slice_words(model, audio, t0, dur)
                if prev.end + 0.2 <= word.start < nxt.start - 0.2
            ]
            if len(heard) > len(best):
                best = heard
        extra.extend(best)
    if not extra:
        return words
    return _dedup(sorted([*words, *extra], key=lambda word: word.start))


# ------------------------------------------------------------------- alignment


def align(ref: list[str], hyp: list[HypWord]) -> list[int | None]:
    """Needleman-Wunsch align reference lyrics to ASR words.

    Returns, for each reference word, the index of the hypothesis word it
    pairs with (or None if it went unmatched). Scoring is phonetic rather
    than exact so mis-heard sung words still anchor the timeline.
    """
    n, m = len(ref), len(hyp)
    if n == 0 or m == 0:
        return [None] * n

    # score[i][j] = best score aligning ref[:i] with hyp[:j].
    neg = float("-inf")
    score = [[neg] * (m + 1) for _ in range(n + 1)]
    # 0 = diagonal (pair), 1 = up (ref gap), 2 = left (hyp gap)
    back = [[0] * (m + 1) for _ in range(n + 1)]
    score[0][0] = 0.0
    for i in range(1, n + 1):
        score[i][0] = score[i - 1][0] + GAP_REF
        back[i][0] = 1
    for j in range(1, m + 1):
        score[0][j] = score[0][j - 1] + GAP_HYP
        back[0][j] = 2

    for i in range(1, n + 1):
        r = ref[i - 1]
        row, prev = score[i], score[i - 1]
        brow = back[i]
        for j in range(1, m + 1):
            diag = prev[j - 1] + (phonetic_match_score(hyp[j - 1].word, r) - MATCH_BIAS)
            up = prev[j] + GAP_REF
            left = row[j - 1] + GAP_HYP
            best, arg = diag, 0
            if up > best:
                best, arg = up, 1
            if left > best:
                best, arg = left, 2
            row[j], brow[j] = best, arg

    pairing: list[int | None] = [None] * n
    i, j = n, m
    while i > 0 or j > 0:
        move = back[i][j]
        if i > 0 and j > 0 and move == 0:
            # Only keep pairs that are actually plausible — a forced diagonal
            # through junk would plant a bogus anchor.
            if phonetic_match_score(hyp[j - 1].word, ref[i - 1]) >= 0.6:
                pairing[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif i > 0 and (move == 1 or j == 0):
            i -= 1
        else:
            j -= 1
    return pairing


# --- repeat-aware decoding ----------------------------------------------------
#
# The linear aligner below walks the slide list exactly once, so it structurally
# cannot emit a slide twice — and worship arrangements repeat constantly (a
# measured example: a chorus block sung 3x and a bridge block 4x, where a human
# annotator marked 36 transitions and the linear pass produced 16).
#
# So decode instead: treat the slide the band is on as a hidden state over the
# transcript and run Viterbi. Revisiting an earlier slide is just another
# transition, so repeats fall out of the model rather than needing special
# cases.

#: Log-scores for the three moves. Staying is free; advancing is cheap;
#: jumping anywhere else is possible but must be paid for, which is what
#: stops the decoder flickering between near-identical slides.
STAY_SCORE = 0.0
ADVANCE_SCORE = -0.6
JUMP_SCORE = -3.5
#: A run shorter than this many words is noise, not a slide the operator
#: would have cued.
MIN_RUN_WORDS = 3
#: How hard to charge for a slide held far from its natural length (in words).
#: This is the only thing distinguishing repeats of an identical slide.
DWELL_WEIGHT = 2.0
#: How hard to charge for cutting a slide short. Kept lighter than the
#: overstay charge — bands truncate a line far more often than they dwell on
#: one for twice its length.
EARLY_EXIT_WEIGHT = 0.5


def _slide_word_sets(slides: list[list[str]]) -> list[list[str]]:
    """Normalized words per slide, blank slides included as empty lists."""
    return [[normalize(w) for w in words if normalize(w)] for words in slides]


def emission_matrix(
    hyp: list[HypWord], slides: list[list[str]]
) -> list[list[float]]:
    """`E[i][s]` — how much word `i` argues for the band being on slide `s`.

    Each word scores against a slide by its best phonetic match within that
    slide, weighted by inverse document frequency: "the" appears on every
    slide and says nothing, while "wretch" pins one slide exactly. Without
    the IDF term, long slides win everything by sheer word count.
    """
    words_by_slide = _slide_word_sets(slides)
    n_slides = len(slides)

    # Document frequency over slides, using the same phonetic notion of
    # "contains" that the emission uses.
    df: dict[str, int] = {}
    for hw in hyp:
        w = normalize(hw.word)
        if w in df:
            continue
        df[w] = sum(
            1
            for sw in words_by_slide
            if any(phonetic_match_score(w, v) >= 0.9 for v in sw)
        )

    out: list[list[float]] = []
    for hw in hyp:
        w = normalize(hw.word)
        idf = math.log((n_slides + 1) / (1 + df.get(w, 0)))
        row = [0.0] * n_slides
        for s, sw in enumerate(words_by_slide):
            if not sw:
                continue  # blank slide — no lyric evidence either way
            best = max((phonetic_match_score(w, v) for v in sw), default=0.0)
            row[s] = best * idf
        out.append(row)
    return out


def decode_slides(
    hyp: list[HypWord],
    slides: list[list[str]],
    *,
    blank_indices: set[int] | None = None,
    advance_score: float = ADVANCE_SCORE,
    jump_score: float = JUMP_SCORE,
    dwell_weight: float = DWELL_WEIGHT,
    early_exit_weight: float = EARLY_EXIT_WEIGHT,
) -> list[int]:
    """Viterbi the most likely slide for every transcript word.

    The state is `(slide, dwell)` — which slide, and how many words have been
    spent on it — not just the slide. The dwell term is what makes textually
    *identical* slides decodable: a song whose chorus slide appears four times
    with the same words offers no lyric evidence for moving on, so without a
    duration model the decoder parks on the first one and never advances
    (measured: 7 cues found where a human marked 14). Charging for leaving a
    slide far from its natural length restores it.

    `blank_indices` (default `{0}`) is excluded from the state space — a
    wordless slide carries no lyric evidence, so nothing can ever argue for
    it. `slides` may be several songs concatenated (`flatten_songs`), one
    blank per song; excluding all of them is also what makes a song boundary
    behave correctly with no other change here. The sequential `advance`
    bonus only fires when the *previous* flat index was itself a scored
    state, and a blank never is — so the first real slide of another song is
    only reachable through the jump mechanism, which is what a leader
    actually switching songs is.
    """
    blanks = blank_indices if blank_indices is not None else {0}
    n_slides = len(slides)
    states = [i for i in range(n_slides) if i not in blanks]
    if not states or not hyp:
        # Nothing but blanks (or nothing sung) — there is no slide to be on,
        # and inventing one would cue a slide that doesn't exist.
        return []

    emissions = emission_matrix(hyp, slides)
    lengths = {s: max(1, len(slides[s])) for s in states}
    d_max = min(60, max(lengths.values()) * 3)

    def stay_cost(s: int, dwell: int) -> float:
        """Cost of holding slide `s` for one more word, at `dwell` already.

        Free up to the slide's natural length, then charged per extra word.
        Charging continuously rather than on exit is essential: an exit-only
        penalty is never paid by a decoder that simply never leaves, which is
        exactly how a song of near-identical slides freezes on one of them
        and emits nothing for the rest of the track.
        """
        n = lengths[s]
        over = (dwell + 1) - n
        return 0.0 if over <= 0 else -dwell_weight * over / n

    def leave_penalty(s: int, dwell: int) -> float:
        """Cost of leaving slide `s` early, having spent `dwell + 1` words."""
        n = lengths[s]
        short = n - (dwell + 1)
        return 0.0 if short <= 0 else -early_exit_weight * short / n

    neg = float("-inf")
    # score[s][d]; only d=0 is reachable for the first word.
    score = {s: [neg] * (d_max + 1) for s in states}
    for s in states:
        score[s][0] = emissions[0][s]
    back: list[dict[tuple[int, int], tuple[int, int]]] = [{}]

    for i in range(1, len(hyp)):
        row = emissions[i]
        # Best (value, state, dwell) to leave from, per source slide.
        leave: dict[int, tuple[float, int]] = {}
        for s in states:
            best_v, best_d = neg, 0
            col = score[s]
            for d in range(d_max + 1):
                v = col[d]
                if v == neg:
                    continue
                v += leave_penalty(s, d)
                if v > best_v:
                    best_v, best_d = v, d
            leave[s] = (best_v, best_d)

        # Jumping to any slide draws from the same global best source, so
        # find the top two once (top one, in case it *is* the destination).
        ranked = sorted(states, key=lambda s: leave[s][0], reverse=True)
        top1 = ranked[0]
        top2 = ranked[1] if len(ranked) > 1 else None

        nxt = {s: [neg] * (d_max + 1) for s in states}
        step: dict[tuple[int, int], tuple[int, int]] = {}
        for s in states:
            # d = 0: arrived here this word, by advancing or jumping.
            best, arg = neg, None
            if s - 1 in leave:
                v = leave[s - 1][0] + advance_score
                if v > best:
                    best, arg = v, (s - 1, leave[s - 1][1])
            src = top1 if top1 != s else top2
            if src is not None:
                v = leave[src][0] + jump_score
                if v > best:
                    best, arg = v, (src, leave[src][1])
            if arg is not None:
                nxt[s][0] = best + row[s]
                step[(s, 0)] = arg

            # d > 0: stayed on this slide.
            col = score[s]
            for d in range(1, d_max + 1):
                v = col[d - 1]
                if v == neg:
                    continue
                nxt[s][d] = v + stay_cost(s, d - 1) + row[s]
                step[(s, d)] = (s, d - 1)

        score = nxt
        back.append(step)

    # Backtrack from the best final cell.
    best_cell, best_val = (states[0], 0), neg
    for s in states:
        for d in range(d_max + 1):
            if score[s][d] > best_val:
                best_val, best_cell = score[s][d], (s, d)

    path = [best_cell[0]]
    cell = best_cell
    for i in range(len(hyp) - 1, 0, -1):
        cell = back[i].get(cell, (cell[0], max(0, cell[1] - 1)))
        path.append(cell[0])
    path.reverse()
    return path


def runs_to_events(
    path: list[int],
    hyp: list[HypWord],
    *,
    min_run: int = MIN_RUN_WORDS,
) -> list[tuple[float, int]]:
    """Collapse a per-word slide path into (time, slide) cue points.

    Short runs are dropped rather than emitted: a couple of words drifting
    onto a similar slide is decoder noise, not a slide the operator changed to.
    """
    if not path:
        return []

    runs: list[tuple[int, int, int]] = []  # (slide, start_idx, end_idx)
    start = 0
    for i in range(1, len(path) + 1):
        if i == len(path) or path[i] != path[start]:
            runs.append((path[start], start, i))
            start = i

    kept = [r for r in runs if r[2] - r[1] >= min_run]
    if not kept:
        kept = runs[:1]

    # Merge neighbours that survived as the same slide after short runs
    # between them were dropped — that's one continuous slide, not two cues.
    events: list[tuple[float, int]] = []
    for slide, s_idx, _e_idx in kept:
        if events and events[-1][1] == slide:
            continue
        events.append((hyp[s_idx].start, slide))
    return events


def slide_times(
    slides: list[list[str]],
    hyp: list[HypWord],
    duration: float,
) -> tuple[list[float | None], float]:
    """Estimate an onset time for each slide. Returns (times, match_rate).

    A slide's onset is the timestamp of the first of its words that found
    an anchor in the transcript. Slides whose words all went unmatched
    (instrumental passages, ad-libbed repeats) come back None and are
    interpolated by the caller.
    """
    ref: list[str] = []
    slide_of_ref: list[int] = []
    for idx, words in enumerate(slides):
        for w in words:
            ref.append(w)
            slide_of_ref.append(idx)

    pairing = align(ref, hyp)
    matched = sum(1 for p in pairing if p is not None)
    rate = matched / len(ref) if ref else 0.0

    onsets: list[float | None] = [None] * len(slides)
    for ref_i, hyp_i in enumerate(pairing):
        if hyp_i is None:
            continue
        slide = slide_of_ref[ref_i]
        t = hyp[hyp_i].start
        if onsets[slide] is None or t < onsets[slide]:
            onsets[slide] = t

    # Enforce monotonicity — a later slide can never start before an earlier
    # one. Alignment noise occasionally inverts two adjacent onsets.
    last = 0.0
    for i, t in enumerate(onsets):
        if t is None:
            continue
        if t < last:
            onsets[i] = last
        else:
            last = t
    return onsets, rate


def fill_gaps(onsets: list[float | None], duration: float) -> list[float]:
    """Linearly interpolate unmatched slides between their known neighbours."""
    known = [(i, t) for i, t in enumerate(onsets) if t is not None]
    if not known:
        # Nothing anchored — spread evenly and let the human fix it.
        n = len(onsets)
        return [duration * i / max(1, n) for i in range(n)]

    out: list[float] = [0.0] * len(onsets)
    first_i, first_t = known[0]
    last_i, last_t = known[-1]
    for i in range(len(onsets)):
        if onsets[i] is not None:
            out[i] = float(onsets[i])
        elif i < first_i:
            out[i] = first_t * i / max(1, first_i)
        elif i > last_i:
            span = max(0.0, duration - last_t)
            out[i] = last_t + span * (i - last_i) / max(1, len(onsets) - last_i)
        else:
            lo = max(k for k, _ in known if k < i)
            hi = min(k for k, _ in known if k > i)
            lo_t, hi_t = out[lo], float(onsets[hi])
            out[i] = lo_t + (hi_t - lo_t) * (i - lo) / (hi - lo)
    return out


# --------------------------------------------------------------- re-timing
#
# Whisper's word timestamps come from cross-attention and land a sung line
# a median 0.37 s off; the decoder above inherits that. Once it has decided
# which slide the band is on, each cue is re-timed by forcing that slide's
# words onto the sung vocal with a CTC model (`vocal_emissions.py`).
#
# Measured (research/work/aligner_bakeoff, 2026-11-07):
#   JamendoLyrics 20 songs, hand word timings — cue F1 within 0.3 s
#   0.33 -> 0.64, median cue error 0.37 -> 0.08 s.
#   9 hand-approved church recordings — F1 within 1 s 0.44 -> 0.57, 8/9 better.
# Slide choice is unchanged: a cue Whisper put on the wrong slide stays there.

#: How far before the decoded cue to look for the line's first word.
RETIME_LOOKBACK_S = 4.0
#: Words of the previous slide aligned ahead of this one, so the tail of the
#: last line has somewhere to go other than this slide's first word.
RETIME_CONTEXT_WORDS = 4

_EMISSIONS_DEPS = (
    "torch==2.5.1", "torchaudio==2.5.1", "demucs==4.0.1",
    "numpy==2.4.6",
)


@dataclass
class VocalEmissions:
    frames: np.ndarray  # (T, labels) log-probabilities; label 0 is the CTC blank
    labels: list[str]
    fps: float


def vocal_emissions_cached(audio: Path, cache: Path) -> VocalEmissions | None:
    """Emissions for `audio`, computed once in a throwaway uv environment.

    Pass the original file when there is one; it must share the timeline of
    the recording's audio.wav (it does when audio.wav was transcoded from it).

    Returns None (and the caller keeps Whisper's timing) if uv, torch or
    Demucs can't run here — re-timing is an improvement, never a requirement.
    """
    import numpy as np

    if not cache.exists():
        print("  separating vocals + CTC emissions (slow: ~1-2x the audio length)…")
        cmd = ["uv", "run", "--no-project", "--python", "3.11"]
        for dep in _EMISSIONS_DEPS:
            cmd += ["--with", dep]
        cmd += ["python", str(Path(__file__).with_name("vocal_emissions.py")),
                str(audio), str(cache)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            log_warn(f"vocal emissions failed, keeping Whisper timing: {detail.strip()[-300:]}")
            return None
    with np.load(cache, allow_pickle=False) as data:
        return VocalEmissions(
            frames=data["emissions"], labels=[str(x) for x in data["labels"]],
            fps=float(data["fps"]),
        )


def ctc_align(emissions: np.ndarray, tokens: list[int]) -> np.ndarray:
    """Viterbi CTC forced alignment of `tokens` (label 0 = blank).

    Returns each token's (first frame, last frame), shape (len(tokens), 2).
    """
    import numpy as np

    n_frames, n_tok = emissions.shape[0], len(tokens)
    n_states = 2 * n_tok + 1  # blank, tok0, blank, tok1, …, blank
    ext = np.zeros(n_states, dtype=np.int64)
    ext[1::2] = tokens
    # A token may follow the previous token directly (skipping the blank
    # between) unless the two are the same letter.
    skip = np.zeros(n_states, dtype=bool)
    skip[3::2] = ext[3::2] != ext[1:-2:2]
    neg = -1e30
    score = np.full(n_states, neg)
    score[0] = emissions[0, 0]
    if n_states > 1:
        score[1] = emissions[0, ext[1]]
    back = np.zeros((n_frames, n_states), dtype=np.int8)  # 0 stay, 1/2 states back
    for t in range(1, n_frames):
        from_prev = np.concatenate(([neg], score[:-1]))
        from_skip = np.where(skip, np.concatenate(([neg, neg], score[:-2])), neg)
        stacked = np.stack([score, from_prev, from_skip])
        arg = stacked.argmax(0)
        score = stacked[arg, np.arange(n_states)] + emissions[t, ext]
        back[t] = arg
    state = n_states - 1
    if n_states > 1 and score[n_states - 2] > score[n_states - 1]:
        state = n_states - 2
    spans = np.full((n_tok, 2), -1)
    for t in range(n_frames - 1, -1, -1):
        if state % 2 == 1:
            k = state // 2
            spans[k, 0] = t
            if spans[k, 1] < 0:
                spans[k, 1] = t
        state -= int(back[t, state])
    return spans


def word_onsets(
    em: VocalEmissions, words: list[str], t0: float, t1: float
) -> list[float | None]:
    """Force `words` onto the vocal between t0 and t1; each word's onset in
    seconds, or None for a word with no letters the model knows."""
    index = {c: i for i, c in enumerate(em.labels)}
    tokens: list[int] = []
    owner: list[int] = []
    for wi, word in enumerate(words):
        for ch in word.lower():
            i = index.get(ch)
            if i and em.labels[i] not in "|-":
                tokens.append(i)
                owner.append(wi)
    lo, hi = int(t0 * em.fps), int(t1 * em.fps)
    onsets: list[float | None] = [None] * len(words)
    # CTC needs a frame per token plus blanks between repeats; a window too
    # short to hold the words can't be aligned honestly.
    if not tokens or hi - lo < 2 * len(tokens) + 2:
        return onsets
    spans = ctc_align(em.frames[lo:hi], tokens)
    for (first, _last), wi in zip(spans, owner, strict=True):
        if onsets[wi] is None:
            onsets[wi] = t0 + first / em.fps
    return onsets


def retime_cues(
    cues: list[tuple[float, int]],
    slides: list[list[str]],
    em: VocalEmissions,
    duration: float,
) -> list[tuple[float, int]]:
    """Move each decoded cue onto its slide's first sung word.

    Each cue is aligned on its own, in a window from RETIME_LOOKBACK_S before
    it to the next cue, so one bad decision can't drag the rest of the song
    the way a whole-song alignment does. Cues with nothing to align (a blank)
    keep their time. Big moves are kept on purpose: capping them at 1-3 s
    lost F1 on both test sets, because Whisper is sometimes that far off.
    """
    out: list[tuple[float, int]] = []
    for i, (t, slide) in enumerate(cues):
        context = slides[cues[i - 1][1]][-RETIME_CONTEXT_WORDS:] if i else []
        words = list(context) + list(slides[slide])
        t0 = max(0.0, t - RETIME_LOOKBACK_S)
        nxt = cues[i + 1][0] if i + 1 < len(cues) else duration
        t1 = min(duration, max(nxt, t + 2.0) + 1.0)
        onsets = word_onsets(em, words, t0, t1)
        first = next((x for x in onsets[len(context):] if x is not None), None)
        out.append((first if first is not None else t, slide))
    # Re-timing works cue by cue, so keep the list in time order.
    for i in range(1, len(out)):
        if out[i][0] < out[i - 1][0]:
            out[i] = (out[i - 1][0], out[i][1])
    return out


# ------------------------------------------------------------------- write-out


def anticipate(t: float, anticipate_ms: float) -> float:
    """Pull a transition earlier, the way a trained operator cues a slide.

    A good slide operator doesn't click on the downbeat of the new line — they
    click just before it, so the congregation is already reading when the band
    arrives. Word-onset alignment gives us the downbeat, so we shift the whole
    timeline earlier by a fixed lead. Clamped at 0: a cue can't precede the
    recording.
    """
    return round(max(0.0, t - anticipate_ms / 1000.0), 3)


def plan_events(
    slides: list[list[str]],
    hyp: list[HypWord],
    duration: float,
    *,
    engine: str,
    anticipate_ms: float,
    item_of: list[int] | None = None,
    local_of: list[int] | None = None,
    blank_indices: set[int] | None = None,
    emissions: VocalEmissions | None = None,
) -> tuple[list[GroundTruthEvent], str]:
    """Decode a cue list. Returns the events plus a one-line quality note.

    `viterbi` is the default and the only engine that can emit a slide more
    than once; `linear` is kept as the measured baseline it beat (mean F1
    0.75 -> 0.91 against two hand-annotated recordings).

    `slides` may be several songs concatenated end to end (`flatten_songs`);
    `item_of`/`local_of` map each flat index back to `(item_idx, slide_idx)`
    for the events this writes. Default to a single song at item 0, which is
    every existing caller.

    With `emissions`, Viterbi cues are re-timed onto the sung vocal
    (`retime_cues`); the linear engine ignores them.
    """
    blanks = blank_indices if blank_indices is not None else {0}
    item_of = item_of if item_of is not None else [0] * len(slides)
    local_of = local_of if local_of is not None else list(range(len(slides)))

    if engine == "linear":
        onsets, rate = slide_times(slides, hyp, duration)
        times = fill_gaps(onsets, duration)
        anchored = sum(
            1 for i, t in enumerate(onsets) if i not in blanks and t is not None
        )
        events = [
            GroundTruthEvent(
                t_seconds=anticipate(times[i], anticipate_ms),
                item_idx=item_of[i],
                slide_idx=local_of[i],
            )
            for i in range(len(slides))
            if i not in blanks
        ]
        return events, f"word match rate={rate:.0%}, {anchored} anchored"

    path = decode_slides(hyp, slides, blank_indices=blanks)
    cues = runs_to_events(path, hyp)
    if emissions is not None:
        cues = retime_cues(cues, slides, emissions, duration)
    events = [
        GroundTruthEvent(
            t_seconds=anticipate(t, anticipate_ms),
            item_idx=item_of[s],
            slide_idx=local_of[s],
        )
        for t, s in cues
    ]
    repeats = len(events) - len({(e.item_idx, e.slide_idx) for e in events})
    timing = ", re-timed on vocals" if emissions is not None else ""
    return events, f"{len(events)} cues, {repeats} repeat(s){timing}"


def new_recording_id(when: float) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(when))
    return f"rec_{ts}_{int((when % 1) * 1000):03d}"


def bootstrap(
    audio: Path,
    titles: str | list[str],
    *,
    db_path: Path,
    recordings_dir: Path,
    model_size: str,
    when: float,
    anticipate_ms: float = 0.0,
    engine: str = "viterbi",
    retime: bool = True,
) -> dict:
    """`titles` is one song, or several in setlist order for a recording that
    moves between songs — see `flatten_songs`. The Viterbi decoder finds the
    *actual* sung order on its own; `titles` only has to name which songs are
    in play, not the order they end up sung in.
    """
    title_list = [titles] if isinstance(titles, str) else list(titles)
    songs = [find_song(db_path, t) for t in title_list]
    flat_slides, flat_sections, item_of, local_of, blanks = flatten_songs(songs)
    if len(flat_slides) <= len(blanks):
        names = ", ".join(repr(s.title) for s in songs)
        raise SystemExit(f"{names} parsed to no slides — check their body text")

    rec_id = new_recording_id(when)
    rec_dir = recordings_dir / rec_id
    wav_path = rec_dir / "audio.wav"

    print(f"  transcoding → {wav_path.name}")
    duration = transcode(audio, wav_path)

    print(f"  transcribing with {model_size} ({duration:.0f}s of audio)…")
    hyp = transcribe_cached(wav_path, model_size, rec_dir / "transcript.json")
    print(f"  {len(hyp)} words transcribed")

    emissions = (
        vocal_emissions_cached(audio, rec_dir / "vocal_emissions.npz")
        if retime and engine == "viterbi" else None
    )
    events, quality = plan_events(
        flat_slides, hyp, duration, engine=engine, anticipate_ms=anticipate_ms,
        item_of=item_of, local_of=local_of, blank_indices=blanks,
        emissions=emissions,
    )

    recording = Recording(
        id=rec_id,
        setlist_id="bootstrap_" + "_".join(s.id for s in songs),
        started_at_ms=int(when * 1000),
        duration_seconds=duration,
        ground_truth=events,
        audio_path=str(wav_path),
        notes=(
            f"Bootstrapped from {audio.name!r} by research/eval/bootstrap_golden.py "
            f"(asr={model_size}, engine={engine}, {quality}, "
            f"anticipation={anticipate_ms:.0f}ms). "
            "Ground truth is machine-estimated — verify in the Golden Recording Editor."
        ),
        setlist_content=[
            SetlistItemSnapshot(
                item_idx=i,
                song_id=song.id,
                title=song.title,
                slides=[
                    flat_slides[j] for j in range(len(flat_slides)) if item_of[j] == i
                ],
                slide_sections=[
                    flat_sections[j]
                    for j in range(len(flat_slides))
                    if item_of[j] == i
                ],
            )
            for i, song in enumerate(songs)
        ],
    )
    rec_dir.mkdir(parents=True, exist_ok=True)
    (rec_dir / "recording.json").write_text(recording.model_dump_json(indent=2))

    golden = GoldenAnnotation(
        recording_id=rec_id,
        ground_truth=events,
        notes="Machine-estimated starting point — needs human review.",
        edited_at_ms=int(when * 1000),
    )
    (rec_dir / "golden.json").write_text(golden.model_dump_json(indent=2))

    return {
        "id": rec_id,
        "title": " / ".join(s.title for s in songs),
        "slides": len(flat_slides) - len(blanks),
        "events": len(events),
        "quality": quality,
        "duration": duration,
        "dir": str(rec_dir),
    }


def reannotate(
    rec_dir: Path,
    *,
    model_size: str,
    anticipate_ms: float,
    engine: str,
    force: bool = False,
    retime: bool = True,
) -> dict | None:
    """Re-decode an existing recording's cues in place.

    Reuses the recording's own audio and setlist snapshot, so it never needs
    the source file or the library again. Returns None when the recording was
    skipped.

    **An approved golden annotation is never touched.** That file is the one
    artefact here a human made by hand, and it is worth more than anything
    this script can generate — `--force` exists but should stay unused.
    """
    meta_path = rec_dir / "recording.json"
    if not meta_path.exists() or not (rec_dir / "audio.wav").exists():
        return None
    recording = Recording.model_validate_json(meta_path.read_text())

    golden_file = rec_dir / "golden.json"
    existing = None
    if golden_file.exists():
        try:
            existing = GoldenAnnotation.model_validate_json(golden_file.read_text())
        except Exception as exc:
            log_warn(f"unreadable golden in {rec_dir.name}: {exc}")
    if existing and existing.approved and not force:
        print(f"  ↷ {rec_dir.name}: approved by hand — left alone")
        return None

    # Rebuild the flat (slides, item_of, local_of, blanks) `flatten_songs`
    # would produce, straight from the stored snapshot — no library lookup
    # needed, so a re-annotation survives the library changing under it.
    flat_slides: list[list[str]] = []
    item_of: list[int] = []
    local_of: list[int] = []
    blanks: set[int] = set()
    for item in recording.setlist_content:
        blanks.add(len(flat_slides))
        for local_idx, words in enumerate(item.slides):
            flat_slides.append(list(words))
            item_of.append(item.item_idx)
            local_of.append(local_idx)

    hyp = transcribe_cached(
        rec_dir / "audio.wav", model_size, rec_dir / "transcript.json"
    )
    emissions = (
        vocal_emissions_cached(rec_dir / "audio.wav", rec_dir / "vocal_emissions.npz")
        if retime and engine == "viterbi" else None
    )
    events, quality = plan_events(
        flat_slides,
        hyp,
        recording.duration_seconds,
        engine=engine,
        anticipate_ms=anticipate_ms,
        item_of=item_of,
        local_of=local_of,
        blank_indices=blanks,
        emissions=emissions,
    )

    before = len(recording.ground_truth)
    recording.ground_truth = events
    recording.notes = re.sub(
        # to the last "anticipation=…ms)": the quality text holds "repeat(s)"
        r"\(asr=.*anticipation=\d+ms\)",
        f"(asr={model_size}, engine={engine}, {quality}, "
        f"anticipation={anticipate_ms:.0f}ms)",
        recording.notes,
        count=1,
    )
    meta_path.write_text(recording.model_dump_json(indent=2))

    golden_file.write_text(
        GoldenAnnotation(
            recording_id=recording.id,
            ground_truth=events,
            notes="Machine-estimated starting point — needs human review.",
            edited_at_ms=int(time.time() * 1000),
            approved=False,
        ).model_dump_json(indent=2)
    )
    print(f"  ✓ {rec_dir.name}: {before} → {len(events)} cues ({quality})")
    return {"id": recording.id, "events": len(events), "before": before}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio", help="Audio file (any format ffmpeg reads).")
    ap.add_argument("--title", help="Song title as it appears in the library.")
    ap.add_argument(
        "--titles",
        help="Comma-separated song titles, for a recording that moves "
        "between songs — the leader singing them out of this order, or "
        "into a fragment of one before the other, is exactly what the "
        "Viterbi decoder is for. Overrides --title.",
    )
    ap.add_argument(
        "--manifest",
        help="JSON list of {audio, title} or {audio, titles: [...]} objects "
        "— batch mode alternative to --audio/--title(s).",
    )
    ap.add_argument(
        "--reannotate",
        action="store_true",
        help="Re-decode cues for the recordings already in --recordings, in "
        "place, reusing their audio + cached transcripts. Skips any recording "
        "whose golden.json is approved.",
    )
    ap.add_argument(
        "--engine",
        choices=("viterbi", "linear"),
        default="viterbi",
        help="viterbi (default) can emit a slide more than once, so it handles "
        "repeated choruses; linear is the older single-pass baseline.",
    )
    ap.add_argument(
        "--no-retime",
        dest="retime",
        action="store_false",
        help="Keep Whisper's word timing. By default each Viterbi cue is "
        "re-timed onto the separated vocal with a CTC aligner, which needs uv "
        "and runs torch + Demucs in a throwaway environment (slow: about the "
        "audio's length again, cached as vocal_emissions.npz).",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Overwrite even hand-approved annotations. Don't.",
    )
    ap.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    ap.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    ap.add_argument(
        "--anticipate-ms",
        type=float,
        default=750.0,
        help="Shift every transition this many ms earlier, mimicking an "
        "operator who cues the slide just before the line lands. 0 = align "
        "exactly on the first sung word.",
    )
    ap.add_argument(
        "--model",
        default="small.en",
        help="faster-whisper model (tiny.en/base.en/small.en/medium.en). "
        "Bigger reads sung lyrics better; small.en is the sweet spot.",
    )
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH")
    if not args.library.exists():
        raise SystemExit(f"library not found: {args.library}")

    if args.reannotate:
        dirs = sorted(d for d in args.recordings.iterdir() if d.is_dir())
        done = []
        for n, d in enumerate(dirs, 1):
            print(f"[{n}/{len(dirs)}] {d.name}")
            res = reannotate(
                d,
                model_size=args.model,
                anticipate_ms=args.anticipate_ms,
                engine=args.engine,
                force=args.force,
                retime=args.retime,
            )
            if res:
                done.append(res)
        print(f"\n{len(done)} recording(s) re-annotated, "
              f"{len(dirs) - len(done)} left alone")
        return 0

    if args.manifest:
        jobs = json.loads(Path(args.manifest).expanduser().read_text())
    elif args.audio and args.titles:
        jobs = [{"audio": args.audio, "titles": args.titles.split(",")}]
    elif args.audio and args.title:
        jobs = [{"audio": args.audio, "title": args.title}]
    else:
        raise SystemExit("need --audio and --title/--titles, or --manifest")

    results = []
    for n, job in enumerate(jobs, 1):
        audio = Path(job["audio"]).expanduser()
        titles = (
            [t.strip() for t in job["titles"]] if "titles" in job else job["title"]
        )
        label = titles if isinstance(titles, str) else " / ".join(titles)
        print(f"[{n}/{len(jobs)}] {label} ← {audio.name}")
        if not audio.exists():
            print(f"  !! missing audio, skipping: {audio}")
            continue
        try:
            res = bootstrap(
                audio,
                titles,
                db_path=args.library,
                recordings_dir=args.recordings,
                model_size=args.model,
                when=time.time(),
                anticipate_ms=args.anticipate_ms,
                engine=args.engine,
                retime=args.retime,
            )
        except SystemExit as exc:
            print(f"  !! {exc}")
            continue
        results.append(res)
        print(f"  ✓ {res['id']}: {res['slides']} slides — {res['quality']}")

    if results:
        print(f"\n{len(results)} recording(s) written to {args.recordings}")
        print("Open Settings → Recording in the app and hit the pencil to fine-tune.")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
