"""Tests for the `MatchingEngine` Protocol + `ParticleFilterEngine`."""

from __future__ import annotations

import pytest

from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.engine import MatchingEngine, ParticleFilterEngine
from auto_presenter.decision.scheduler import (
    LeadTimeScheduler,
    LeadTimeSchedulerConfig,
)
from auto_presenter.index.library import build_index
from auto_presenter.protocol import SetlistContent, SetlistItemContent
from auto_presenter.tracker.forward import ForwardFilter, ForwardTracker


def _make_engine() -> ParticleFilterEngine:
    setlist = build_index(
        SetlistContent(
            items=[
                SetlistItemContent(
                    item_idx=0,
                    song_id="song_a",
                    slides=[
                        ["amazing", "grace", "how", "sweet"],
                        ["that", "saved", "a", "wretch"],
                    ],
                    slide_sections=["verse:1", "verse:1"],
                )
            ]
        )
    )
    pf = ForwardTracker(filter=ForwardFilter(setlist=setlist))
    scheduler = LeadTimeScheduler(
        setlist=setlist,
        config=LeadTimeSchedulerConfig(fixed_lead_time_ms=500, marginal_threshold=0.3),
    )
    return ParticleFilterEngine(filter=pf, scheduler=scheduler)


def _event(words):
    return TranscriptEvent(
        words=[Word(text=t, t_start=s, t_end=e) for (t, s, e) in words],
        is_final=True,
    )


def test_engine_satisfies_the_protocol():
    def use(_engine: MatchingEngine) -> None:
        return None

    use(_make_engine())


def test_engine_proxies_current_item_idx():
    engine = _make_engine()
    assert engine.current_item_idx == 0
    engine.set_current_item(0)  # same item — noop
    assert engine.current_item_idx == 0


def test_engine_update_returns_decision_when_scheduler_fires():
    """Drive enough perfect words to push the cloud near the end of slide 0."""
    engine = _make_engine()
    # Slide 0: "amazing grace how sweet" (4 words). Feed the words at
    # a fast tempo so the particles' position crosses the scheduler's
    # lead-time threshold while the cloud is still concentrated on
    # slide 0.
    words = [
        ("amazing", 0.0, 0.4),
        ("grace", 0.4, 0.8),
        ("how", 0.8, 1.2),
        ("sweet", 1.2, 1.6),
        ("sweet", 1.6, 2.0),
        ("sweet", 2.0, 2.4),
        ("sweet", 2.4, 2.8),
    ]
    decision = None
    for w in words:
        d = engine.update(_event([w]))
        if d is not None:
            decision = d
            break
    assert decision is not None
    assert decision.slide_idx == 1


def test_commit_external_re_anchors_filter_and_scheduler():
    engine = _make_engine()
    engine.update(_event([("amazing", 0.0, 0.4)]))
    engine.commit_external(item_idx=0, slide_idx=1)
    # Filter's cloud should be on slide 1.
    assert engine.filter.belief.top_hypothesis[1] == 1
    # Scheduler should not re-fire that same slide if the cloud stays
    # there (re-anchor noted internally).
    decision = engine.update(_event([("that", 1.5, 1.8)]))
    # Whether it fires (0, 2) depends on timing; what we can verify is
    # that if it fires, it isn't (0, 1) again.
    if decision is not None:
        assert decision.slide_idx != 1


def test_commit_external_unknown_slide_is_noop():
    engine = _make_engine()
    engine.commit_external(item_idx=0, slide_idx=99)
    # Filter still on slide 0.
    assert engine.filter.belief.top_hypothesis[1] == 0


def test_set_current_item_through_property_resets_filter():
    engine = _make_engine()
    engine.current_item_idx = 0  # property writer
    # Same item: belief unchanged; current_item_idx still 0.
    assert engine.current_item_idx == 0


def _two_song_index():
    return build_index(
        SetlistContent(
            items=[
                SetlistItemContent(
                    item_idx=0,
                    song_id="song_a",
                    slides=[["amazing", "grace"], ["that", "saved"]],
                    slide_sections=["verse:1", "verse:1"],
                ),
                SetlistItemContent(
                    item_idx=1,
                    song_id="song_b",
                    slides=[["holy", "holy", "holy"]],
                    slide_sections=["verse:1"],
                ),
            ]
        )
    )


def test_reindex_reanchors_to_current_item():
    engine = _make_engine()
    new_index = _two_song_index()
    engine.reindex(new_index, current_item_idx=1)
    assert engine.current_item_idx == 1
    # Re-initialised against the new index, with item 1's opening among the
    # leaders. Not necessarily alone: the forward filter keeps a standing
    # chance of every song's opening (see `note_displayed`).
    best = max(m for _i, _s, m in engine.filter.belief.top_3)
    assert (1, 0, best) in engine.filter.belief.top_3
    # Scheduler now references the new index (its setlist was swapped).
    assert engine.scheduler.setlist is new_index


def test_reindex_clamps_out_of_range_anchor():
    engine = _make_engine()
    engine.reindex(_two_song_index(), current_item_idx=99)
    assert engine.current_item_idx == 1  # clamped to last item


def _three_slide_engine() -> ParticleFilterEngine:
    setlist = build_index(
        SetlistContent(
            items=[
                SetlistItemContent(
                    item_idx=0,
                    song_id="song_a",
                    slides=[
                        ["amazing", "grace", "how", "sweet", "the", "sound"],
                        ["that", "saved", "a", "wretch", "like", "me"],
                        ["i", "once", "was", "lost", "but", "now", "am", "found"],
                    ],
                    slide_sections=["verse:1", "verse:1", "verse:1"],
                )
            ]
        )
    )
    pf = ForwardTracker(filter=ForwardFilter(setlist=setlist))
    scheduler = LeadTimeScheduler(
        setlist=setlist,
        config=LeadTimeSchedulerConfig(fixed_lead_time_ms=500, marginal_threshold=0.3),
    )
    return ParticleFilterEngine(filter=pf, scheduler=scheduler)


def test_engine_advances_through_multiple_slides():
    """Regression for the 'only the first transition fires' bug: feeding a
    clean run through three slides should yield decisions advancing the
    display to slide 1 and then slide 2 (not freeze after the first)."""
    engine = _three_slide_engine()
    lines = [
        ["amazing", "grace", "how", "sweet", "the", "sound"],
        ["that", "saved", "a", "wretch", "like", "me"],
        ["i", "once", "was", "lost", "but", "now", "am", "found"],
        # Repeat the last line a few times to give the cloud time to settle.
        ["i", "once", "was", "lost", "but", "now", "am", "found"],
    ]
    t = 0.0
    reached = set()
    for line in lines:
        for w in line:
            engine.update(_event([(w, t, t + 0.4)]))
            t += 0.4
        # capture the engine's committed/displayed slide via belief mode
        reached.add(engine.filter.belief.top_hypothesis[1])
    # The display must have advanced beyond slide 1 — i.e. the second
    # transition fired, not just the first.
    assert max(reached) >= 2


# --- session start -----------------------------------------------------------


def _set_of_seven():
    from auto_presenter.decision.production import build_engine

    songs = [
        ["long", "before", "the", "dawn", "you", "were", "calling", "out", "to", "me"],
        ["all", "to", "jesus", "i", "surrender", "all", "to", "him", "i", "freely", "give"],
        ["we", "gather", "here", "to", "lift", "our", "voices", "in", "the", "light"],
        ["holy", "holy", "holy", "lord", "god", "almighty", "early", "in", "the", "morning"],
        ["on", "a", "hill", "far", "away", "stood", "an", "old", "rugged", "cross", "high"],
        ["alleluia", "alleluia", "for", "the", "lord", "god", "almighty", "reigns"],
        ["amazing", "grace", "how", "sweet", "the", "sound", "that", "saved", "a", "wretch"],
    ]
    content = SetlistContent(
        items=[
            SetlistItemContent(item_idx=i, song_id=f"s{i}", slides=[[], words])
            for i, words in enumerate(songs)
        ]
    )
    return build_engine(build_index(content))


def test_a_stray_token_at_session_start_cannot_pick_a_song():
    """Traced 2026-09-29: Whisper's first output on a near-silent opening was
    a lone "!", and with the screen assumed blank the first commit skipped the
    song-change bar — Amazing Grace went up over Reckless Love. Knowing what
    is displayed puts that first move through the same bar as any other."""
    engine = _set_of_seven()
    # Not song 0: unseeded, a lone "!" commits to song 0's opening at
    # confidence 1.0 — a seven-way tie broken by rounding.
    engine.note_displayed(3, 0)
    assert engine.update(_event([("!", 0.0, 0.9)])) is None


def test_singing_the_displayed_song_still_moves_straight_away():
    engine = _set_of_seven()
    engine.note_displayed(0, 0)
    decision = engine.update(
        _event([(w, 1.0 + i * 0.4, 1.3 + i * 0.4) for i, w in enumerate(
            ["long", "before", "the", "dawn", "you"])])
    )
    assert decision is not None and decision.item_idx == 0
