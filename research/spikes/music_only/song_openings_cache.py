"""Cache teacher-forced span scores of EVERY span of EVERY song we have, on every recording.

For the "listen for the other songs' opening lines" spike (song_openings.py):
each 6 s window ending at t (every STEP s) is scored by the production
`MLXSpanScorer` (whisper small.en, truncated encoder, shared cross-attn K/V)
against the union of all distinct 6-word spans of all 11 songs in the
recordings below — rules can then restrict to entry spans, own-song spans or
decoys without rescoring. Also scores each span on silence and on pink noise
once (the language-model prior, for a PMI-style normalisation).

    research/spikes/music_only/py.sh research/spikes/music_only/song_openings_cache.py [KEY ...]

Writes build/openings/<key>.npz (t, scores[float16, T x S], ms per window)
and build/openings/spans.json (span table: text, song, slide, word index).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr  # noqa: E402
from auto_presenter.asr.mlx_backend import local_snapshot, repo_for  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.follower.scorer import WINDOW_S, MLXSpanScorer, spans_for  # noqa: E402
from auto_presenter.music.inspect import load_wav  # noqa: E402

STEP = {"set": 2.0, "medley": 2.0}  # single-song (decoy) recordings: every 4 s
ENTRY_WORDS = 12  # decoy spans: those ending in the first N words of a slide
OUT = Path(__file__).resolve().parents[3] / "build" / "openings"  # this worktree; the shared dir is read-only
KEYS = ["set", "medley", "bml", "since", "wabn", "rl", "komh", "isa"]


def song_table():
    """song_id -> (title, slides); every song in every case's setlist."""
    songs: dict[str, tuple[str, list[list[str]]]] = {}
    for key in KEYS:
        rec = load_recording(mr.CASES[key][0])
        for x in rec.setlist_content:
            title = x.title or {"set": "", "bml": ""}.get(key, key)
            songs.setdefault(x.song_id, (title or key, [list(s) for s in x.slides]))
    return songs


def span_table():
    """Distinct (song, span text) rows, with every (slide, word index) the span ends at."""
    rows: dict[tuple[str, str], list] = {}
    for sid, (_title, slides) in song_table().items():
        for (si, j), text in spans_for(slides).items():
            rows.setdefault((sid, text), []).append((si, j))
    return [{"song": s, "text": t, "at": at} for (s, t), at in rows.items()]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    table = span_table()
    (OUT / "spans.json").write_text(json.dumps({"songs": {k: v[0] for k, v in song_table().items()},
                                                "spans": table}))
    texts = sorted({r["text"] for r in table})  # score each distinct text once
    print(f"{len(table)} (song, span) rows, {len(texts)} distinct texts")
    scorer = MLXSpanScorer(local_snapshot(repo_for("small.en")))
    scorer._load()
    scorer._place = lambda *a, **k: 0.0  # placement isn't needed here
    base = OUT / "baseline.npz"
    if not base.exists():
        n = int(WINDOW_S * 16000)
        rng = np.random.default_rng(0)
        white = rng.standard_normal(n + 1)
        pink = np.cumsum(white)[1:] - np.cumsum(white)[:-1] * 0.98  # crude 1/f-ish tilt
        pink = (pink / np.abs(pink).max() * 0.05).astype(np.float32)
        sil = scorer.score(np.zeros(n, np.float32), texts).scores
        noi = scorer.score(pink, texts).scores
        np.savez(base, texts=np.array(texts), silence=sil, noise=noi)
    for key in sys.argv[1:] or KEYS:
        path = OUT / f"{key}.npz"
        if path.exists():
            continue
        audio = load_wav(mr.CASES[key][0] / "audio.wav")
        dur = len(audio) / 16000
        ts = np.arange(WINDOW_S, dur + 1e-6, STEP.get(key, 4.0))
        # the GPU is shared: score the recording's own songs in full and every
        # other song's entry spans only (unscored columns stay NaN). The set
        # was cached first with every span of every song.
        own = {x.song_id for x in load_recording(mr.CASES[key][0]).setlist_content}
        want = sorted({r["text"] for r in table
                       if r["song"] in own or any(j < ENTRY_WORDS for _si, j in r["at"])})
        wi = np.array([texts.index(w) for w in want])
        mat = np.full((len(ts), len(texts)), np.nan, np.float16)
        ms = np.zeros(len(ts), np.float32)
        t0 = time.time()
        for i, t in enumerate(ts):
            a = audio[int((t - WINDOW_S) * 16000): int(t * 16000)]
            r = scorer.score(a, want)
            mat[i, wi] = r.scores
            ms[i] = r.timings["score"] * 1000
            if i % 50 == 0:
                print(f"  {key} t={t:.0f}/{dur:.0f}  {ms[i]:.0f} ms/window  ({time.time() - t0:.0f}s)", flush=True)
        np.savez(path, t=ts, scores=mat, ms=ms, texts=np.array(texts))
        print(f"{key}: {len(ts)} windows, median {np.median(ms):.0f} ms for {len(want)} spans")
    return 0


if __name__ == "__main__":
    sys.exit(main())
