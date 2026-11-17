"""Listening for the other songs' opening lines (`follower.openings`)."""

from __future__ import annotations

import numpy as np

from auto_presenter.follower.openings import MIN_GAIN, OpeningsWatch
from auto_presenter.follower.scorer import ScoreResult

SONG_A = [[], ["our", "god", "is", "a", "mighty", "god", "he", "reigns"]]
SONG_B = [
    [],
    ["praise", "god", "from", "whom", "all", "blessings", "flow"],
    ["praise", "him", "all", "creatures", "here", "below"],
]


class FakeScorer:
    """Scores a span by a table; anything else gets `base`."""

    def __init__(self, table: dict[str, float], base: float = -6.0):
        self.table, self.base = table, base

    def score(self, audio, spans, place_among=None):
        loud = float(np.abs(audio).max()) > 0.1  # silence/noise vs "the band"
        sc = np.array([self.table.get(s, self.base) if loud else self.base for s in spans])
        return ScoreResult(scores=sc, best=int(np.argmax(sc)), place_s=0.0)


def watch(table, base=-6.0):
    w = OpeningsWatch.build([(0, SONG_A), (1, SONG_B)], FakeScorer(table, base))
    assert w is not None
    w.warm_up()
    return w


def test_a_clearly_sung_opening_line_of_another_song_is_heard():
    w = watch({"praise god from whom all blessings": -1.0})
    heard = w.listen(np.ones(16000 * 6, np.float32), 100.0, current_item=0)
    assert heard is not None and heard.item_idx == 1 and heard.slide_idx == 1
    assert heard.gain >= MIN_GAIN


def test_a_line_the_model_likes_anyway_is_not_heard():
    """Gain over the span's own score on silence, not its raw score."""
    w = watch({})
    w.prior["praise god from whom all blessings"] = -1.5  # an LM favourite
    w.scorer.table["praise god from whom all blessings"] = -1.0
    assert w.listen(np.ones(16000 * 6, np.float32), 100.0, current_item=0) is None


def test_the_current_song_heard_as_well_blocks_a_switch():
    w = watch({"praise god from whom all blessings": -1.0, "our god is a mighty god": -0.8})
    assert w.listen(np.ones(16000 * 6, np.float32), 100.0, current_item=0) is None


def test_on_a_song_the_watch_doesnt_know_nothing_is_proposed():
    """A song added mid-service: no own spans to beat, so no switching away
    on one loosely matching line (2026-10-17, What a Beautiful Name)."""
    w = watch({"praise god from whom all blessings": -1.0})
    assert w.listen(np.ones(16000 * 6, np.float32), 100.0, current_item=9) is None


def test_a_shared_line_does_not_cut_away_while_the_current_song_is_heard():
    """Worship songs share phrases. Live, «the son of god» (2.8 against the
    current song's 1.1) cut away from the song being sung; a line must beat
    the current song by `MARGIN` (2.0), not merely clear the gain bar."""
    from auto_presenter.follower.openings import MARGIN

    w = watch({"praise god from whom": -1.0, "our god is a mighty god": -6.0 + 3.5})
    w.listen(np.ones(16000 * 6, np.float32), 100.0, current_item=0)
    current = w.last[2]
    heard = w.last[3][1][0]
    assert heard >= MIN_GAIN and heard - current < MARGIN
    assert w.listen(np.ones(16000 * 6, np.float32), 102.0, current_item=0) is None


def test_a_song_the_watch_does_not_know_proposes_nothing():
    """A song imported mid-service, before the watch is rebuilt: its own lines
    are unknown, so "beats the current song" would mean beating nothing."""
    w = watch({"praise god from whom all blessings": -1.0})
    assert w.listen(np.ones(16000 * 6, np.float32), 100.0, current_item=8) is None


def test_the_song_on_screen_is_never_proposed():
    w = watch({"praise god from whom all blessings": -1.0})
    assert w.listen(np.ones(16000 * 6, np.float32), 100.0, current_item=1) is None


def test_one_song_has_nothing_to_listen_for_and_passes_keep_their_cadence():
    assert OpeningsWatch.build([(0, SONG_A)], FakeScorer({})) is None
    w = watch({})
    assert w.due(10.0)
    w.listen(np.ones(16000 * 6, np.float32), 10.0, current_item=0)
    assert not w.due(11.0) and w.due(12.0)  # every CADENCE_S (2 s)


def test_with_no_band_playing_a_song_change_needs_a_second_hearing(monkeypatch):
    import asyncio

    from auto_presenter import server_follower as sf
    from auto_presenter.follower.openings import SongHeard
    from auto_presenter.music.band import BandPresence
    from auto_presenter.server_state import _state

    class Matcher:
        current_item_idx = 0
        committed = None

        def commit_external(self, item, slide):
            self.committed = (item, slide)
            self.current_item_idx = item

    class Director:
        mode = "active"

        def __init__(self):
            self.decisions = []

        def on_decision(self, d):
            self.decisions.append((d.item_idx, d.slide_idx))

    m, d, band = Matcher(), Director(), BandPresence()
    monkeypatch.setattr(_state, "matcher", m)
    monkeypatch.setattr(_state, "director", d)
    monkeypatch.setattr(_state, "band", band)
    monkeypatch.setattr(_state, "openings_pending", None)

    def heard(t, item=1):
        return SongHeard(t, item, 1, "praise god from whom all blessings", 3.5, 0.2)

    talk = np.zeros(25)
    talk[-1] = 1.0  # all "no chord"
    band.observe(100.0, talk)
    asyncio.run(sf.apply_song_heard(heard(100.0)))  # a lyric quoted once in a sermon
    assert m.committed is None and d.decisions == []
    asyncio.run(sf.apply_song_heard(heard(110.0)))  # again, but too long after
    assert m.committed is None
    asyncio.run(sf.apply_song_heard(heard(112.0)))  # heard again on the next pass
    assert m.committed == (1, 1) and d.decisions == [(1, 1)]

    m.committed, m.current_item_idx = None, 1
    chord = np.zeros(25)
    chord[0] = 1.0
    band.observe(130.0, chord)  # the band is playing: one hearing is enough
    asyncio.run(sf.apply_song_heard(heard(130.0, item=0)))
    assert m.committed == (0, 1)
    asyncio.run(sf.apply_song_heard(heard(132.0, item=0)))  # already on it: nothing more
    assert d.decisions == [(1, 1), (0, 1)]


def test_the_openings_can_ride_along_in_the_followers_pass():
    """Scores handed over from another pass decide the same way as its own."""
    w = watch({"praise god from whom all blessings": -1.0})
    scores = {t: (-1.0 if t == "praise god from whom all blessings" else -6.0) for t in w.wanted(0)}
    heard = w.hear(scores, 50.0, current_item=0)
    assert heard is not None and heard.item_idx == 1


def test_a_long_setlist_takes_turns_and_always_hears_the_next_song(monkeypatch):
    from auto_presenter.follower import openings as op

    monkeypatch.setattr(op, "MAX_ENTRY_SPANS", 30)
    items = [(i, [[], [f"song{i}", "line", "one", "goes", "here", "and", "on"]]) for i in range(12)]
    watch = op.OpeningsWatch(songs=op._songs(items), scorer=None)
    per_song = {s.item_idx: set(s.entry) for s in watch.songs}
    seen: set[int] = set()
    for _ in range(12):
        got = set(watch.wanted(3))
        heard = {i for i, e in per_song.items() if i != 3 and e <= got}
        assert 4 in heard  # the next song, every pass
        assert sum(len(per_song[i]) for i in heard) <= 30 + len(per_song[4])
        seen |= heard
    assert seen == set(per_song) - {3}  # every other song gets its turn


def test_priors_are_scored_once_per_span_text():
    from types import SimpleNamespace

    from auto_presenter.follower import openings as op

    calls = []

    class Scorer:
        repo = "test-priors"

        def score(self, audio, texts):
            calls.append(list(texts))
            return SimpleNamespace(scores=[-1.0] * len(texts))

    items = [(0, [[], ["a", "b", "c", "d", "e"]]), (1, [[], ["f", "g", "h", "i", "j"]])]
    op._PRIORS.pop("test-priors", None)
    op.OpeningsWatch.build(items, Scorer()).warm_up()
    n = len(calls)
    op.OpeningsWatch.build(items, Scorer()).warm_up()  # the setlist edited, same songs
    assert len(calls) == n


def test_landing_mid_song_needs_a_slightly_clearer_line():
    """Its first sung slide clears the usual bars; a later slide needs
    `MID_SONG_NUDGE` more (gain and lead) — "really sure, this unusual spot?"."""
    from auto_presenter.follower.openings import MARGIN, MID_SONG_NUDGE

    just = -6.0 + max(MIN_GAIN, MARGIN) + MID_SONG_NUDGE / 2  # clears the bars, not the nudge
    first = watch({"praise god from whom all blessings": just})
    heard = first.listen(np.ones(16000 * 6, np.float32), 100.0, current_item=0)
    assert heard is not None and heard.slide_idx == 1
    mid = watch({"praise him all creatures here": just})
    assert mid.listen(np.ones(16000 * 6, np.float32), 100.0, current_item=0) is None
    mid.mid_song_nudge = 0.0
    assert mid.listen(np.ones(16000 * 6, np.float32), 102.0, current_item=0) is not None


def test_a_song_change_that_is_then_not_heard_blanks_until_it_is(monkeypatch):
    from auto_presenter import server_follower as sf
    from auto_presenter.server_state import _state

    for k, v in (("last_ai_item", 0), ("song_probation", None), ("song_unconfirmed", False),
                 ("tunables", None)):
        monkeypatch.setattr(_state, k, v)
    # a real change: the new song keeps scoring
    assert sf.note_ai_move(1, 100.0) is False
    for t, g in ((102, 4.0), (104, 1.6), (106, 1.4), (108, 2.3), (110, 1.6), (112, 1.8), (114, 1.8)):
        assert sf.probation_step(1, g, t) is None
    assert sf.probation_step(1, 1.0, 118) is None and not _state.song_unconfirmed
    # a false one: the line that made it, then nothing
    sf.note_ai_move(2, 200.0)
    verdict = [sf.probation_step(2, g, t) for t, g in
               ((201, 2.6), (203, 1.2), (205, 0.6), (207, 0.8), (209, 1.5), (211, 0.3), (213, 0.0), (217, 0.2))]
    assert verdict[-1] is True and _state.song_unconfirmed
    assert sf.probation_step(2, 1.9, 219) is None          # not yet clearly heard
    assert sf.probation_step(2, 3.0, 221) is False         # now it is: the blank lifts
    # a person's choice is never put on probation, and lifts a blank
    sf.note_ai_move(3, 300.0)
    _state.song_unconfirmed = True
    assert sf.note_operator_move(3) is True
    assert _state.song_probation is None and not _state.song_unconfirmed
