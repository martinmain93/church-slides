"""The next-line check: hear the band start a new line about a second after
its first word, instead of the 3 s or more Whisper's words take to arrive.

Every 0.25 s of audio the last 2.5 s go through Whisper's encoder (the
small.en model, on its own MLX stream) and a small phone model trained on
public music (`PhoneHead`: a 2-layer GRU over the encoder's output, giving
each 20 ms frame a probability for each of 40 English phones and CTC's
blank). `NextLineCheck` reads those phones against the song's lines:

* **Which line is being sung.** Start from the slide on screen. A line-finder
  scores every distinct line of the song on the window; a line that beats the
  screen's line by `SELF_MARGIN` becomes the line tracked, and by
  `SELF_FLIP_MARGIN` the screen moves to it outright.
* **Has it ended.** The tracked line's path through the window reaching its
  last word arms the check for `WAIT_S`; it stays armed while that ending is
  still heard, and `QUIET_S` with no singing (a break) arms it too.
* **What started.** Every other distinct line of the song is a candidate,
  scored as "this line's tail, then the candidate's opening" (and, while
  armed, as an opening on its own). The written next line gets `NEXT_PRIOR`
  to break ties between look-alike openings. A candidate that beats staying
  on the line by `MARGIN` and the runner-up by `RUNNER_UP_MARGIN` moves the
  screen, and a share of the follower's belief jumps to its opening.

Replayed with the follower on real-time traces (Plan/NEXT_LINE_CHECK.md
steps 3-5): on the 53 held-out public recordings, changes more than 2 s late
or missed without it 315 -> 126, wrong-slide seconds -31%; church
recordings 328 -> 237 s. Live cost (step 6, M1 on mains): about 70 ms a
check beside the engine, speech recognition 7-10% slower.
"""

from __future__ import annotations

import functools
import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- phones

#: The phone model's outputs after CTC's blank (index 0). AH0 (the unstressed
#: schwa) is its own phone; every other vowel loses its stress mark.
PHONES = (
    "AA AE AH0 AH AO AW AY EH ER EY IH IY OW OY UH UW "
    "B CH D DH F G HH JH K L M N NG P R S SH T TH V W Y Z ZH"
).split()
IDX = {p: i + 1 for i, p in enumerate(PHONES)}
#: Words the dictionary doesn't have: one phone per letter (crude, and rare:
#: 99% of lyric words are in it).
LETTER = {
    "a": "AE", "b": "B", "c": "K", "d": "D", "e": "EH", "f": "F", "g": "G", "h": "HH",
    "i": "IH", "j": "JH", "k": "K", "l": "L", "m": "M", "n": "N", "o": "AA", "p": "P",
    "q": "K", "r": "R", "s": "S", "t": "T", "u": "AH", "v": "V", "w": "W", "x": "K",
    "y": "IY", "z": "Z",
}  # fmt: skip


def _norm(w: str) -> str:
    return "".join(c for c in w.lower() if c.isalpha() or c == "'").strip("'")


@functools.lru_cache(maxsize=4096)
def line_phones(words: tuple[str, ...]) -> tuple[int, ...]:
    """A line's phones as the model's indices: CMUdict's first pronunciation
    of each word (the bundled copy, `index.phonemic`), else its letters."""
    from auto_presenter.index.phonemic import _dictionary

    d = _dictionary()
    out: list[str] = []
    for raw in words:
        w = _norm(raw)
        if not w:
            continue
        pr = d.get(w) or d.get(w.replace("'", ""))
        if pr:
            out += ["AH0" if p == "AH0" else re.sub(r"\d", "", p) for p in pr[0]]
        else:
            out += [LETTER[c] for c in w if c in LETTER]
    return tuple(IDX[p] for p in out)


# ---------------------------------------------------------------- the phone model

#: Where a release build carries the model (`sidecar.spec`), and where
#: `scripts/setup_next_line_model.py` installs it for development.
MODEL_FILE = "next_line_head.npz"
#: The encoder the phone model was trained on, at this many 20 ms frames.
ENCODER_REPO = "mlx-community/whisper-small.en-mlx"
WINDOW_S = 2.5
AUDIO_CTX = int(WINDOW_S * 50)


def model_dir() -> Path:
    """`AUTO_PRESENTER_NEXT_LINE_MODEL`, else the copy bundled in a release
    build, else the app data folder."""
    override = os.environ.get("AUTO_PRESENTER_NEXT_LINE_MODEL")
    if override:
        return Path(override)
    base = getattr(sys, "_MEIPASS", None)
    if base and (Path(base) / "models" / "next_line" / MODEL_FILE).is_file():
        return Path(base) / "models" / "next_line"
    data_dir = os.environ.get("AUTO_PRESENTER_DATA_DIR")
    if data_dir:
        root = Path(data_dir)
    else:
        from auto_presenter.config import app_data_dir

        root = app_data_dir()
    return root / "models" / "next_line"


def _erf(x: np.ndarray) -> np.ndarray:
    # Abramowitz & Stegun 7.1.26 (error < 1.5e-7): numpy has no erf, and the
    # model was trained with the exact GELU
    s = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (
        ((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592
    ) * t * np.exp(-a * a)
    return s * y


def _gelu(x: np.ndarray) -> np.ndarray:
    return 0.5 * x * (1.0 + _erf(x / math.sqrt(2.0)))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class PhoneHead:
    """Encoder frames (T, 768) -> phone logits (T, 41): a width-5 convolution
    with GELU, a 2-layer bidirectional GRU (256 a direction), a linear read-out.
    Numpy, so it runs wherever the encoder does (the app ships no torch)."""

    def __init__(self, weights: dict[str, np.ndarray]) -> None:
        w = {k: np.asarray(v, np.float32) for k, v in weights.items()}
        # conv as one matmul over the 5 shifted copies: (5 * 768, 512)
        cw = w["conv.0.weight"]  # (out, in, k)
        self.conv_w = cw.transpose(2, 1, 0).reshape(-1, cw.shape[0])
        self.conv_b = w["conv.0.bias"]
        self.k = cw.shape[2]
        self.layers = []
        layer = 0
        while f"rnn.weight_ih_l{layer}" in w:
            dirs = []
            for suffix in ("", "_reverse"):
                dirs.append(
                    (
                        w[f"rnn.weight_ih_l{layer}{suffix}"].T.copy(),
                        w[f"rnn.weight_hh_l{layer}{suffix}"].T.copy(),
                        w[f"rnn.bias_ih_l{layer}{suffix}"],
                        w[f"rnn.bias_hh_l{layer}{suffix}"],
                    )
                )
            self.layers.append(dirs)
            layer += 1
        self.out_w = w["out.weight"].T.copy()
        self.out_b = w["out.bias"]

    @classmethod
    def load(cls, path: Path) -> PhoneHead:
        with np.load(path) as z:
            return cls({k: z[k] for k in z.files})

    @staticmethod
    def _gru(x: np.ndarray, w_ih, w_hh, b_ih, b_hh, reverse: bool) -> np.ndarray:
        n_t, hd = x.shape[0], w_hh.shape[0]
        gi = x @ w_ih + b_ih  # (frames, 3 * hd): r, z, n
        h = np.zeros(hd, np.float32)
        out = np.empty((n_t, hd), np.float32)
        for t in range(n_t - 1, -1, -1) if reverse else range(n_t):
            gh = h @ w_hh + b_hh
            r = _sigmoid(gi[t, :hd] + gh[:hd])
            z = _sigmoid(gi[t, hd : 2 * hd] + gh[hd : 2 * hd])
            n = np.tanh(gi[t, 2 * hd :] + r * gh[2 * hd :])
            h = (1.0 - z) * n + z * h
            out[t] = h
        return out

    def __call__(self, feats: np.ndarray) -> np.ndarray:
        x = np.asarray(feats, np.float32)
        n_t = x.shape[0]
        p = self.k // 2
        xp = np.pad(x, ((p, p), (0, 0)))
        cols = np.concatenate([xp[j : j + n_t] for j in range(self.k)], axis=1)
        x = _gelu(cols @ self.conv_w + self.conv_b)
        for dirs in self.layers:
            x = np.concatenate(
                [self._gru(x, *dirs[0], reverse=False), self._gru(x, *dirs[1], reverse=True)],
                axis=1,
            )
        return x @ self.out_w + self.out_b


# ---------------------------------------------------------------- scoring

NEG = -1e9


@dataclass
class Chains:
    """`score_chains` per chain: the best path ending inside its first
    `split` phones, and inside the rest, each against the free best-label
    path (0 is a perfect fit); `tail_state` where the first one ended."""

    in_tail: np.ndarray
    in_cand: np.ndarray
    tail_state: np.ndarray


def score_chains(
    logp: np.ndarray,
    seqs: list[tuple[int, ...]],
    g: float,
    split: list[int],
    entry_max: list[int] | None = None,
) -> Chains:
    """CTC paths for each phone sequence through the window's phone
    log-probabilities `logp` (T, 41), ending at its last frame. A path may
    start late (frames before it cost the best label minus `g` each) and join
    its sequence at any of the first `entry_max` phones (default `split`), so
    a chain "tail + opening" can only reach the opening through the tail."""
    n_t, n_h = logp.shape[0], len(seqs)
    b = logp.max(axis=1)
    garbage = np.concatenate([[0.0], np.cumsum(b - g)])
    lens = np.array([2 * len(s) + 1 for s in seqs])
    n_s = int(lens.max())
    em = np.full((n_h, n_t, n_s), NEG)
    skip = np.zeros((n_h, n_s), bool)
    valid_end = np.zeros((n_h, n_s), bool)
    entry = np.zeros((n_h, n_s), bool)
    for h, s in enumerate(seqs):
        em[h, :, 0 : lens[h] : 2] = logp[:, [0]]
        if s:
            em[h, :, 1 : lens[h] : 2] = logp[:, list(s)]
        for j in range(3, lens[h], 2):  # straight from the phone before, when different
            skip[h, j] = s[(j - 1) // 2] != s[(j - 3) // 2]
        valid_end[h, 1 : 2 * split[h] + 1] = True
        entry[h, : 2 * (split[h] if entry_max is None else entry_max[h]) + 1] = True
    alpha = np.full((n_h, n_s), NEG)
    pad1 = np.full((n_h, 1), NEG)
    pad2 = np.full((n_h, 2), NEG)
    for t in range(n_t):
        one = np.concatenate([pad1, alpha[:, :-1]], axis=1)
        two = np.where(skip, np.concatenate([pad2, alpha[:, :-2]], axis=1), NEG)
        best = np.maximum(np.maximum(alpha, one), two)
        best = np.where(entry, np.maximum(best, garbage[t]), best)
        alpha = best + em[:, t, :]
    tail = np.where(valid_end, alpha, NEG)
    in_cand = np.where(
        ~valid_end & (np.arange(n_s)[None] >= 1) & (np.arange(n_s)[None] < lens[:, None]),
        alpha,
        NEG,
    )
    return Chains(tail.max(axis=1) - b.sum(), in_cand.max(axis=1) - b.sum(), tail.argmax(axis=1))


# ---------------------------------------------------------------- the decision

#: Per-frame cost of the window's unexplained frames (CTC garbage).
G = 0.6
#: Pushed off CTC's blank before reading phones: the model under-commits.
BLANK_PUSH = 4.0
#: A candidate's lead over staying on the line, and over the runner-up.
MARGIN = 9.0
RUNNER_UP_MARGIN = 5.0
#: Added to the written next line's score (ties between look-alike openings).
NEXT_PRIOR = 6.0
#: Armed this long after the line's ending was heard.
WAIT_S = 6.0
#: The tracked line follows a fired candidate this long.
HOLD_S = 4.0
#: The line's ending: its last this-many words.
END_WORDS = 2
#: The line-finder's lead to track another line, and to move the screen.
SELF_MARGIN = 10.0
SELF_FLIP_MARGIN = 20.0
#: This long with no singing (blank owns the window) arms the check.
QUIET_S = 3.0
QUIET_SINGING = 0.05
#: Where a flip lands in the follower: this share of the belief, onto the
#: candidate's opening, this long before the tick.
JUMP = 0.3
JUMP_BACK_S = 0.5
#: Seconds between ticks.
TICK_S = 0.25


@dataclass
class _Line:
    slide: int
    text: str
    phones: tuple[int, ...]
    #: phones before the last `END_WORDS` words, and before the last word
    head: int
    before_last: int


@dataclass
class NextLineCheck:
    """One song's check. `step` once per tick (0.25 s) with the phone logits;
    `take_flip` hands over a slide to show, at most once."""

    slide_words: list[list[str]]
    fired: int | None = None
    t_fired: float = -1e9
    done: float | None = None
    done_for: int | None = None
    quiet: float = 0.0
    flip: tuple[int, float] | None = None
    _lines: dict[int, _Line] = field(default_factory=dict, repr=False)
    _distinct: list[int] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for j, w in enumerate(self.slide_words):
            if not w:
                continue
            text = " ".join(w)
            ph = line_phones(tuple(w))
            self._lines[j] = _Line(
                j,
                text,
                ph,
                len(line_phones(tuple(w[:-END_WORDS]))) if len(w) > END_WORDS else 0,
                len(line_phones(tuple(w[:-1]))) if len(w) > 1 else 0,
            )
            if text not in seen and ph:
                seen.add(text)
                self._distinct.append(j)

    def reset(self) -> None:
        self.fired, self.t_fired = None, -1e9
        self.done = self.done_for = None
        self.quiet = 0.0
        self.flip = None

    def _text(self, s: int | None) -> str | None:
        line = self._lines.get(s) if s is not None else None
        return line.text if line is not None else None

    def take_flip(self) -> tuple[int, float] | None:
        flip, self.flip = self.flip, None
        return flip

    def step(
        self,
        tt: float,
        logits: np.ndarray,
        mass: np.ndarray,
        shown: int | None,
        displayed: int | None,
        dt: float = TICK_S,
    ) -> None:
        """One tick at audio time `tt`. `mass`: the follower's belief per
        slide; `shown`: the follower's slide on screen; `displayed`: the
        latest read-out's slide."""
        sub = np.asarray(logits, np.float64)
        pb = np.exp(sub[:, 0] - np.logaddexp.reduce(sub, axis=1))
        self.quiet = self.quiet + dt if float((1 - pb).mean()) < QUIET_SINGING else 0.0
        if not self._distinct or mass.sum() <= 0:
            return
        now = int(np.argmax(mass))
        if shown is not None and shown in self._lines:
            now = shown
        sub = sub.copy()
        sub[:, 0] -= BLANK_PUSH
        lp = sub - np.logaddexp.reduce(sub, axis=1, keepdims=True)
        # which line is being sung: the line-finder over every distinct line
        uniq = self._distinct
        ph = [self._lines[j].phones for j in uniq]
        tl = score_chains(lp, ph, G, [len(p) for p in ph]).in_tail
        j = int(np.argmax(tl))
        now_text = self._text(now)
        base = next((i for i, u in enumerate(uniq) if self._lines[u].text == now_text), None)
        if base is None or tl[j] - tl[base] >= SELF_MARGIN:
            now = uniq[j]
            if (
                self._text(displayed) != self._lines[now].text
                and tl[j] - tl[base if base is not None else j] >= SELF_FLIP_MARGIN
            ):
                self.flip = (now, tt)  # the band is on another line than the screen
        cur = self.fired if self.fired is not None and tt - self.t_fired < HOLD_S else now
        if self.done is not None and self.done_for is not None and tt - self.done <= WAIT_S:
            cur = self.done_for  # keep the line heard ending, whatever the follower now believes
        if self.done_for != cur:
            self.done = self.done_for = None
        line = self._lines.get(cur)
        if line is None:
            return
        cur_text = line.text
        cands = [cur] + [u for u in uniq if self._lines[u].text != cur_text]
        cp = [self._lines[c].phones for c in cands]
        tail = line.phones
        n = len(cands)
        ch = score_chains(lp, [tail + x for x in cp], G, [len(tail)] * n, [line.head] * n)
        stay = float(ch.in_tail.max())
        best = ch.in_cand
        end_state = 2 * line.before_last + 1
        if int(ch.tail_state[0]) >= end_state:
            # the line's ending heard: armed from now (again, while it lasts)
            if self.done is None or self.done_for == cur:
                self.done, self.done_for = tt, cur
        if self.quiet >= QUIET_S:
            self.done, self.done_for = tt, cur  # a break: whatever is sung next is an opening
        if self.done is not None and tt - self.done <= WAIT_S:
            free = score_chains(lp, cp, G, [0] * n, [0] * n).in_cand
            best = np.maximum(best, free)
            stay = max(stay, -G * lp.shape[0])
        nxt = next((s for s in range(cur + 1, len(self.slide_words)) if self.slide_words[s]), None)
        nxt_text = self._text(nxt)
        best = best + np.array([NEXT_PRIOR * (self._lines[c].text == nxt_text) for c in cands])
        k = int(np.argmax(best))
        texts = [self._lines[c].text for c in cands]
        others = [best[i] for i in range(n) if texts[i] != texts[k]]
        runner = max(others) if others else -1e18
        if best[k] - stay >= MARGIN and best[k] - runner >= RUNNER_UP_MARGIN and cands[k] != cur:
            self.fired, self.t_fired = cands[k], tt
            self.done = self.done_for = None
            self.flip = (cands[k], tt)


def slide_mass(
    marginal_now: np.ndarray, slide_idx: np.ndarray, slide_words: list[list[str]]
) -> np.ndarray:
    """The follower's belief per slide, slides with no words left out."""
    m = np.asarray(marginal_now[: len(slide_idx)], np.float64)
    ok = slide_idx >= 0
    mass = np.bincount(slide_idx[ok], weights=m[ok], minlength=len(slide_words))[: len(slide_words)]
    for k, w in enumerate(slide_words):
        if not w:
            mass[k] = 0.0
    return mass


def model_info(d: Path) -> dict:
    p = d / "next_line_head.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ---------------------------------------------------------------- live


def mlx_encoder(repo: str):
    """Whisper's encoder on `WINDOW_S` of audio, for the check's own thread:
    the model Whisper's passes use (`repo` must be small.en, which the phone
    model was trained on), on a stream of its own and outside their GPU lock.
    It only reads the weights, and waiting behind a whole transcription pass
    (0.3-0.7 s) would make most of its answers late. Measured on an M1 with
    Whisper passes running beside it: identical output, ~50 ms a pass.

    MLX arrays not yet evaluated belong to the thread that made them, so the
    check never loads the model (Whisper's thread does) and makes its own
    stream and positional table on its own thread. Returns None until a
    transcription pass has run (`mlx_backend.FIRST_PASS`): the tick is skipped."""
    import threading

    local = threading.local()

    def encode(audio_16k: np.ndarray) -> np.ndarray | None:
        import mlx.core as mx
        import mlx.nn as nn
        from mlx_whisper.audio import log_mel_spectrogram, pad_or_trim
        from mlx_whisper.transcribe import ModelHolder
        from mlx_whisper.whisper import sinusoids

        from auto_presenter.asr.mlx_backend import FIRST_PASS

        model = ModelHolder.model
        if not FIRST_PASS.is_set() or model is None or ModelHolder.model_path != repo:
            return None
        state = getattr(local, "state", None)
        if state is None or state[0] is not model:
            stream = mx.new_stream(mx.gpu)
            with mx.stream(stream):
                d = model.dims
                pos = sinusoids(d.n_audio_ctx, d.n_audio_state).astype(mx.float16)[:AUDIO_CTX]
                mx.eval(pos)
            state = local.state = (model, stream, pos)
        _, stream, pos = state
        with mx.stream(stream):
            audio = mx.array(audio_16k.astype(np.float32))
            mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels)  # pyright: ignore[reportArgumentType]
            mel = pad_or_trim(mel, 2 * AUDIO_CTX, axis=-2)[None].astype(mx.float16)
            enc = model.encoder
            x = nn.gelu(enc.conv1(mel))
            x = nn.gelu(enc.conv2(x))
            x = x + pos[: x.shape[1]]
            for block in enc.blocks:
                x, _, _ = block(x)
            out = enc.ln_post(x)[0].astype(mx.float32)
            mx.eval(out)
            return np.array(out)

    return encode


class PhoneListener:
    """The check's own thread. The capture thread `feed`s audio; every
    `TICK_S` of it, the trailing `WINDOW_S` go to the thread, which hands
    phone logits to `on_logits(t_end, logits)`. Only the newest window waits:
    a thread that falls behind skips ticks rather than answering late."""

    def __init__(self, encode, head: PhoneHead, on_logits) -> None:
        import queue
        import threading

        self._encode, self._head, self._on_logits = encode, head, on_logits
        n = int(WINDOW_S * 16000)
        self._ring = np.zeros(n, np.float32)
        self._filled = 0
        self._next_tick: float | None = None
        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self.paused = False
        #: health: per-check milliseconds (recent), ticks skipped, checks run
        self.ms: list[float] = []
        self.skipped = 0
        self.checks = 0
        self._thread = threading.Thread(target=self._run, name="next-line-check", daemon=True)
        self._thread.start()

    def feed(self, samples: np.ndarray, t_end: float) -> None:
        import queue

        x = np.asarray(samples, np.float32).ravel()
        n = len(self._ring)
        if len(x) >= n:
            self._ring[:] = x[-n:]
        else:
            self._ring = np.roll(self._ring, -len(x))
            self._ring[-len(x) :] = x
        self._filled = min(n, self._filled + len(x))
        if self._next_tick is None:
            self._next_tick = t_end
        if t_end + 1e-6 < self._next_tick or self.paused or self._filled < n:
            return
        while self._next_tick <= t_end + 1e-6:
            self._next_tick += TICK_S
        item = (t_end, self._ring.copy())
        try:
            self._q.put_nowait(item)
        except queue.Full:
            try:
                self._q.get_nowait()
                self.skipped += 1
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(item)
            except queue.Full:
                self.skipped += 1

    def _run(self) -> None:
        import queue
        import time

        while not self._stop.is_set():
            try:
                t_end, audio = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            started = time.perf_counter()
            try:
                feats = self._encode(audio)
                if feats is None:
                    continue  # not ready (speech recognition hasn't run yet)
                self._on_logits(t_end, self._head(feats))
            except Exception:
                log.exception("next-line check failed; off for this session")
                return
            self.checks += 1
            self.ms = [*self.ms[-59:], (time.perf_counter() - started) * 1000]

    def stop(self) -> None:
        self._stop.set()


def load_head() -> PhoneHead | None:
    p = model_dir() / MODEL_FILE
    if not p.is_file():
        return None
    try:
        return PhoneHead.load(p)
    except Exception:
        log.exception("next-line model at %s could not be read", p)
        return None
