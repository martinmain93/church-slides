"""The look-back over a stored recording, for the Model inspector.

Runs exactly what a live session's learning observer runs
(`session.LearningSession`: words filed under the slide shown when they were
sung, the look-back, the label cleaning, the weight), over a recording's
audio-clock words and the slides it showed. Then it lays the result against
the golden, so a human can see where the look-back is right, unsure or
wrong, and why.

Where the words come from (`source`):

* ``live`` — what the live session's matcher heard (`live_transcript.json`,
  written by recordings made since 2026-10-08): exactly the look-back's input.
* ``asr`` — Whisper over the audio the way a live session runs it (the
  streaming window, MLX), cached beside the recording after the first run.
* ``cached`` — the whole-file Whisper pass `bootstrap_golden.py` caches as
  `transcript.json`: more accurate than live, so optimistic.
* ``golden`` — a perfect transcript made from the golden: the look-back's
  ceiling, not evidence about it.
* ``auto`` — live, else cached, else asr.
"""

from __future__ import annotations

import json
import logging
import time
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from auto_presenter.asr.types import Word
from auto_presenter.learning import labels
from auto_presenter.learning.lookback import align
from auto_presenter.learning.session import LearningSession, identical_canon
from auto_presenter.protocol import SetlistContent, SetlistItemContent
from auto_presenter.recording.sink import LIVE_TRANSCRIPT

log = logging.getLogger(__name__)

#: The streamed Whisper pass, cached beside the recording.
ASR_CACHE = "lookback_asr.json"
#: The whole-file pass `research/eval/bootstrap_golden.py` caches.
BOOTSTRAP_CACHE = "transcript.json"
#: A pass agrees with the golden if the golden showed its slide at any point
#: from this long before its first word to its last: a reference that runs
#: late (a recording with no approved golden is judged against what was
#: shown live) should read as late, not as wrong.
GOLDEN_LEAD_S = 1.0


def available_sources(recording_dir: Path) -> list[str]:
    out = []
    if (recording_dir / LIVE_TRANSCRIPT).exists():
        out.append("live")
    if (recording_dir / ASR_CACHE).exists():
        out.append("asr")
    if (recording_dir / BOOTSTRAP_CACHE).exists():
        out.append("cached")
    return out


def _read_words(path: Path) -> list[Word]:
    data = json.loads(path.read_text())
    out = []
    for w in data.get("words") or []:
        text = w.get("text") or w.get("word") or ""
        start = w.get("t_start", w.get("start"))
        end = w.get("t_end", w.get("end", start))
        if text and start is not None:
            out.append(Word(text=text.strip(), t_start=float(start), t_end=float(end)))
    return out


def _asr_words(recording, recording_dir: Path) -> list[Word]:
    """Whisper the way a live session runs it, cached after the first time."""
    cache = recording_dir / ASR_CACHE
    if cache.exists():
        return _read_words(cache)
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend
    from auto_presenter.asr.streaming import SlidingWindowWrapper
    from auto_presenter.eval.replay import Replayer

    if not MLXWhisperBackend.is_available():
        raise RuntimeError("streamed Whisper needs the MLX backend on this machine")
    model = (getattr(recording, "config", None) and recording.config.asr_model) or "small.en"
    backend = MLXWhisperBackend(model)
    wrapper = SlidingWindowWrapper(backend)
    words: list[Word] = []
    for ev in wrapper.transcribe(Replayer(recording, realtime=False).audio_frames()):
        if ev.is_final:
            words.extend(w for w in ev.words if w.t_start is not None)
    cache.write_text(
        json.dumps(
            {
                "source": "asr",
                "model": model,
                "words": [{"text": w.text, "t_start": w.t_start, "t_end": w.t_end} for w in words],
            }
        )
    )
    return words


def _golden_words(recording, golden) -> list[Word]:
    from auto_presenter.eval.trace import synthesize_schedule

    perfect = recording.model_copy(update={"ground_truth": list(golden)})
    return [w for _t, ev in synthesize_schedule(perfect) for w in ev.words]


def words_for(recording, recording_dir: Path, golden, source: str) -> tuple[list[Word], str]:
    if source == "auto":
        have = available_sources(recording_dir)
        source = next((s for s in ("live", "cached", "asr") if s in have), "asr")
    if source == "live":
        return _read_words(recording_dir / LIVE_TRANSCRIPT), source
    if source == "cached":
        return _read_words(recording_dir / BOOTSTRAP_CACHE), source
    if source == "golden":
        return _golden_words(recording, golden), source
    return _asr_words(recording, recording_dir), "asr"


def inspect_lookback(recording, golden, words: list[Word]) -> list[dict]:
    """Per setlist item: the look-back's passes and per-word placements, what
    it would store, and how it compares with the golden."""
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=s.item_idx,
                song_id=s.song_id or f"item{s.item_idx}",
                slides=list(s.slides),
                title=getattr(s, "title", "") or "",
            )
            for s in recording.setlist_content
        ]
    )
    session = LearningSession(content, None)
    shown = sorted(recording.ground_truth, key=lambda g: g.t_seconds)
    for g in shown:
        session.on_slide_shown(g.item_idx, g.slide_idx, g.source or "manual", t=g.t_seconds)
    session.on_words(words)
    gold = sorted(golden, key=lambda g: g.t_seconds)

    out = []
    for item in content.items:
        perf = session._perfs.get(item.item_idx)
        if perf is None or not perf.words:
            continue
        started = time.perf_counter()
        al = align(perf.model, list(perf.words))
        ms = (time.perf_counter() - started) * 1000
        canon = identical_canon(perf.slides)
        moves = labels.clean_moves(perf.moves)
        agree = labels.agreement(al.passes, moves, canon)
        n = sum(p.n_words for p in al.passes)
        mean_conf = sum(p.confidence * p.n_words for p in al.passes) / n if n else 0.0
        led = labels.led_by(moves)

        def golden_during(a: float, b: float, item_idx=item.item_idx) -> list[int]:
            """Slides the golden showed between a and b, longest first."""
            cur = None
            shown: dict[int, float] = {}
            for g in [*gold, None]:
                end = b if g is None else min(b, g.t_seconds)
                if cur is not None and cur.item_idx == item_idx and end > a:
                    start = max(a, cur.t_seconds)
                    if end > start or not shown:
                        shown[cur.slide_idx] = shown.get(cur.slide_idx, 0.0) + max(0.0, end - start)
                if g is None or g.t_seconds >= b:
                    break
                cur = g
            return sorted(shown, key=lambda s: -shown[s])

        passes = []
        for p in al.passes:
            during = golden_during(p.t_start - GOLDEN_LEAD_S, max(p.t_end, p.t_start + 0.5))
            gs = during[0] if during else None
            passes.append(
                {
                    "slide": p.slide,
                    "t_start": round(p.t_start, 2),
                    "t_end": round(p.t_end, 2),
                    "confidence": round(p.confidence, 3),
                    "n_words": p.n_words,
                    "golden_slide": gs,
                    "agrees": None if gs is None else canon(p.slide) in {canon(x) for x in during},
                }
            )
        gold_seq: list[int] = []
        gold_t: list[float] = []
        for g in gold:
            if g.item_idx != item.item_idx or g.slide_idx >= len(perf.slides):
                continue
            if perf.slides[g.slide_idx]:
                c = canon(g.slide_idx)
                if not gold_seq or gold_seq[-1] != c:
                    gold_seq.append(c)
                    gold_t.append(g.t_seconds)
        conf = [p for p in al.passes if p.confidence >= labels.CONFIDENT]
        lb_seq: list[int] = []
        lb_t: list[float] = []
        for p in conf:
            c = canon(p.slide)
            if not lb_seq or lb_seq[-1] != c:
                lb_seq.append(c)
                lb_t.append(p.t_start)
        sm = SequenceMatcher(None, lb_seq, gold_seq, autojunk=False)
        matched = [(bl.a + q, bl.b + q) for bl in sm.get_matching_blocks() for q in range(bl.size)]
        offsets = [lb_t[a] - gold_t[b] for a, b in matched]
        cols = {si: i for i, si in enumerate(al.slides)}
        word_rows = []
        for k, w in enumerate(al.words):
            row = al.posterior[k]
            top = np.argsort(row)[::-1][:3]
            si = al.word_slide[k]
            word_rows.append(
                {
                    "t": round(w.t, 2),
                    "text": w.text,
                    "slide": si,
                    "pos": al.word_pos[k],
                    "p": round(float(row[cols[si]]), 3),
                    "top": [
                        {"slide": al.slides[int(c)], "p": round(float(row[int(c)]), 3)} for c in top
                    ],
                }
            )
        out.append(
            {
                "item_idx": item.item_idx,
                "title": item.title,
                "heard_words": len(perf.words),
                "lookback_ms": round(ms, 1),
                "passes": passes,
                "words": word_rows,
                "led_by": led,
                "weight": labels.performance_weight("live", led, agree, mean_conf),
                "agreement": None if agree is None else round(agree, 3),
                "mean_confidence": round(float(mean_conf), 3),
                "lookback_sequence": lb_seq,
                "golden_sequence": gold_seq,
                "precision": round(len(matched) / len(lb_seq), 3) if lb_seq else None,
                "recall": round(len(matched) / len(gold_seq), 3) if gold_seq else None,
                "median_offset_s": round(float(np.median(offsets)), 2) if offsets else None,
            }
        )
    return out
