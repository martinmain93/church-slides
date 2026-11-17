"""In-service learning: a song with no lead sheet learns its slides' rhythm and
chords from the service (`follower.learn`) and the live follower rebuilds on
them (`LiveFollowers.apply_learned`).

The aligner and beat tracker are fakes (Whisper and Beat This are not run in
unit tests); the look-back and the follower are real.
"""

from __future__ import annotations

import numpy as np
import pytest

from auto_presenter.follower import learn
from auto_presenter.follower.learn import (
    ServiceLearner,
    beat_segment,
    beats_usable,
    borrow,
    clean_beats,
    metre_groups,
    syllables,
)
from auto_presenter.follower.live import build_live_followers, carry
from auto_presenter.follower.spec import NONE

# Verses 1 and 2 share a metre (and so a tune); the chorus does not.
SLIDES = [
    [],
    ["when", "peace", "like", "a", "river", "attendeth", "my", "way"],
    ["when", "sorrows", "like", "sea", "billows", "roll", "along", "now"],
    ["it", "is", "well", "with", "my", "soul"],
]
SECTIONS = ["other:0", "verse:1", "verse:2", "chorus:1"]
WORD_S = 0.5
BEAT_S = 0.5


def test_syllables_count_vowel_groups():
    assert syllables("river") == 2
    assert syllables("soul") == 1
    assert syllables("attendeth") == 3
    assert syllables("time") == 1  # silent e


def test_lines_of_one_metre_share_a_tune_and_others_do_not():
    groups = metre_groups(SLIDES)
    together = next(g for g in groups if 1 in g)
    assert 2 in together
    assert 3 not in together


def test_a_missed_beat_is_put_back_and_a_double_dropped():
    beats = np.arange(20) * 0.5
    missed = np.delete(beats, 7)
    assert np.allclose(clean_beats(missed), beats)
    doubled = np.sort(np.append(beats, 3.1))
    assert np.allclose(clean_beats(doubled), beats)


def test_a_beat_track_with_many_gaps_is_not_used():
    beats = np.arange(40) * 0.5
    assert beats_usable(beats)
    assert not beats_usable(np.delete(beats, [5, 6, 12, 13, 20, 21, 28, 29]))
    assert not beats_usable(None)


def test_a_slide_in_beat_time_places_its_words_and_heard_chords():
    beats = np.arange(40) * BEAT_S
    words = SLIDES[3]
    on = 2.0 + np.arange(len(words)) * BEAT_S  # a word a beat
    probs = np.zeros(25, np.float32)
    probs[5] = 0.9

    seg = beat_segment(3, words, on, on[-1] + 2.0, beats, lambda a, b: probs)
    offs = [o for _w, o in seg.words]
    assert offs == [round(k / learn.CELL) for k in range(len(words))]
    assert seg.cells >= offs[-1] + 1
    assert (seg.chord == 5).all()

    unsure = probs.copy()
    unsure[5] = learn.CHORD_MIN - 0.1
    seg = beat_segment(3, words, on, on[-1] + 2.0, beats, lambda a, b: unsure)
    assert (seg.chord == NONE).all()


def test_a_tune_mate_borrows_the_tune_stretched_to_its_words():
    beats = np.arange(40) * BEAT_S
    on = 1.0 + np.arange(8) * BEAT_S
    src = beat_segment(1, SLIDES[1], on, on[-1] + 2.0, beats, lambda a, b: None)
    lent = borrow(src, 2, SLIDES[2][:6])
    assert lent.slide == 2 and lent.cells == src.cells
    assert [w for w, _ in lent.words] == SLIDES[2][:6]
    offs = [o for _w, o in lent.words]
    assert offs[0] == src.words[0][1] and offs[-1] == src.words[-1][1]


# ---------------------------------------------------------------- the learner


def _sing(order: list[int], start: float = 2.0, gap: float = 3.0):
    """Words of `order`'s slides at a steady pace: (audio t, arrival, text)."""
    out, t = [], start
    for si in order:
        for w in SLIDES[si]:
            out.append((t, t + 1.0, w))
            t += WORD_S
        t += gap
    return out, t


def _learner(aligned: list | None = None):
    calls = []

    def aligner(audio, words):
        calls.append(list(words))
        return None if aligned is None else [k * WORD_S + 1.0 for k in range(len(words))]

    def beats(audio):
        return np.arange(0.0, len(audio) / learn.SR, BEAT_S)

    return ServiceLearner({7: SLIDES}, aligner, beats), calls


def _confirm(lr: ServiceLearner, now: float) -> None:
    """Two agreeing beat runs, as 30 s apart in a service."""
    s = lr.songs[7]
    lr._refresh_beats(s, now)
    s.beats_at = -1e9


def _feed(lr: ServiceLearner, words, until: float):
    lr.on_words(7, words)
    sr = learn.SR
    for k in range(int(until)):
        lr.on_audio(7, float(k), np.zeros(sr, np.float32))
    probs = np.zeros(25, np.float32)
    probs[2] = 0.8
    for k in range(int(until * learn.CHORD_FPS)):
        lr.on_chords(7, k / learn.CHORD_FPS, probs)
    _confirm(lr, until)


def test_a_sure_pass_is_learned_and_lent_to_its_tune_mate():
    lr, calls = _learner(aligned=[])
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    known = lr.tick(7, end + 5)
    assert known is not None
    assert 1 in known and 3 in known  # sung and sure
    assert 2 in known  # never sung: lent by verse 1, its tune mate
    assert known[1].chord.max() == 2  # the chord heard over it
    assert calls  # placed by forced alignment
    # nothing new: nothing to publish
    assert lr.tick(7, end + 6) is None


def test_nothing_is_learned_without_a_usable_beat_grid():
    lr, _ = _learner(aligned=[])
    lr.beat_tracker = lambda audio: np.delete(
        np.arange(0, 60, 0.5), [k for b in range(3, 110, 8) for k in (b, b + 1)]
    )
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    assert lr.tick(7, end + 5) is None


def test_a_line_the_aligner_cannot_place_is_not_learned():
    lr, _ = _learner(aligned=None)
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    assert lr.tick(7, end + 5) is None
    assert lr.stats["slides learned"] == 0


def test_nothing_is_learned_until_two_grids_agree_on_the_tempo():
    lr, _ = _learner(aligned=[])
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    s = lr.songs[7]
    s.confirmed, s.tempo0, s.beats_at = False, None, -1e9
    assert lr.tick(7, end + 5) is None  # one grid: not yet
    s.beats_at = -1e9
    assert lr.tick(7, end + 6)  # a second, agreeing: learned


def test_a_learned_slide_is_forgotten_if_its_pass_stops_being_sure(monkeypatch):
    lr, _ = _learner(aligned=[])
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    assert 1 in (lr.tick(7, end + 5) or {})
    monkeypatch.setattr(learn, "MIN_CONFIDENCE", 1.01)  # nothing is sure any more
    lr.songs[7].beats_at = end + 5  # no beat rerun
    got = lr.tick(7, end + 10)
    assert got is not None and 1 not in got
    assert lr.stats["slides forgotten"] >= 1


def test_a_settled_slide_is_kept_however_the_look_back_changes_its_mind(monkeypatch):
    lr, _ = _learner(aligned=[])
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    lr.tick(7, end + 5)
    monkeypatch.setattr(learn, "MIN_CONFIDENCE", 1.01)
    lr.songs[7].beats_at = end + 5 + learn.REVISE_S
    assert lr.tick(7, end + 5 + learn.REVISE_S) is None  # frozen: no change
    assert 1 in lr.songs[7].own


def test_a_song_whose_tempo_flips_forgets_what_it_learned():
    lr, _ = _learner(aligned=[])
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    assert lr.tick(7, end + 5)
    # the tracker switches to double time
    lr.beat_tracker = lambda audio: np.arange(0.0, len(audio) / learn.SR, BEAT_S / 2)
    lr.songs[7].beats_at = -1e9
    assert lr.tick(7, end + 6) == {}  # back to the library slides
    assert lr.stats["songs dropped"] == 1
    lr.songs[7].beats_at = -1e9
    assert lr.tick(7, end + 7) is None  # and stays there


def test_a_new_grid_re_measures_every_learned_slide():
    lr, _ = _learner(aligned=[])
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    first = lr.tick(7, end + 5)
    # a slightly faster grid (within the tempo tolerance): more beats per line
    lr.beat_tracker = lambda audio: np.arange(0.0, len(audio) / learn.SR, BEAT_S * 0.9)
    lr.songs[7].beats_at = -1e9
    second = lr.tick(7, end + 6)
    assert second is not None
    assert second[1].cells > first[1].cells
    assert lr.songs[7].measured_on is lr.songs[7].beats


def test_talk_placed_on_the_lyrics_is_not_learned():
    """The look-back places any words on some slide; words that are not the
    lyrics they are placed on teach nothing."""
    lr, _ = _learner(aligned=[])
    talk = ["so", "do", "you", "see", "this", "creature", "here", "an", "eagle", "yes"]
    words, t = [], 2.0
    for _ in range(4):
        for w in talk:
            words.append((t, t + 1.0, w))
            t += WORD_S
        t += 3.0
    _feed(lr, words, t + 5)
    assert lr.tick(7, t + 5) is None
    assert lr.stats["slides learned"] == 0


def test_a_collapsed_alignment_is_not_learned():
    # every word placed at the same instant
    lr, _ = _learner(aligned=[])
    lr.aligner = lambda audio, words: [1.0] * len(words)
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    assert lr.tick(7, end + 5) is None
    assert lr.stats["collapsed alignments"] >= 1


def test_collapse_and_lexical_checks():
    from types import SimpleNamespace

    from auto_presenter.follower.learn import collapsed, lexical_match
    from auto_presenter.follower.spec import SegmentSpec

    p = SimpleNamespace(onsets=[(0, 1.0), (1, 1.5), (2, 2.0), (3, 2.5)])
    lyric = ["our", "god", "is", "an"]
    assert lexical_match(p, lyric, {1.0: "our", 1.5: "god", 2.0: "is", 2.5: "an"}) == 1.0
    assert lexical_match(p, lyric, {1.0: "im", 1.5: "not", 2.0: "a", 2.5: "man"}) == 0.0
    flat = np.full(8, NONE, np.int16)
    assert collapsed(SegmentSpec(1, 8, [("a", 0), ("b", 0), ("c", 0), ("d", 2)], flat, flat))
    assert not collapsed(SegmentSpec(1, 8, [("a", 0), ("b", 2), ("c", 4), ("d", 6)], flat, flat))


# ---------------------------------------------------------------- the rebuild


def test_a_rebuild_carries_the_belief_by_each_slides_share():
    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True)
    sf = lf.songs[7]
    old = sf.follower
    old.anchor(3, 0.0)
    lr, _ = _learner(aligned=[])
    words, end = _sing([1, 3, 1, 3])
    _feed(lr, words, end + 5)
    known = lr.tick(7, end + 5)
    assert known is not None

    lf.apply_learned(7, known)
    new = sf.follower
    assert new is not old and lf.rebuilds == 1
    assert abs(float(new.alpha.sum()) - 1.0) < 1e-5
    on_3 = new.ref.slide_of == 3
    assert float(new.alpha[:, on_3].sum()) > 0.99
    assert new.ref.has_chords  # the learned chords are in the reference


def test_carry_conserves_belief_spread_over_slides():
    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True)
    old = lf.songs[7].follower
    rng = np.random.default_rng(0)
    old.alpha = rng.random(old.alpha.shape).astype(np.float32)
    old.alpha /= old.alpha.sum()
    a = carry(old, old.ref)
    for s in (1, 2, 3):
        mine = old.ref.slide_of == s
        assert abs(float(a[:, mine].sum()) - float(old.alpha[:, mine].sum())) < 1e-5


def test_recent_words_are_pushed_again_onto_the_rebuilt_reference():
    from auto_presenter.asr.types import Word

    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True)
    lf.enter(7, 0, t=0.0)
    sung = [
        Word(text=w, t_start=1.0 + k * WORD_S, t_end=1.2 + k * WORD_S, confidence=1.0)
        for k, w in enumerate(SLIDES[3])
    ]
    lf.audio_now = 5.0
    lf.on_words(sung, arrival=5.0)
    assert len(lf.songs[7].raw) == len(SLIDES[3])
    seen = []
    orig = type(lf.songs[7].follower).push_word

    def spy(self, t, arr, w):
        seen.append(w)
        return orig(self, t, arr, w)

    type(lf.songs[7].follower).push_word = spy
    try:
        lr, _ = _learner(aligned=[])
        words, end = _sing([1, 3, 1, 3])
        _feed(lr, words, end + 5)
        lf.apply_learned(7, lr.tick(7, end + 5) or {})
    finally:
        type(lf.songs[7].follower).push_word = orig
    assert seen == SLIDES[3]


# ---------------------------------------------------------------- wiring


def test_alignments_run_on_the_asr_thread_when_the_scorer_is_idle():
    import threading

    class Scorer:
        def score(self, audio, spans, place_among=None):
            raise AssertionError("no song is active: nothing to score")

        def word_starts(self, audio, words):
            return [0.25 * k for k in range(len(words))]

    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True, scorer=Scorer())
    got = []
    t = threading.Thread(target=lambda: got.append(lf.align_words(np.zeros(16000), ["a", "b"])))
    t.start()
    while lf._align_jobs.empty():
        pass
    assert lf.side_pass([], 1.0) is None  # no song on screen: the GPU is free
    t.join(2)
    assert got == [[0.0, 0.25]]


def test_alignments_also_run_when_the_scorer_scores_every_tick():
    """The scorer is due at every ASR step (both every 2 s): an alignment
    waiting for an idle tick would never run."""
    import threading

    from auto_presenter.audio.types import AudioFrame

    class Scorer:
        def score(self, audio, spans, place_among=None):
            from types import SimpleNamespace

            return SimpleNamespace(scores=[0.0] * len(spans), place_s=0.0)

        def word_starts(self, audio, words):
            return [0.5]

    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True, scorer=Scorer())
    lf.enter(7, 0, t=0.0)
    got = []
    t = threading.Thread(target=lambda: got.append(lf.align_words(np.zeros(16000), ["a"])))
    t.start()
    while lf._align_jobs.empty():
        pass
    frame = AudioFrame(samples=np.zeros(16000 * 8, np.float32), t_start=0.0, t_end=8.0)
    lf.side_pass([frame], 8.0)
    t.join(2)
    assert lf.scorer_steps == 1 and got == [[0.5]]


def test_without_a_scorer_that_aligns_there_is_no_alignment():
    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True)
    assert lf.align_words(np.zeros(16000), ["a"]) is None


def test_learning_stays_off_without_beat_this(monkeypatch):
    from auto_presenter import server_follower
    from auto_presenter.music import beat_this

    monkeypatch.setattr(beat_this, "python_path", lambda: None)
    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True)
    server_follower.start_service_learning(lf)
    assert lf.learner is None


# ---------------------------------------------------------------- lifecycle


def test_an_alignment_nobody_ran_is_not_run_later():
    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True)

    class Scorer:
        ran = 0

        def word_starts(self, audio, words):
            Scorer.ran += 1
            return [0.0]

    lf.scorer = Scorer()
    assert lf.align_words(np.zeros(16000), ["a"], timeout=0.05) is None  # nobody serviced it
    lf._run_align_job()  # the ASR thread comes back
    assert Scorer.ran == 0


def test_alignments_wait_while_the_pipeline_is_behind():
    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True)
    lf.load_level = 1
    lf._align_jobs.put(
        (
            np.zeros(16000),
            ["a"],
            [],
            __import__("threading").Event(),
            __import__("threading").Event(),
        )
    )
    lf._run_align_job()
    assert not lf._align_jobs.empty()


def test_detached_followers_stop_asking_for_alignments():
    lf = build_live_followers([(7, SLIDES, SECTIONS, None)], without_sheet=True)
    lf.scorer = type("S", (), {"word_starts": lambda self, a, w: [0.0]})()
    lf.detach()
    assert lf.align_words(np.zeros(16000), ["a"], timeout=5.0) is None


def test_the_audio_cap_trims_the_oldest_audio_only(monkeypatch):
    monkeypatch.setattr(learn, "MAX_AUDIO_S", 10.0)
    monkeypatch.setattr(learn, "TRIM_STEP_S", 2.0)
    lr, _ = _learner(aligned=[])
    for k in range(25):
        lr.on_audio(7, float(k), np.zeros(learn.SR, np.float32))
        lr._audio(lr.songs[7], float(k), float(k) + 1.0)  # merges, as ticks do
    s = lr.songs[7]
    # trimmed to the cap in steps, the newest audio kept, none lost in between
    assert 10 * learn.SR <= s.audio_n <= 12 * learn.SR
    assert s.audio_t0 + s.audio_n / learn.SR == pytest.approx(25.0)


def test_a_learner_hands_a_song_on_after_a_setlist_edit():
    old, _ = _learner(aligned=[])
    words, end = _sing([1, 3, 1, 3])
    _feed(old, words, end + 5)
    assert old.tick(7, end + 5)
    new, _ = _learner(aligned=[])
    new.songs[2] = new.songs.pop(7)  # the song moved in the setlist
    new.songs[2].item_idx = 2
    new.adopt(2, old.songs[7])
    assert new.songs[2].own and new.songs[2].item_idx == 2
    assert new.tick(2, end + 6)  # published again to the new followers
