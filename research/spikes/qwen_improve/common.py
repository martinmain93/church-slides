"""Shared pieces for the Qwen3-ASR improvement spike.

See docs/research/QWEN3_ASR_IMPROVEMENTS.md. Everything here runs in the
Qwen research venv (`mlx-qwen3-asr[aligner]`), never the app's:

    V=~/Development/auto-presenter-research/work/qwen3asr/.venv/bin/python
    cd sidecar && PYTHONPATH=. $V ../research/spikes/qwen_improve/<script>.py ...

Audio and lyrics are public (JamendoLyrics, CC) and read from the dataset at
run time; nothing lyric-bearing is written into the repo. Output goes to
WORK (outside the repo).
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "streaming_asr"))
from stream_eval import JAMALT, RESEARCH, SR, load_clip, norm  # noqa: E402,F401

WORK = RESEARCH / "work" / "qwen_improve"
MODELS = {"0.6b": "Qwen/Qwen3-ASR-0.6B", "1.7b": "Qwen/Qwen3-ASR-1.7B"}
# The parent spike's six songs (runs/main6), so numbers line up.
MAIN6 = [
    "Avercage_-_Embers",
    "Color_Out_-_Falling_Star",
    "HILA_-_Give_Me_the_Same",
    "Kinematic_-_Peyote",
    "Quentin_Hannappe_-_Keep_On",
    "Songwriterz_-_Back_In_Time",
]


def all_songs() -> list[str]:
    return [ln.strip() for ln in (JAMALT / "en.txt").read_text().splitlines() if ln.strip()]


def audio_of(clip) -> np.ndarray:
    return np.frombuffer(clip.pcm16, dtype=np.int16).astype(np.float32) / 32768.0


@dataclass
class Line:
    words: list[str]  # raw tokens as written (for prompting the model)
    norms: list[str]  # normalised, same length (empty string for punctuation-only)
    starts: list[float]
    ends: list[float]

    @property
    def start(self) -> float:
        return self.starts[0]

    @property
    def end(self) -> float:
        return self.ends[-1]

    @property
    def text(self) -> str:
        return " ".join(self.words)

    def opening(self, k: int) -> str:
        return " ".join(self.words[:k])

    def key(self, k: int = 3) -> tuple[str, ...]:
        return tuple(n for n in self.norms if n)[:k]


def song_lines(name: str) -> list[Line]:
    """The song's lyric lines with hand-made word timings (JamendoLyrics)."""
    text = (JAMALT / "lyrics" / f"{name}.txt").read_text()
    with open(JAMALT / "words" / f"{name}.csv") as f:
        rows = list(csv.DictReader(f))
    out: list[Line] = []
    k = 0
    for raw in text.splitlines():
        toks = raw.split()
        if not toks:
            continue
        rs = rows[k : k + len(toks)]
        k += len(toks)
        out.append(Line(toks, [norm(t) for t in toks],
                        [float(r["word_start"]) for r in rs], [float(r["word_end"]) for r in rs]))
    if k != len(rows):
        raise ValueError(f"{name}: {k} tokens vs {len(rows)} timings")
    return out


def load_qwen(size: str, bits: int = 16, encoder_bits: int | None = None):
    """Qwen3-ASR model, optionally quantized in memory (group size 64)."""
    from mlx_qwen3_asr.convert import quantize_model
    from mlx_qwen3_asr.load_models import load_model

    model, _ = load_model(MODELS[size])
    if bits < 16:
        quantize_model(model, bits=bits, group_size=64, encoder_bits=encoder_bits)
    return model


def tokenizer_for(size: str):
    from mlx_qwen3_asr.load_models import _ModelHolder
    from mlx_qwen3_asr.tokenizer import _TokenizerHolder
    import mlx.core as mx

    return _TokenizerHolder.get(_ModelHolder.get_resolved_path(MODELS[size], dtype=mx.float16))


def pct(xs, q: float) -> float:
    if len(xs) == 0:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]
