"""`QwenOnnxBackend`'s streaming, with the ONNX model faked out.

What the processor backend adds around the shared `QwenStreamer` loop: 2 s
encoder blocks whose audio cache is kept, so each step prefills only the new
audio; the text prefix rolled back each step; the window committed at a
pause when it overflows; and word times read from the decoder's attention.
Accuracy and speed are measured in research (docs/research/QWEN3_ASR_CPU.md).
"""

from __future__ import annotations

import numpy as np

from auto_presenter.asr import qwen_onnx_backend as q
from auto_presenter.audio.types import SAMPLE_RATE

WORDS = ["Holy", "holy", "holy", "Lord", "God", "Almighty"]  # public-domain hymn
HEAD = [1, 2, 3]
TAIL = [4, 5]
EOS = q.EOS_IDS[0]


class FakeTokenizer:
    def head(self, context: str) -> list[int]:
        return list(HEAD)

    def tail(self) -> list[int]:
        return list(TAIL)

    def decode(self, ids: list[int]) -> str:
        return "".join(f" {WORDS[i - 100]}" if i >= 100 else "" for i in ids)


class FakeModel:
    """13 audio tokens a second. Generates WORDS (as ids 100..) one per
    step beyond what the prefix already forced, then stops; each token's
    attention peaks on the audio token of its own index."""

    def __init__(self, per_step: int = 2) -> None:
        self.tokenizer = FakeTokenizer()
        self.per_step = per_step
        self.extends: list[tuple[int, int, int]] = []  # (past, new, start)
        self._stop_after = 0

    def encode(self, mel: np.ndarray) -> np.ndarray:
        return np.zeros((max(1, mel.shape[1] * 13 // 100), q.HIDDEN), np.float32)

    def embeds(self, ids: list[int]) -> np.ndarray:
        e = np.zeros((len(ids), q.HIDDEN), np.float32)
        e[:, 0] = ids  # carry the id, so extend can read it back
        return e

    def empty_cache(self):
        z = np.zeros((1, 1, 1, 0, 1), np.float32)
        return z, z.copy()

    def extend(self, embeds, start, keys, values):
        s, p = embeds.shape[0], keys.shape[3]
        self.extends.append((p, s, start))
        ids = [int(x) for x in embeds[:, 0]]
        text = [i for i in ids if i >= 100]
        if s > 1:  # a prefill: generate past the forced prefix
            self._stop_after = min(len(WORDS), len(text) + self.per_step)
            nxt = 100 + len(text)
        else:
            nxt = ids[0] + 1
        logits = np.zeros(q.VOCAB, np.float32)
        logits[nxt if nxt - 100 < self._stop_after else EOS] = 10.0
        k = np.zeros((1, 1, 1, p + s, 1), np.float32)
        attn = np.zeros((8, s, p + s), np.float32)
        for r, i in enumerate(ids):
            col = len(HEAD) + 13 * max(0, i - 100) if i >= 100 else 0
            attn[:, r, min(col, p + s - 1)] = 1.0
        return logits, k, k.copy(), attn


def backend(per_step: int = 2) -> tuple[q.QwenOnnxBackend, FakeModel]:
    b = q.QwenOnnxBackend()
    m = FakeModel(per_step)
    b._onnx = m  # type: ignore[assignment]
    return b, m


def tone(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_each_step_prefills_only_the_new_audio() -> None:
    b, m = backend()
    st = b.new_state()
    for _ in range(4):
        b.feed(st, tone(2.0))
    prefills = [e for e in m.extends if e[1] > 1][1:]  # after the head's own
    # every step's 2 s block is cached the step it arrives
    assert [p for p, _, _ in prefills] == [len(HEAD) + i * 26 for i in range(4)]
    # so each step prefills one block of audio, never the whole window
    assert all(s <= 26 + len(TAIL) + len(WORDS) for _, s, _ in prefills)
    assert len(st._prefix_cache.blocks) == 4


def test_the_head_is_prefilled_once_per_context() -> None:
    b, m = backend()
    st = b.new_state()
    for _ in range(3):
        b.feed(st, tone(2.0))
    assert sum(1 for p, s, _ in m.extends if p == 0) == 1


def test_text_is_forced_back_in_minus_the_unfixed_tokens() -> None:
    b, _ = backend(per_step=3)
    b.unfixed_tokens = 1
    st = b.new_state()
    texts = []
    for _ in range(3):
        b.feed(st, tone(2.0))
        texts.append(st.window_text)
    # two unfixed chunks decode from scratch; the third keeps all but one token
    assert texts[0] == "Holy holy holy"
    assert texts[2] == "Holy holy holy Lord God"


def test_an_overflowing_window_is_committed() -> None:
    b, _ = backend()
    st = b.new_state()
    for _ in range(6):  # 12 s into a 10 s window
        b.feed(st, tone(2.0))
    assert st.committed_text
    assert len(st.audio_accum) <= b.max_context_seconds * SAMPLE_RATE


def test_word_times_follow_the_attention() -> None:
    b, _ = backend(per_step=3)
    st = b.new_state()
    b.feed(st, tone(2.0))
    b.feed(st, tone(2.0))
    words = b.align(st.audio_accum, st.window_text)
    assert [w for w, _, _ in words] == ["Holy", "holy", "holy"]
    starts = [s for _, s, _ in words]
    assert starts == sorted(starts) and starts[1] - starts[0] > 0.5


def test_repetition_stops_decoding() -> None:
    assert q._repeating([7] * 20)
    assert q._repeating([1, 2] * 10)
    assert not q._repeating(list(range(30)))


def test_commit_cut_lands_in_a_pause() -> None:
    audio = np.concatenate([tone(8.5), np.zeros(int(0.5 * SAMPLE_RATE), np.float32), tone(1.0)])
    cut = q._commit_cut(audio)
    assert 8.5 * SAMPLE_RATE < cut < 9.0 * SAMPLE_RATE
    assert q._commit_cut(tone(10.0)) == 10 * SAMPLE_RATE


def test_log_mel_shape_and_range() -> None:
    mel = q.log_mel(tone(1.0))
    assert mel.shape == (128, 100)
    assert mel.max() - mel.min() <= 2.0 + 1e-6  # clamped to 8 decades, /4
