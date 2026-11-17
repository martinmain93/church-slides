"""Where the band was, in hindsight: heard words aligned to a song's slides.

A hidden Markov model over every word position of every sung slide, run
forward *and* backward over the words heard so far, so each word is placed
with the evidence after it as well as before. A causal tracker has to decide
at a fork with what it has; three seconds later the next line's words settle
it, and the backward pass uses them.

The model is deliberately neutral: library order, a small chance of repeating
the line or jumping anywhere, nothing learned and nothing from what the
engine displayed. This is what makes it safe to learn from: if it used the
learned order, a habit it had wrongly learned would place this week's words
to fit, and confirm itself.

Cost: one dense N x N matrix-vector product per heard word, N the song's
word count (a few hundred), forward and back: a few milliseconds per song.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from auto_presenter.follower import score as sc

#: Within a line, from one heard word to the next.
P_STAY = 0.12  # the same word again (Whisper repeats; a held note re-heard)
P_ADVANCE = 0.6
P_SKIP = (0.1, 0.04)  # one or two words missed
#: From a line's last `END_REGION` words, the band may go on to a line start.
END_REGION = 2
P_NEXT_LINE = 0.5
#: Where a line goes next (shares of P_NEXT_LINE): the next slide in library
#: order, the same line again, any other line.
ORDER_NEXT = 0.75
ORDER_SAME = 0.1
#: Anywhere, any time: a restart, an ad-lib jump.
P_ANYWHERE = 0.01
#: Floor of a word's emission: a heard word that matches nothing (a junk
#: word, talk, a misheard lyric) costs this much, not everything.
JUNK = 0.05
#: Emission sharpness on the phonetic match score in [0, 1]: unrelated words
#: score around 0.3-0.5, so a high power keeps them near the floor.
SHARPNESS = 6.0
#: Two passes of one line are separate when the place falls back to the
#: line's first words.
RESTART_FROM = 3


@dataclass(frozen=True)
class HeardWord:
    t: float  # audio time the word started, seconds
    text: str


@dataclass
class Pass:
    """One pass through one slide's line."""

    slide: int
    t_start: float
    t_end: float
    #: (word index in the slide, audio time) for each heard word placed here.
    onsets: list[tuple[int, float]] = field(default_factory=list)
    #: Mean posterior of this slide over the pass's words.
    confidence: float = 0.0

    @property
    def n_words(self) -> int:
        return len(self.onsets)


@dataclass
class Alignment:
    passes: list[Pass]
    #: words x slides posterior (columns: `slides`), for diagnostics.
    posterior: np.ndarray
    slides: list[int]
    #: The words aligned (empty-after-normalising ones dropped), and where
    #: each was placed: its slide and word index in that slide.
    words: list[HeardWord] = field(default_factory=list)
    word_slide: list[int] = field(default_factory=list)
    word_pos: list[int] = field(default_factory=list)

    @property
    def sequence(self) -> list[int]:
        return [p.slide for p in self.passes]


class SongModel:
    """The state space and transitions for one song; build once, align often."""

    def __init__(self, slides: list[list[str]]):
        self.words: list[str] = []
        self.st_slide: list[int] = []
        self.st_pos: list[int] = []
        self.first: dict[int, int] = {}
        self.last: dict[int, int] = {}
        for si, sw in enumerate(slides):
            mine = [w for w in (sc.norm(x) for x in sw) if w]
            if not mine:
                continue
            self.first[si] = len(self.words)
            for j, w in enumerate(mine):
                self.words.append(w)
                self.st_slide.append(si)
                self.st_pos.append(j)
            self.last[si] = len(self.words) - 1
        self.sung = list(self.first)
        self.N = len(self.words)
        self.st_slide_arr = np.asarray(self.st_slide)
        self.T = self._transitions() if self.N else np.zeros((0, 0))
        self.init = np.zeros(self.N)
        for k, si in enumerate(self.sung):
            self.init[self.first[si]] = 0.6 if k == 0 else 0.4 / max(1, len(self.sung) - 1)
        if self.N and self.init.sum() > 0:
            self.init /= self.init.sum()
        self._emit_cache: dict[str, np.ndarray] = {}
        # slide column per state, for summing posteriors per slide
        self.col = {si: i for i, si in enumerate(self.sung)}
        self.onehot = np.zeros((self.N, len(self.sung)))
        for s, si in enumerate(self.st_slide):
            self.onehot[s, self.col[si]] = 1.0

    def _order(self, si: int) -> dict[int, float]:
        """Where line `si` goes next: library order, the same line, any other."""
        sung = self.sung
        if len(sung) == 1:
            return {si: 1.0}
        nxt = sung[(sung.index(si) + 1) % len(sung)]
        out = {nxt: ORDER_NEXT, si: ORDER_SAME}
        others = [x for x in sung if x not in (si, nxt)]
        rest = 1.0 - ORDER_NEXT - ORDER_SAME
        for x in others:
            out[x] = rest / len(others)
        if not others:
            out[nxt] += rest
        return out

    def _transitions(self) -> np.ndarray:
        n = self.N
        trans = np.zeros((n, n))
        starts = [self.first[si] for si in self.sung]
        for s in range(n):
            si = self.st_slide[s]
            last = self.last[si]
            trans[s, s] += P_STAY
            lost = 0.0  # within-line mass with nowhere to go: on to a new line
            for step, p in ((1, P_ADVANCE), (2, P_SKIP[0]), (3, P_SKIP[1])):
                if s + step <= last:
                    trans[s, s + step] += p
                else:
                    lost += p
            go = lost + (P_NEXT_LINE if last - s < END_REGION else 0.0)
            if go > 0:
                for sj, share in self._order(si).items():
                    trans[s, self.first[sj]] += go * share
            for a in starts:
                trans[s, a] += P_ANYWHERE / len(starts)
        trans /= trans.sum(axis=1, keepdims=True)
        return trans

    def emission(self, text: str) -> np.ndarray:
        w = sc.norm(text)
        e = self._emit_cache.get(w)
        if e is None:
            from auto_presenter.index.phonetic import phonetic_match_score

            uniq = {x: (1.0 if x == w else phonetic_match_score(w, x)) for x in set(self.words)}
            e = JUNK + (1 - JUNK) * np.array([uniq[x] for x in self.words]) ** SHARPNESS
            self._emit_cache[w] = e
        return e


def align(model: SongModel, words: list[HeardWord]) -> Alignment:
    """Every heard word placed in the song, forward-backward."""
    words = [w for w in words if sc.norm(w.text)]
    n_slides = len(model.sung)
    if not words or not model.N:
        return Alignment([], np.zeros((0, n_slides)), list(model.sung))
    k_words, trans = len(words), model.T
    emit = np.stack([model.emission(w.text) for w in words])
    alpha = np.zeros((k_words, model.N))
    a = model.init * emit[0]
    alpha[0] = a / a.sum()
    for k in range(1, k_words):
        a = (alpha[k - 1] @ trans) * emit[k]
        alpha[k] = a / a.sum()
    beta = np.ones((k_words, model.N))
    for k in range(k_words - 2, -1, -1):
        b = trans @ (emit[k + 1] * beta[k + 1])
        beta[k] = b / b.sum()
    gamma = alpha * beta
    gamma /= gamma.sum(axis=1, keepdims=True)
    post = gamma @ model.onehot  # k_words x slides
    word_slide = [model.sung[int(c)] for c in post.argmax(axis=1)]
    word_pos = [
        int(gamma[k, model.first[si] : model.last[si] + 1].argmax())
        for k, si in enumerate(word_slide)
    ]
    return Alignment(
        _passes(model, words, gamma, post),
        post,
        list(model.sung),
        words=words,
        word_slide=word_slide,
        word_pos=word_pos,
    )


def _passes(model: SongModel, words: list[HeardWord], gamma, post) -> list[Pass]:
    out: list[Pass] = []
    for k, w in enumerate(words):
        c = int(post[k].argmax())
        si = model.sung[c]
        a, b = model.first[si], model.last[si] + 1
        j = int(gamma[k, a:b].argmax())
        cur = out[-1] if out else None
        restart = (
            cur is not None
            and cur.slide == si
            and cur.onsets
            and j < RESTART_FROM
            and cur.onsets[-1][0] >= j + RESTART_FROM
        )
        if cur is None or cur.slide != si or restart:
            out.append(Pass(slide=si, t_start=w.t, t_end=w.t))
            cur = out[-1]
        cur.onsets.append((j, w.t))
        cur.t_end = w.t
        cur.confidence += float(post[k, c])
    for p in out:
        p.confidence /= max(1, p.n_words)
    return _merge_blips(out)


def _merge_blips(passes: list[Pass]) -> list[Pass]:
    """A one-word pass between two passes of the same slide is a stray word
    of that line; a one-word pass elsewhere stays only if confident."""
    out: list[Pass] = []
    for i, p in enumerate(passes):
        nxt = passes[i + 1] if i + 1 < len(passes) else None
        prev = out[-1] if out else None
        if p.n_words == 1 and prev is not None and nxt is not None and prev.slide == nxt.slide:
            continue
        if p.n_words == 1 and p.confidence < 0.6 and prev is not None:
            continue
        if (
            prev is not None
            and prev.slide == p.slide
            and p.onsets[0][0] > prev.onsets[-1][0] - RESTART_FROM
        ):
            n0, n1 = prev.n_words, p.n_words
            prev.onsets.extend(p.onsets)
            prev.t_end = p.t_end
            prev.confidence = (prev.confidence * n0 + p.confidence * n1) / (n0 + n1)
            continue
        out.append(p)
    return out
