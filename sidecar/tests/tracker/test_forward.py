"""The dense forward filter: does the recursion do what density is meant to buy?"""

from __future__ import annotations

from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.index.library import build_index
from auto_presenter.protocol import SetlistContent, SetlistItemContent
from auto_presenter.tracker.forward import ForwardConfig, ForwardFilter

VERSE = "all to jesus i surrender all to him i freely give"
CHORUS = "i surrender all i surrender all"
BRIDGE = "when the storm is raging all around me"


def _filter(*slides: str, sections: list[str] | None = None, **cfg) -> ForwardFilter:
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=0,
                song_id="s",
                slides=[s.split() for s in slides],
                slide_sections=sections or [f"sec:{i}" for i in range(len(slides))],
            )
        ]
    )
    return ForwardFilter(setlist=build_index(content), config=ForwardConfig(**cfg))


def _say(filt: ForwardFilter, text: str, t: float = 1.0):
    words = [Word(text=w, t_start=t, t_end=t) for w in text.split()]
    return filt.update(TranscriptEvent(words=words, is_final=True, segment_id=0))


def test_position_advances_as_the_expected_words_arrive():
    f = _filter(VERSE, CHORUS)
    start = f.belief.position
    b = _say(f, "all to jesus i surrender")
    assert b.slide_idx == 0
    assert b.position > start


def test_it_follows_the_band_into_the_next_slide():
    f = _filter(VERSE, CHORUS)
    _say(f, VERSE)
    b = _say(f, CHORUS)
    assert b.slide_idx == 1


def test_a_repeat_is_a_transition_not_a_search():
    """The point of the whole formulation: no lost state, no relocalization.

    The band sings the bridge, then jumps back to the chorus. Nothing has to
    notice a loss first — the chorus states carry mass every step, so the
    evidence simply moves the posterior there.
    """
    f = _filter(VERSE, CHORUS, BRIDGE)
    _say(f, VERSE)
    _say(f, CHORUS)
    assert _say(f, BRIDGE).slide_idx == 2
    b = _say(f, CHORUS)
    assert b.slide_idx == 1, "did not follow the band back to the chorus"


def test_identical_slides_pool_into_the_display_marginal():
    """Which copy of a repeated chorus is showing does not matter; that the
    tracker is *sure it is the chorus* does."""
    f = _filter(VERSE, CHORUS, CHORUS, CHORUS)
    _say(f, VERSE)
    b = _say(f, CHORUS)
    assert b.display_marginal > b.marginal
    assert b.display_marginal > 0.5, (
        f"pooled confidence {b.display_marginal:.2f} — mass split across copies"
    )


def test_unexplained_words_do_not_drive_the_posterior_forward():
    """Evidence, not motion. Words that match nothing should leave the belief
    roughly where it was rather than marching it through the song."""
    f = _filter(VERSE, CHORUS)
    _say(f, "all to jesus")
    before = f.belief
    b = _say(f, "purple bicycle quantum sandwich")
    assert b.slide_idx == before.slide_idx
    assert b.position - before.position < 0.35
    assert b.evidence < 0.5


def test_belief_is_cached_but_never_stale_across_two_updates():
    """`_snapshot()` is memoized (profiled at ~13% of matcher time, almost
    all of it recomputing the same posterior three times per update) — this
    pins that the cache never leaks a previous update's evidence or
    position into the next one."""
    f = _filter(VERSE, CHORUS)
    first = _say(f, VERSE)
    second = _say(f, CHORUS)
    assert second.slide_idx != first.slide_idx or second.position != first.position
    assert f.belief is second  # cache hit returns the same instance, not a stale one


def test_an_operator_commit_collapses_the_posterior():
    f = _filter(VERSE, CHORUS, BRIDGE)
    _say(f, VERSE)
    f.commit_slide(0, 2)
    b = f.belief
    assert b.slide_idx == 2
    assert b.marginal == 1.0


def test_it_is_deterministic():
    """No sampling, so no seed — which is why one run is the answer rather
    than the median of eight."""
    runs = []
    for _ in range(2):
        f = _filter(VERSE, CHORUS, BRIDGE)
        _say(f, VERSE)
        _say(f, CHORUS)
        b = _say(f, BRIDGE)
        runs.append((b.slide_idx, round(b.marginal, 12), round(b.position, 12)))
    assert runs[0] == runs[1]


def test_a_word_cannot_absorb_observations_indefinitely():
    """The duration model, and the failure it exists for.

    With a geometric self-loop the stay probability never changes, so a state
    that matches one word decently can sit there forever without ever being
    tested against the *next* word. Traced on King of My Heart: the filter held
    position 0.02-0.14 of the chorus for ten observations while the band sang a
    verse, because "are" scores 0.90 against "you're" under Double Metaphone.

    Here the stay probability decays with dwell, so mass is pushed forward.
    """
    f = _filter("alpha bravo charlie delta echo", "zulu yankee xray")
    _say(f, "alpha")
    positions = []
    for _ in range(4):
        positions.append(_say(f, "alpha").position)

    assert positions == sorted(positions), f"position went backwards: {positions}"
    assert positions[-1] > positions[0], (
        f"repeating one word never advanced the belief: {positions}"
    )


def test_the_duration_bound_is_what_moves_it():
    """Same input, geometric dwell — the belief should sit still instead."""
    geometric = _filter("alpha bravo charlie delta echo", "zulu yankee xray",
                        stay_decay=1.0)
    _say(geometric, "alpha")
    start = geometric.belief.position
    for _ in range(4):
        _say(geometric, "alpha")
    stuck = geometric.belief.position - start

    bounded = _filter("alpha bravo charlie delta echo", "zulu yankee xray")
    _say(bounded, "alpha")
    start = bounded.belief.position
    for _ in range(4):
        _say(bounded, "alpha")
    moved = bounded.belief.position - start

    assert moved > stuck, (
        f"duration bound advanced {moved:.3f} against geometric {stuck:.3f}"
    )


def test_engine_belief_reflects_an_operator_override_immediately():
    """An override moves the tracker with no audio event to refresh the cache.

    `ParticleFilterEngine.belief` serves a snapshot taken during `update`. With
    a particle cloud that snapshot shares its `Particle` objects with the
    filter, so `commit_slide` mutating them in place hides the staleness. A
    dense posterior builds fresh particles each read, so the stale snapshot is
    genuinely stale: the WS broadcast and the inspector would show where the
    tracker was before the operator spoke, until the next transcript event up
    to a second later.
    """
    from auto_presenter.asr.types import TranscriptEvent, Word
    from auto_presenter.decision.engine import ParticleFilterEngine
    from auto_presenter.decision.scheduler import (
        LeadTimeScheduler,
        LeadTimeSchedulerConfig,
    )
    from auto_presenter.index.library import build_index
    from auto_presenter.protocol import SetlistContent, SetlistItemContent
    from auto_presenter.tracker.forward import ForwardFilter, ForwardTracker

    indexed = build_index(
        SetlistContent(
            items=[
                SetlistItemContent(
                    item_idx=0,
                    song_id="s",
                    slides=[VERSE.split(), CHORUS.split(), BRIDGE.split()],
                    slide_sections=["verse:1", "chorus:1", "bridge:1"],
                )
            ]
        )
    )
    engine = ParticleFilterEngine(
        filter=ForwardTracker(filter=ForwardFilter(setlist=indexed)),
        scheduler=LeadTimeScheduler(
            setlist=indexed,
            config=LeadTimeSchedulerConfig(fixed_lead_time_ms=300, marginal_threshold=0.4),
        ),
    )
    engine.update(
        TranscriptEvent(
            words=[Word(text=w, t_start=1.0, t_end=1.0) for w in VERSE.split()],
            is_final=True,
            segment_id=0,
        )
    )
    elsewhere = 2 if engine.belief.top_hypothesis[1] != 2 else 1
    engine.commit_external(0, elsewhere)
    assert engine.belief.top_hypothesis[1] == elsewhere


# --- what the live run exposed -----------------------------------------------
#
# Both of these shipped as constants, and both looked harmless until the app was
# actually sung to. Neither shows up in wrong-slide rate — they degrade timing
# and the operator's trust in the display, which is why they survived so long.


def _sing(filt: ForwardFilter, text: str, wps: float, t0: float = 0.0) -> float:
    """Feed words one at a time at a fixed tempo, returning the end time."""
    t = t0
    for word in text.split():
        t += 1.0 / wps
        filt.update(
            TranscriptEvent(
                words=[Word(text=word, t_start=t, t_end=t)],
                is_final=True,
                segment_id=0,
            )
        )
    return t


def test_rate_is_nominal_until_there_is_enough_history():
    """One word of quantisation must not be allowed to set the tempo."""
    f = _filter(VERSE, CHORUS)
    assert f.rate_wps == f.config.nominal_rate_wps
    _sing(f, "all to jesus", wps=2.0)
    assert f.rate_wps == f.config.nominal_rate_wps


def test_rate_tracks_the_tempo_actually_sung():
    """It read a constant 1.00 live while Amazing Grace was sung at 0.79 w/s,
    so the scheduler's `(1 - position) * n_words / rate` projection was
    meaningless and anticipation never fired."""
    slow = _filter(VERSE, CHORUS)
    _sing(slow, VERSE, wps=0.8)
    assert 0.55 < slow.rate_wps < 1.05

    fast = _filter(VERSE, CHORUS)
    _sing(fast, VERSE + " " + CHORUS, wps=2.5)
    assert fast.rate_wps > slow.rate_wps * 1.5


def test_crossing_a_slide_boundary_does_not_read_as_going_backwards():
    """Position is per-slide and resets at a boundary; the rate estimate has to
    measure progress along the song or it would go negative every transition."""
    f = _filter(VERSE, CHORUS, BRIDGE)
    t = _sing(f, VERSE, wps=1.5)
    _sing(f, CHORUS, wps=1.5, t0=t)
    assert f.rate_wps > 0.0


def test_the_tracker_is_not_lost_while_it_is_sure_of_the_slide():
    """The ESS heuristic the server falls back to is shaped for a particle
    cloud, where low ESS means degeneracy. On a dense posterior it means the
    opposite. Live, that reported "lost" on 99% of updates while the right
    slide sat on top at marginal 0.88-0.98."""
    from auto_presenter.tracker.forward import ForwardTracker

    f = _filter(VERSE, CHORUS, BRIDGE)
    tracker = ForwardTracker(filter=f)
    _say(f, VERSE)
    assert f.belief.display_marginal > tracker.lost_below
    assert tracker.is_lost is False


def test_words_that_match_nothing_do_not_raise_the_alarm():
    """Deliberate. An instrumental break, crowd noise, or the preacher talking
    all produce words no slide explains, and the posterior correctly stays
    parked where it was — concentrated, because nothing moved it. The right
    thing to show then is the current slide, not a warning."""
    from auto_presenter.tracker.forward import ForwardTracker

    tracker = ForwardTracker(filter=_filter(VERSE, CHORUS, BRIDGE))
    _say(tracker.filter, VERSE)
    for _ in range(6):
        _say(tracker.filter, "gravel switchback tungsten oyster")
    assert tracker.is_lost is False


def test_lost_is_exactly_pooled_confidence_below_the_threshold():
    """The signal is calibrated on real audio, not here — replaying the six
    approved goldens, `display_marginal < 0.35` fires on 2.2% of updates where
    the top slide was correct and catches 18.4% of the updates where it was
    not. (Evidence was measured as the alternative and is near-chance: at 0.50
    it fires on 92.6% of right and 92.9% of wrong.) What this test pins is that
    the tracker reports that quantity and nothing else.
    """
    from auto_presenter.tracker.forward import ForwardTracker

    f = _filter(VERSE, CHORUS, BRIDGE)
    _say(f, VERSE)
    confidence = f.belief.display_marginal

    assert ForwardTracker(filter=f, lost_below=confidence - 0.01).is_lost is False
    assert ForwardTracker(filter=f, lost_below=confidence + 0.01).is_lost is True


# --- following the band to another song -------------------------------------
#
# Reported from live use: singing whole verses of another song in the set never
# moved the display. That was not mistuning — the filter's state was scoped to
# one song, so no cross-song hypothesis existed for evidence to favour.

OTHER = "i have decided to follow jesus no turning back no turning back"
OTHER2 = "the world behind me the cross before me no turning back"


def _two_songs(**cfg):
    content = SetlistContent(
        items=[
            SetlistItemContent(item_idx=0, song_id="a",
                               slides=[s.split() for s in (VERSE, CHORUS)],
                               slide_sections=["v1", "c1"]),
            SetlistItemContent(item_idx=1, song_id="b",
                               slides=[s.split() for s in (OTHER, OTHER2)],
                               slide_sections=["v1", "v2"]),
        ]
    )
    return ForwardFilter(setlist=build_index(content), config=ForwardConfig(**cfg))


def test_it_follows_the_band_into_a_different_song():
    f = _two_songs()
    _say(f, VERSE)
    assert f.belief.item_idx == 0
    for _ in range(3):
        _say(f, OTHER)
    assert f.belief.item_idx == 1, "never left the first song"


def test_it_stays_put_while_the_current_song_is_being_sung():
    """Leaving must cost evidence, not happen on drift."""
    f = _two_songs()
    for _ in range(4):
        _say(f, VERSE)
        _say(f, CHORUS)
    assert f.belief.item_idx == 0


def test_one_song_in_the_setlist_behaves_exactly_as_before():
    """With nowhere to go, the cross-song term must be inert — the single-song
    measurements have to stay comparable."""
    f = _filter(VERSE, CHORUS)
    _say(f, VERSE)
    before = dict(f._alpha)
    g = _filter(VERSE, CHORUS, cross_song=0.5)
    _say(g, VERSE)
    assert dict(g._alpha) == before


def test_an_operator_override_moves_songs_too():
    f = _two_songs()
    _say(f, VERSE)
    f.commit_slide(1, 0)
    assert f.belief.item_idx == 1
    assert f.belief.slide_idx == 0


def test_a_reading_between_songs_is_never_a_place_to_go():
    """A passage or text item in the service arrives as one wordless slide
    (`setlistContent.ts`) so the songs after it keep their place. Nothing
    sung can lead there, and the band moving on lands on the next song."""
    content = SetlistContent(
        items=[
            SetlistItemContent(item_idx=0, song_id="a",
                               slides=[s.split() for s in (VERSE, CHORUS)],
                               slide_sections=["v1", "c1"]),
            SetlistItemContent(item_idx=1, song_id="item_reading",
                               slides=[[]], slide_sections=["other:0"]),
            SetlistItemContent(item_idx=2, song_id="b",
                               slides=[s.split() for s in (OTHER, OTHER2)],
                               slide_sections=["v1", "v2"]),
        ]
    )
    f = ForwardFilter(setlist=build_index(content))
    assert [i for i, _ in f._openings()] == [0, 2]
    _say(f, VERSE)
    # The operator puts the reading up; the app tells the engine the next
    # song's start (`serviceItems.aiAnchor`), and the band starts it.
    f.commit_slide(2, 0)
    for _ in range(2):
        _say(f, OTHER)
    assert f.belief.item_idx == 2
    assert all(state[0] != 1 for state in f._alpha)


def test_a_reading_with_words_is_followed_but_never_jumped_into():
    """With read-along on, a passage arrives with its words and `spoken`.
    Put up by the operator, it is followed slide by slide; quoted from a
    song (or a sermon), it is not a place to jump to."""
    reading = ["for god so loved the world that he gave his only begotten son",
               "that whosoever believeth in him should not perish"]
    content = SetlistContent(
        items=[
            SetlistItemContent(item_idx=0, song_id="a",
                               slides=[s.split() for s in (VERSE, CHORUS)],
                               slide_sections=["v1", "c1"]),
            SetlistItemContent(item_idx=1, song_id="item_reading", spoken=True,
                               slides=[[]] + [s.split() for s in reading],
                               slide_sections=["other:0", "other:1", "other:1"]),
        ]
    )
    f = ForwardFilter(setlist=build_index(content))
    assert [i for i, _ in f._openings()] == [0]
    _say(f, VERSE)
    f.commit_slide(0, 0)
    for _ in range(2):
        _say(f, reading[0])
    assert f.belief.item_idx == 0, "jumped into a reading on its own"

    f.commit_slide(1, 0)  # the operator puts the reading up
    _say(f, reading[0])
    _say(f, reading[1])
    assert f.belief.item_idx == 1
    assert f.belief.slide_idx == 2


def test_identical_slides_pool_only_within_one_song():
    """Two songs can share a chorus. Showing either is not the same thing —
    they are different slides in different songs."""
    same = "holy holy holy lord god almighty"
    content = SetlistContent(
        items=[
            SetlistItemContent(item_idx=0, song_id="a",
                               slides=[VERSE.split(), same.split()],
                               slide_sections=["v1", "c1"]),
            SetlistItemContent(item_idx=1, song_id="b",
                               slides=[OTHER.split(), same.split()],
                               slide_sections=["v1", "c1"]),
        ]
    )
    f = ForwardFilter(setlist=build_index(content))
    _say(f, VERSE)
    _say(f, same)
    b = f.belief
    assert b.display_marginal <= 1.0
    assert b.marginal <= b.display_marginal


def test_a_multi_song_set_starts_without_committing_to_song_one():
    """A service that opens on the *second* song should find it immediately.

    `_reset` used to assert probability 1.0 on `current_item_idx`'s opening,
    but nothing tells the filter where the service starts — the session-start
    message carries no item index. So a set opening on any song but the first
    began fully committed to the wrong one and had to escape at `cross_song`
    (0.02) per observation. Observed live on a "The Blood" / "Nothing But The
    Blood" medley that opened on song two: ~14 s stranded on song one.
    """
    from auto_presenter.index.library import build_index
    from auto_presenter.protocol import SetlistContent, SetlistItemContent

    indexed = build_index(
        SetlistContent(
            items=[
                SetlistItemContent(
                    item_idx=0,
                    song_id="a",
                    slides=[[], "amazing grace how sweet the sound".split()],
                ),
                SetlistItemContent(
                    item_idx=1,
                    song_id="b",
                    slides=[[], "nothing but the blood of jesus".split()],
                ),
            ]
        )
    )
    filt = ForwardFilter(setlist=indexed)

    # Before any evidence, neither song is favoured.
    assert filt.belief.marginal < 0.9

    for n, word in enumerate("nothing but the blood".split(), start=1):
        filt.update(
            TranscriptEvent(
                words=[Word(text=word, t_start=n - 1.0, t_end=float(n))],
                is_final=True,
                segment_id=n,
            )
        )
    assert filt.belief.item_idx == 1


def test_a_single_song_set_is_unaffected_by_the_opening_prior():
    """The prior only spreads across songs that exist — one song, one opening,
    so the long-tuned single-song behaviour is byte-identical."""
    from auto_presenter.index.library import build_index
    from auto_presenter.protocol import SetlistContent, SetlistItemContent

    indexed = build_index(
        SetlistContent(
            items=[
                SetlistItemContent(
                    item_idx=0,
                    song_id="a",
                    slides=[[], "amazing grace how sweet the sound".split()],
                )
            ]
        )
    )
    filt = ForwardFilter(setlist=indexed)
    assert filt._alpha == {(0, 1, 0, 0): 1.0}


# --- confidence is about the slide, not about which song ----------------------


def _two_song_filter():
    """Two songs whose openings share no words, so mass placement is clean."""
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=0, song_id="a",
                slides=[[], ["alpha", "bravo", "charlie"], ["delta", "echo", "foxtrot"]],
            ),
            SetlistItemContent(
                item_idx=1, song_id="b",
                slides=[[], ["golf", "hotel", "india"], ["juliet", "kilo", "lima"]],
            ),
        ]
    )
    return ForwardFilter(setlist=build_index(content))


def test_display_marginal_is_not_taxed_by_other_songs():
    """`display_marginal` gates commits and is supposed to answer "how sure am
    I of what to put on screen". Measured across the whole setlist it also
    carried doubt about *which song* — and `cross_song` puts mass on every
    other song's opening every observation, so that doubt never cleared. On a
    live run the correct slide held 0.15 while two other songs' openings held
    0.24 between them, and the commit stayed blocked under the 0.4 bar."""
    f = _two_song_filter()
    for text in ("alpha", "bravo", "charlie", "delta", "echo"):
        _say(f, text)

    b = f.belief
    assert b.item_idx == 0
    # Mass genuinely sitting on other songs must not reduce our confidence in
    # the slide, which is a question about this song.
    other = sum(m for (i, _s, _w, _d), m in f._alpha.items() if i != b.item_idx)
    assert other > 0.0, "no cross-song mass, so this proves nothing"
    assert b.display_marginal > b.marginal


def test_display_marginal_still_reflects_doubt_within_the_song():
    """Conditioning must not make it unconditionally confident — a belief split
    between two slides of the *same* song should still read as split."""
    f = _two_song_filter()
    _say(f, "alpha")
    b = f.belief
    assert b.display_marginal <= 1.0
    # Nothing has disambiguated slide 1 from slide 2 yet.
    assert b.display_marginal < 1.0


def test_leaving_a_song_is_still_possible():
    """`cross_song` was lowered, not removed. Before it existed the tracker
    could not leave a song at all, reported live as singing whole verses of
    another song and watching nothing happen."""
    f = _two_song_filter()
    for text in ("alpha", "bravo", "charlie"):
        _say(f, text)
    assert f.belief.item_idx == 0

    for text in ("golf", "hotel", "india", "juliet", "kilo", "lima"):
        _say(f, text)
    assert f.belief.item_idx == 1
