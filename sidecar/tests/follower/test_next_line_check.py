"""The next-line check (`follower.next_line`): phones, the phone model, the
CTC scoring, the decision, and its path to the screen in a live session.

The decision tests sing synthetic phone streams: each phone of the words
held for a few 20 ms frames with blank between, read 0.25 s at a time
through a 2.5 s window, the way the live listener reads the encoder."""

from __future__ import annotations

import numpy as np
import pytest

from auto_presenter.follower import next_line as nl
from auto_presenter.follower.live import build_live_followers

SLIDES = [
    [],
    "amazing grace how sweet the sound".split(),
    "that saved a wretch like me".split(),
    "i once was lost but now am found".split(),
    "was blind but now i see".split(),
    "praise god from whom all blessings flow".split(),
]
SECTIONS = ["other:0", "verse:1", "verse:1", "verse:1", "verse:1", "chorus:1"]
FPS = 50


def test_line_phones_use_the_dictionary_then_letters():
    assert [nl.PHONES[i - 1] for i in nl.line_phones(("Grace,",))] == ["G", "R", "EY", "S"]
    assert [nl.PHONES[i - 1] for i in nl.line_phones(("the",))] == ["DH", "AH0"]
    # not in the dictionary: a phone a letter
    assert [nl.PHONES[i - 1] for i in nl.line_phones(("zxq",))] == ["Z", "K", "K"]


def test_phone_head_conv_matches_a_direct_convolution():
    rng = np.random.default_rng(0)
    d, h, k = 6, 4, 5
    w = {
        "conv.0.weight": rng.normal(size=(2 * h, d, k)),
        "conv.0.bias": rng.normal(size=2 * h),
        "out.weight": rng.normal(size=(len(nl.PHONES) + 1, 2 * h)),
        "out.bias": rng.normal(size=len(nl.PHONES) + 1),
    }
    for layer in range(2):
        for sfx in ("", "_reverse"):
            w[f"rnn.weight_ih_l{layer}{sfx}"] = rng.normal(size=(3 * h, 2 * h)) * 0.3
            w[f"rnn.weight_hh_l{layer}{sfx}"] = rng.normal(size=(3 * h, h)) * 0.3
            w[f"rnn.bias_ih_l{layer}{sfx}"] = rng.normal(size=3 * h) * 0.1
            w[f"rnn.bias_hh_l{layer}{sfx}"] = rng.normal(size=3 * h) * 0.1
    head = nl.PhoneHead(w)
    x = rng.normal(size=(9, d)).astype(np.float32)
    xp = np.pad(x, ((2, 2), (0, 0)))
    direct = np.stack(
        [
            np.einsum("oik,ki->o", w["conv.0.weight"], xp[t : t + k]) + w["conv.0.bias"]
            for t in range(9)
        ]
    )
    cols = np.concatenate([xp[j : j + 9] for j in range(k)], axis=1)
    assert np.allclose(cols @ head.conv_w + head.conv_b, direct, atol=1e-4)
    out = head(x)
    assert out.shape == (9, len(nl.PHONES) + 1)
    # bidirectional: the first frame's output depends on the last frame's input
    y = x.copy()
    y[-1] += 1.0
    assert not np.allclose(head(y)[0], out[0])


def _sing(*lines: tuple[float, list[str]], end: float) -> np.ndarray:
    """Frame logits for words starting at the given times: each phone held
    3 frames, 1 blank frame after it; blank elsewhere."""
    n = int(end * FPS) + 1
    lg = np.zeros((n, len(nl.PHONES) + 1))
    lg[:, 0] = 10.0
    for t0, words in lines:
        f = int(t0 * FPS)
        for p in nl.line_phones(tuple(words)):
            for _ in range(3):
                if f < n:
                    lg[f] = 0.0
                    lg[f, p] = 10.0
                f += 1
            f += 1
    return lg


def _run(lg: np.ndarray, shown: int, t_from: float, t_to: float, slides=SLIDES):
    """Ticks every 0.25 s with the follower's belief on `shown`; the first
    flip (slide, tick time) or None."""
    check = nl.NextLineCheck(slides)
    mass = np.zeros(len(slides))
    mass[shown] = 1.0
    t = t_from
    while t <= t_to:
        f = round(t * FPS)
        check.step(t, lg[f - 125 : f], mass, shown, shown)
        flip = check.take_flip()
        if flip is not None:
            return flip
        t += 0.25
    return None


def test_scoring_prefers_the_sung_line():
    lg = _sing((0.2, SLIDES[2]), end=2.5)
    lp = lg - np.logaddexp.reduce(lg, axis=1, keepdims=True)
    seqs = [nl.line_phones(tuple(s)) for s in SLIDES[1:]]
    tl = nl.score_chains(lp, seqs, nl.G, [len(s) for s in seqs]).in_tail
    assert int(np.argmax(tl)) == 1


def test_hears_the_written_next_line_start():
    lg = _sing((0.5, SLIDES[1]), (3.2, SLIDES[2]), end=8.0)
    flip = _run(lg, shown=1, t_from=2.5, t_to=8.0)
    assert flip is not None and flip[0] == 2
    assert flip[1] < 3.2 + 1.5  # within about a second of the first word


def test_hears_a_jump_back_to_the_chorus():
    lg = _sing((0.5, SLIDES[4]), (3.0, SLIDES[5]), end=8.0)
    flip = _run(lg, shown=4, t_from=2.5, t_to=8.0)
    assert flip is not None and flip[0] == 5


def test_still_singing_the_shown_line_moves_nothing():
    lg = _sing((0.5, SLIDES[3]), (3.0, SLIDES[3][:4]), end=6.0)
    assert _run(lg, shown=3, t_from=2.5, t_to=6.0) is None


def test_silence_moves_nothing():
    lg = _sing(end=8.0)
    assert _run(lg, shown=1, t_from=2.5, t_to=8.0) is None


def test_a_flip_reaches_the_screen_through_the_live_follower():
    followers = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    sf = followers.songs[0]
    sf.check = nl.NextLineCheck(sf.ref.slide_words)
    followers.enter(0, 1, t=0.0)
    from auto_presenter.audio.types import AudioFrame

    def frame(t_end: float) -> AudioFrame:
        return AudioFrame(samples=np.zeros(1600, np.float32), t_start=t_end - 0.1, t_end=t_end)

    lg = _sing((0.5, SLIDES[1]), (3.2, SLIDES[2]), end=8.0)
    decisions = []
    t = 0.1
    while t <= 6.0 + 1e-9:
        d = followers.on_frame(frame(round(t, 1)))
        if d is not None:
            decisions.append(d)
        tick = round(t, 1)
        if tick >= 2.5 and abs(tick * 4 - round(tick * 4)) < 1e-6:
            f = round(tick * FPS)
            followers._on_phones(tick, lg[f - 125 : f])
        t += 0.1
    assert decisions and decisions[0].slide_idx == 2
    assert decisions[0].t_seconds < 3.2 + 2.0
    assert followers.next_line_flips >= 1


def _flip_through(followers, shown: int, to: int = 2):
    """Enter on `shown`, hand the check a heard flip to slide 2, read out."""
    from auto_presenter.audio.types import AudioFrame

    sf = followers.songs[0]
    sf.check = nl.NextLineCheck(sf.ref.slide_words)
    followers.enter(0, shown, t=0.0)
    out = []
    for i in range(1, 41):
        t = round(i * 0.1, 1)
        if t == 3.0:  # the slide has been up for 3 s: past the settle time
            sf.check_flip = (to, 3.0)
        d = followers.on_frame(
            AudioFrame(samples=np.zeros(1600, np.float32), t_start=t - 0.1, t_end=t)
        )
        out.append(d)
    return out


def test_no_band_holds_the_flip():
    followers = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    followers.band_playing = lambda: False
    _flip_through(followers, shown=1)
    assert followers.next_line_flips == 0 and followers.next_line_held == 1


def test_never_flips_off_a_blank_slide():
    followers = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    followers.band_playing = lambda: True
    _flip_through(followers, shown=0)
    assert followers.next_line_flips == 0 and followers.next_line_held == 1


def test_a_band_lets_the_flip_through():
    followers = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    followers.band_playing = lambda: True
    _flip_through(followers, shown=1)
    assert followers.next_line_flips == 1


def test_never_undoes_a_move_to_the_next_slide():
    followers = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    followers.band_playing = lambda: True
    _flip_through(followers, shown=3, to=2)
    assert followers.next_line_flips == 0 and followers.next_line_held == 1


def test_waits_after_a_slide_change():
    followers = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    followers.band_playing = lambda: True
    sf = followers.songs[0]
    sf.follower._shown_since = 2.5  # the screen changed half a second ago
    sf.follower._seen_shown = 1
    _flip_through(followers, shown=1)
    assert followers.next_line_flips == 0 and followers.next_line_held >= 1


@pytest.mark.parametrize("bad", ["", "..."])
def test_lines_without_phones_are_never_candidates(bad):
    slides = [[], [bad], *SLIDES[1:3]]
    check = nl.NextLineCheck(slides)
    assert 1 not in check._distinct
