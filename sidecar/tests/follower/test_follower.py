"""The score follower: reference, online filter, live coordinator, engine routing.

Real lead sheets are copyrighted and not committed; a small synthetic sheet
(two lines of a hymn over four bars, in the stored `leadsheet.omr` shape)
exercises the mechanics. Parity with the spike on the real goldens is
`research/spikes/music_only/exp_online.py` (dev machine).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.follower_engine import FollowerEngine
from auto_presenter.decision.production import build_engine
from auto_presenter.follower import score as sc
from auto_presenter.follower import sheet
from auto_presenter.follower.live import LiveFollowers, SongFollower, build_live_followers
from auto_presenter.follower.online import INTRO_LEAVE, OnlineFollower, SegmentTransition
from auto_presenter.follower.reference import SegmentReference
from auto_presenter.follower.scorer import spans_for
from auto_presenter.follower.spec import SegmentSpec, library_spec
from auto_presenter.index.library import build_index
from auto_presenter.matcher.types import MatcherDecision
from auto_presenter.protocol import SetlistContent, SetlistItemContent
from auto_presenter.server_follower import load_lead_sheets

LINE_1 = ["a", "maz", "ing", "grace", "how", "sweet"]
HYPH_1 = [True, True, False, False, False, False]
LINE_2 = ["the", "sound", "that", "saved", "a", "wretch"]


def _bar(number: int, syllables: list[str], hyph: list[bool], chord: str, midi0: int) -> dict:
    return {
        "number": number,
        "meter": 4.0,
        "notes": [
            {
                "voice": 1,
                "onset": i * (4.0 / len(syllables)),
                "dur": 4.0 / len(syllables),
                "midi": midi0 + i,
                "syllables": {"0": s},
                "hyphen_after": {"0": h},
                "tied_from_prev": False,
            }
            for i, (s, h) in enumerate(zip(syllables, hyph, strict=True))
        ],
        "chords": [{"beat": 0.0, "name": chord}],
        "voltas": [],
        "start_repeat": False,
        "end_repeat": False,
        "segno": False,
        "ds": False,
        "section": "VERSE" if number == 1 else None,
    }


def score_json() -> dict:
    return {
        "key_fifths": 0,
        "bars": [
            _bar(1, LINE_1[:3], HYPH_1[:3], "G", 62),
            _bar(2, LINE_1[3:], HYPH_1[3:], "C", 64),
            _bar(3, LINE_2[:3], [False] * 3, "D", 66),
            _bar(4, LINE_2[3:], [False] * 3, "G", 67),
        ],
    }


SLIDES = [
    [],
    ["amazing", "grace", "how", "sweet"],
    ["the", "sound", "that", "saved", "a", "wretch"],
]
SECTIONS = ["other:0", "verse:1", "verse:2"]


def reference() -> SegmentReference:
    return SegmentReference(sc.from_extract(score_json()), SLIDES, SECTIONS)


# ---------------------------------------------------------------- reference


def test_reference_binds_every_slide_and_starts_with_an_intro():
    ref = reference()
    assert ref.coverage.share == 1.0
    assert ref.slide_of[ref.intro_cell] == 0  # the blank slide
    assert ref.seg_slides == [0, 1, 2]
    assert ref.seg_slides[ref.first_segment] == 1
    # each slide's own words sit on cells of its own segment
    for (si, _j), c in ref.word_cell.items():
        assert ref.slide_of[c] == si


def test_chords_are_scored_in_the_band_key():
    ref = reference()
    g_major = np.zeros(25, np.float32)
    g_major[sheet.chord_class("G")] = 1.0
    a_major = np.zeros(25, np.float32)
    a_major[sheet.chord_class("A")] = 1.0
    bar1 = ref.jump_targets[1]  # first cell of line 1: G in the sheet
    assert ref.chords(g_major)[0, bar1] > 0
    ref.set_key(2)  # the band plays it a tone up: G -> A
    assert ref.chords(a_major)[0, bar1] > 0
    assert ref.chords(g_major)[0, bar1] < 0


def test_key_estimate_finds_a_transposition():
    ref = reference()
    heard = np.zeros((40, 25), np.float32)
    for i, name in enumerate(["A", "D", "E", "A"] * 10):  # G C D G, a tone up
        heard[i, sheet.chord_class(name)] = 1.0
    k, _ = sheet.estimate_key_shift(heard, ref.chord_sheet)
    assert k == 2


def test_unscored_cells_are_neutral_not_penalised():
    ref = reference()
    # two spans scored, one well and one badly; every other cell was not asked about
    vec = ref.scored({(1, 3): -0.5, (2, 5): -3.0})
    assert vec is not None
    good, bad = vec[ref.word_cell[(1, 3)]], vec[ref.word_cell[(2, 5)]]
    unasked = vec[ref.word_cell[(2, 0)]]
    assert bad < unasked < good  # neutral, not lumped in with the worst


def test_a_window_whose_best_match_is_one_word_is_no_evidence():
    ref = reference()
    # a lone first word out-scores every real span: nothing was sung
    assert ref.scored({(1, 0): -2.0, (1, 3): -2.5, (2, 5): -3.0}) is None
    # one-word spans never count, even when a real span wins
    vec = ref.scored({(1, 0): -4.0, (1, 3): -0.5, (2, 5): -3.0})
    assert vec is not None
    assert vec[ref.word_cell[(1, 0)]] == vec[ref.word_cell[(2, 0)]]


def test_a_window_with_no_line_in_it_favours_the_intro():
    ref = reference()
    murky = ref.scored({(1, 3): -4.5, (2, 5): -4.8})
    sung = ref.scored({(1, 3): -0.6, (2, 5): -4.0})
    assert murky is not None and sung is not None
    assert murky[ref.intro_cell] > murky[ref.word_cell[(1, 3)]]
    assert sung[ref.intro_cell] < sung[ref.word_cell[(1, 3)]]


# ---------------------------------------------------------------- filter


def test_the_intro_holds_and_leaks_slowly():
    ref = reference()
    tr = SegmentTransition(ref, 10.0, p_jump=0.03, p_rate=0.02)
    x = np.zeros((len(ref.rates), ref.P), np.float32)
    x[:, ref.intro_cell] = 1.0 / len(ref.rates)
    for _ in range(10):
        x = tr.step(x)
    assert x[:, ref.intro_cell].sum() == pytest.approx((1 - INTRO_LEAVE) ** 10, rel=1e-4)
    # most of what leaked went to the first line's start
    first = ref.jump_targets[ref.first_segment]
    other = ref.jump_targets[2]
    assert x[:, first].sum() > x[:, other].sum() > 0


def test_singing_the_first_line_leaves_the_intro():
    # A band starting mid-song (on a chorus) is measured on real songs in
    # research/spikes/music_only/exp_midstart.py (53/54 lock within 60 s, median
    # 8 s); this two-bar sheet is too short to separate its lines in time.
    ref = reference()
    f = OnlineFollower(ref, lookahead_s=0.0)
    f.anchor(0, 0.0)
    assert f.readout(1.0).slide == 0
    for i, w in enumerate(["amazing", "grace", "how"]):
        f.push_word(2.0 + 0.4 * i, 4.0, w)
    # and the scorer, live, finds line 1 sung well and line 2 not at all
    scored = ref.scored({(1, 3): -0.3, (2, 5): -4.0})
    assert scored is not None
    f.push_scored(3.0, 4.0, scored)
    # out of the intro; on this toy sheet line 1 lasts ~4 s, so by 4.5 s the
    # tempo has carried the band into line 2, which is what it shows
    r = f.readout(4.5)
    assert r.slide in (1, 2)
    assert r.marginal_now[ref.intro_cell] < r.marginal_now[ref.slide_of > 0].sum()


def test_evidence_older_than_the_checkpoint_is_counted_not_placed():
    f = OnlineFollower(reference(), refilter_s=1.0)
    f.anchor(1, 0.0)
    f.readout(10.0)  # checkpoint moves to 9 s
    f.push_word(2.0, 10.5, "grace")
    assert f.late_evidence == 1


def test_identical_slides_tie_to_the_one_already_shown():
    slides = [[], ["holy", "holy"], ["holy", "holy"]]
    data = score_json()
    data["bars"] = [_bar(1, ["ho", "ly", "ho", "ly"], [True, False, True, False], "G", 62)]
    ref = SegmentReference(sc.from_extract(data), slides, ["other:0", "chorus:1", "chorus:1"])
    f = OnlineFollower(ref, identical={1: {2}, 2: {1}})
    f.anchor(2, 0.0)
    m = np.zeros(ref.P)
    m[ref.slide_of == 1] = 0.5
    m[ref.slide_of == 2] = 0.5
    assert f._pooled_slide(m)[0] == 2


def _bookended():
    """Reckless Love's chorus shape: its last line repeats its first."""
    slides = [[], ["holy", "holy"], ["glory", "glory"], ["holy", "holy"]]
    data = score_json()
    data["bars"] = [
        _bar(1, ["ho", "ly", "ho", "ly"], [True, False, True, False], "G", 62),
        _bar(2, ["glo", "ry", "glo", "ry"], [True, False, True, False], "G", 64),
        _bar(3, ["ho", "ly", "ho", "ly"], [True, False, True, False], "G", 62),
    ]
    ref = SegmentReference(
        sc.from_extract(data), slides, ["other:0", "chorus:1", "chorus:2", "chorus:3"]
    )
    return ref, OnlineFollower(ref, identical={1: {3}, 3: {1}})


def test_a_repeated_line_shows_the_copy_the_order_is_on():
    ref, f = _bookended()
    f.shown = 2
    m = np.zeros(ref.P)
    m[ref.slide_of == 1] = 0.1 / (ref.slide_of == 1).sum()
    m[ref.slide_of == 3] = 0.9 / (ref.slide_of == 3).sum()
    assert f._pooled_slide(m)[0] == 3  # not back to the first copy


def test_a_wrong_copy_on_screen_gives_way_to_the_one_the_order_is_on():
    ref, f = _bookended()
    f.shown = 1
    m = np.zeros(ref.P)
    m[ref.slide_of == 1] = 0.45 / (ref.slide_of == 1).sum()
    m[ref.slide_of == 3] = 0.55 / (ref.slide_of == 3).sum()
    assert f._pooled_slide(m)[0] == 1  # near a tie: keep the screen
    m[ref.slide_of == 1] = 0.2 / (ref.slide_of == 1).sum()
    m[ref.slide_of == 3] = 0.8 / (ref.slide_of == 3).sum()
    assert f._pooled_slide(m)[0] == 3


def test_a_slide_needs_half_the_belief_to_replace_the_shown_one():
    ref = reference()
    f = OnlineFollower(ref)
    f.shown = 1
    m = np.zeros(ref.P)
    m[ref.slide_of == 1] = 0.3 / (ref.slide_of == 1).sum()
    m[ref.slide_of == 2] = 0.4 / (ref.slide_of == 2).sum()
    m[ref.intro_cell] = 0.3
    assert f._pooled_slide(m)[0] == 1  # 2 leads, but on 40%: hold
    m[ref.slide_of == 2] = 0.6 / (ref.slide_of == 2).sum()
    m[ref.intro_cell] = 0.1
    assert f._pooled_slide(m)[0] == 2


# ---------------------------------------------------------------- live coordinator


def live(scorer=None) -> LiveFollowers:
    items = [
        (0, SLIDES, SECTIONS, score_json()),
        (1, [[], ["other", "song"]], ["other:0", "verse:1"], None),
    ]
    return build_live_followers(items, scorer=scorer)


def test_only_songs_with_a_usable_sheet_are_followed():
    lf = live()
    assert lf.has(0) and not lf.has(1)


def test_a_sheet_that_places_too_few_slides_is_left_to_the_matcher():
    # two bridge slides the sheet does not print, and no bridge to borrow from
    slides = [*SLIDES, ["nothing", "like", "this"], ["nor", "this", "either"]]
    items = [(0, slides, [*SECTIONS, "bridge:1", "bridge:2"], score_json())]
    assert not build_live_followers(items).has(0)


def test_scored_spans_reach_the_current_song_only():
    class FakeScorer:
        def __init__(self):
            self.calls = []

        def score(self, audio, spans, place_among=None):
            from auto_presenter.follower.scorer import ScoreResult

            self.calls.append(len(spans))
            scores = np.full(len(spans), -5.0, np.float32)
            best = spans.index("the sound that saved a wretch")
            scores[best] = -0.3
            return ScoreResult(scores, best, 4.0)

    from auto_presenter.audio.types import AudioFrame

    fake = FakeScorer()
    lf = live(fake)
    lf.enter(0, 0, 0.0)
    frames = [
        AudioFrame(samples=np.zeros(1600, np.float32), t_start=i * 0.1, t_end=(i + 1) * 0.1)
        for i in range(60)
    ]
    lf.audio_now = 6.0
    lf.side_pass(frames, 6.0)
    lf.side_pass(frames, 6.5)  # too soon: every 2 s
    assert fake.calls == [len(set(spans_for(SLIDES).values()))]
    sf = lf.songs[0]
    assert sum(len(v) for v in sf.follower._events.values()) == 1
    lf.set_load(2)  # overloaded: scorer off
    lf.side_pass(frames, 9.0)
    assert len(fake.calls) == 1


# ---------------------------------------------------------------- engine routing


def engine_with_followers() -> tuple[FollowerEngine, LiveFollowers]:
    content = SetlistContent(
        items=[
            SetlistItemContent(item_idx=0, song_id="a", slides=SLIDES, slide_sections=SECTIONS),
            SetlistItemContent(
                item_idx=1,
                song_id="b",
                slides=[[], ["other", "song", "here"]],
                slide_sections=["other:0", "verse:1"],
            ),
        ]
    )
    base = build_engine(build_index(content))
    base.note_displayed(0, 0)
    lf = live()
    engine = FollowerEngine.wrap(base, lf)
    lf.enter(0, 0, 0.0)
    return engine, lf


def test_within_a_followed_song_the_matcher_does_not_move_the_screen():
    engine, _ = engine_with_followers()
    assert (
        engine._route(MatcherDecision(item_idx=0, slide_idx=2, t_seconds=5.0, confidence=0.9))
        is None
    )
    assert engine._displayed == (0, 0)  # the production engine was told the truth


def test_a_song_change_passes_and_moves_the_follower():
    engine, lf = engine_with_followers()
    d = engine._route(MatcherDecision(item_idx=1, slide_idx=1, t_seconds=5.0, confidence=0.9))
    assert d is not None and d.item_idx == 1
    assert lf.current == 1
    # back to the followed song: its follower is anchored where the screen goes
    engine._route(MatcherDecision(item_idx=0, slide_idx=2, t_seconds=9.0, confidence=0.9))
    assert lf.current == 0 and lf.songs[0].follower.shown == 2


def test_a_follower_decision_for_a_song_no_longer_on_screen_is_stale():
    engine, _ = engine_with_followers()
    assert engine.accept_follower_decision(
        MatcherDecision(item_idx=0, slide_idx=1, t_seconds=3.0, confidence=0.8)
    )
    engine.commit_external(1, 1)  # the operator moved on
    assert not engine.accept_follower_decision(
        MatcherDecision(item_idx=0, slide_idx=2, t_seconds=4.0, confidence=0.8)
    )


def test_words_reach_the_follower_through_the_engine():
    engine, lf = engine_with_followers()
    ev = TranscriptEvent(
        words=[Word(text="grace", t_start=1.0, t_end=1.3)], is_final=True, t_start=1.0, t_end=1.3
    )
    engine.update(ev)
    assert sum(len(v) for v in lf.songs[0].follower._events.values()) == 1


# ---------------------------------------------------------------- library


def test_lead_sheets_load_read_only_by_song(tmp_path: Path):
    import json

    db = tmp_path / "library.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE song_references (id TEXT, song_id TEXT, kind TEXT, source_name TEXT, "
        "data_json TEXT, summary_json TEXT, created_at INT, updated_at INT)"
    )
    con.execute(
        "INSERT INTO song_references VALUES ('r1','a','lead_sheet','x.pdf',?,'{}',0,0)",
        (json.dumps(score_json()),),
    )
    con.execute(
        "INSERT INTO song_references VALUES ('r2','b','recording','rec',?,'{}',0,0)", ("{}",)
    )
    con.commit()
    con.close()
    sheets = load_lead_sheets(db, ["a", "b", "c"])
    assert list(sheets) == ["a"] and sheets["a"]["key_fifths"] == 0


def test_a_library_without_the_table_has_no_lead_sheets(tmp_path: Path):
    db = tmp_path / "old.db"
    sqlite3.connect(db).close()
    assert load_lead_sheets(db, ["a"]) == {}


# ---------------------------------------------------------------- without a lead sheet


def test_library_spec_lays_every_sung_slide_out_in_library_order():
    spec = library_spec(SLIDES)
    assert spec.source == "library"
    assert [g.slide for g in spec.segments] == [1, 2] == spec.covered
    assert spec.sequences == []
    for g in spec.segments:
        assert (g.midi == -1).all() and (g.chord == -1).all()
        assert [w for w, _c in g.words] == [sc.norm(w) for w in SLIDES[g.slide]]
        # a word a beat, then a bar's gap before the next line
        assert g.cells == round((len(g.words) + 4) / sheet.CELL)


def test_a_library_reference_follows_the_same_interface():
    ref = SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS)
    assert ref.source == "library" and ref.s is None
    assert not ref.has_chords and reference().has_chords
    assert ref.coverage.share == 1.0
    assert ref.seg_slides == [0, 1, 2]
    assert ref.seg_slides[ref.first_segment] == 1
    for (si, _j), c in ref.word_cell.items():
        assert ref.slide_of[c] == si
    # library order: slide 1 goes on to slide 2
    assert ref.succ[1].argmax() == 2


def test_a_library_reference_moves_on_with_the_words():
    f = OnlineFollower(SegmentReference(library_spec(SLIDES), SLIDES, SECTIONS), lookahead_s=0.0)
    f.anchor(1, 0.0)
    t = 1.0
    for w in SLIDES[1] + SLIDES[2]:
        f.push_word(t, t + 0.5, w)
        t += 0.8
    assert f.readout(t + 1.0).slide == 2


def test_songs_without_a_sheet_are_followed_by_their_slides_when_asked():
    items = [
        (0, SLIDES, SECTIONS, score_json()),
        (1, [[], ["other", "song"]], ["other:0", "verse:1"], None),
        (2, [[]], ["other:0"], None),  # nothing sung: nothing to follow
    ]
    lf = build_live_followers(items, without_sheet=True)
    assert lf.songs[0].ref.source == "lead_sheet"
    assert lf.songs[1].ref.source == "library"
    assert not lf.has(2)


def test_a_sheet_placing_too_little_falls_back_to_the_slides():
    slides = [*SLIDES, ["words", "the", "sheet", "never", "prints"], ["nor", "these", "ones"]]
    items = [(0, slides, [*SECTIONS, "bridge:1", "tag:1"], score_json())]
    assert not build_live_followers(items).has(0)
    assert build_live_followers(items, without_sheet=True).songs[0].ref.source == "library"


def test_a_segment_rejects_a_word_outside_it():
    with pytest.raises(ValueError):
        SegmentSpec(slide=1, cells=2, words=[("x", 2)], midi=np.full(2, -1), chord=np.full(2, -1))


def test_song_follower_keeps_spans_in_a_fixed_order():
    sf = live().songs[0]
    assert isinstance(sf, SongFollower)
    assert sf.spans == sorted(set(sf.owner.values()))


def test_a_held_decision_leaves_the_follower_knowing_the_real_screen():
    lf = live()
    lf.enter(0, 1, 0.0)
    sf = lf.songs[0]
    sf.follower.shown = 2  # the follower decided slide 2...
    lf.note_shown(0, 1)  # ...but the screen was held on slide 1
    assert sf.follower.shown == 1
    lf.note_shown(1, 5)  # another song: not this follower's business
    assert sf.follower.shown == 1


def test_talk_before_the_song_keeps_the_follower_in_the_intro():
    f = OnlineFollower(reference(), lookahead_s=0.0)
    f.anchor(0, 0.0)
    for i, w in enumerate(["good", "morning", "everyone", "welcome", "please", "stand"]):
        f.push_word(1.0 + 0.5 * i, 5.0, w)
    assert f.readout(6.0).slide == 0


# ---------------------------------------------------------------- held-out fixes (2026-10-07)


def test_with_no_line_heard_lately_only_a_clear_majority_moves_the_screen():
    ref = reference()
    f = OnlineFollower(ref, lookahead_s=0.0)
    f.shown = 1
    m = np.zeros(ref.P)
    m[ref.slide_of == 1] = 0.4 / (ref.slide_of == 1).sum()
    m[ref.slide_of == 2] = 0.6 / (ref.slide_of == 2).sum()
    assert f._choose(m, 20.0)[0] == 1  # 60%, nothing heard: hold
    f.push_heard(19.0)
    assert f._choose(m, 20.0)[0] == 2  # a line was just heard: the usual bar
    m[ref.slide_of == 1] = 0.1 / (ref.slide_of == 1).sum()
    m[ref.slide_of == 2] = 0.9 / (ref.slide_of == 2).sum()
    assert f._choose(m, 40.0)[0] == 2  # nothing heard for 21 s, but the music is clear


def test_after_a_line_is_heard_murky_singing_does_not_return_to_the_blank():
    ref = reference()
    murky = ref.scored({(1, 3): -4.5, (2, 5): -4.8})  # favours the intro (null level)
    assert murky is not None
    f = OnlineFollower(ref, lookahead_s=0.0)
    f.anchor(1, 0.0)
    f.push_heard(0.5)
    for k in range(10):
        f.push_scored(1.0 + k, 1.5 + k, murky, heard=False)
    r = f.readout(12.0)
    assert r.marginal_now[ref.intro_cell] < 0.01


def test_a_slide_in_other_words_still_finds_its_line():
    data = score_json()
    # "which" for "that, saved": 0.86 alike, under the strict 0.93 bar
    slides = [
        [],
        ["amazing", "grace", "how", "sweet"],
        ["the", "sound", "which", "saved", "a", "wretch"],
    ]
    spans = sc.bind_slides(sc.from_extract(data), slides)
    assert spans[2], "a long line in other words is a lyric variant, not a different line"


def test_a_line_inside_another_slides_longer_line_is_not_its_place():
    from auto_presenter.follower.reference import prune_spans

    s = sc.from_extract(score_json())
    short = sc.Span(1, 0, 3, 0.0, 1.0, 1.0)
    long = sc.Span(2, 0, 6, 0.0, 2.0, 1.0)
    elsewhere = sc.Span(1, 8, 11, 4.0, 5.0, 1.0)
    out = prune_spans(s, {1: [short, elsewhere], 2: [long]})
    assert out[1] == [elsewhere] and out[2] == [long]


def test_a_step_counts_by_how_clearly_it_heard_a_line():
    ref = reference()
    assert ref.reliability({(1, 3): -1.0, (2, 5): -6.0}) == 1.0
    assert ref.reliability({(1, 3): -5.7, (2, 5): -6.0}) == 0.0  # Whisper made nothing out
    assert 0.0 < ref.reliability({(1, 3): -3.5}) < 1.0


def test_an_unmistakable_line_pulls_the_belief_mid_line_and_a_confused_one_does_not():
    ref = reference()
    confident = {(1, 3): -4.0, (2, 5): -0.3}  # clearly line 2
    confused = {(1, 3): -2.1, (2, 5): -2.0}
    assert ref.step_entropy(confident) < 0.2 < ref.step_entropy(confused)
    moved = {}
    for name, score in (("confident", confident), ("confused", confused)):
        f = OnlineFollower(ref, lookahead_s=0.0)
        f.anchor(1, 0.0)
        vec = ref.scored(score)
        f.push_scored(0.3, 0.4, vec, weight=0.0, entropy=ref.step_entropy(score))  # jump only
        moved[name] = float(f.readout(0.5).marginal_now[ref.slide_of == 2].sum())
    assert moved["confident"] > 0.02 > moved["confused"] + 0.015


# ---------------------------------------------------------------- the band waits (2026-10-07)


def test_cells_after_a_lines_last_note_are_not_sung():
    ref = reference()
    assert not ref.sung[ref.intro_cell]
    for g in range(1, len(ref.jump_targets)):
        a = ref.jump_targets[g]
        assert ref.sung[a]  # a segment starts on its first word
    # a sheet line that stops early leaves a gap up to the next line
    data = score_json()
    for i, n in enumerate(data["bars"][1]["notes"]):  # "grace how sweet" in a beat and a half
        n["onset"], n["dur"] = 0.5 * i, 0.5
    gappy = SegmentReference(sc.from_extract(data), SLIDES, SECTIONS)
    seg1 = slice(gappy.jump_targets[1], gappy.end_cells[1] + 1)
    assert gappy.sung[seg1].any() and not gappy.sung[seg1].all()
    assert not gappy.sung[gappy.end_cells[1]]


def test_a_wait_takes_its_share_of_a_segment_end_and_conserves_belief():
    ref = reference()
    tr = SegmentTransition(ref, 10.0, p_jump=0.0, p_rate=0.0, wait_enter=0.25, wait_leave=0.1)
    assert tr.N == ref.P + len(ref.jump_targets)
    x = np.zeros((len(ref.rates), tr.N), np.float32)
    x[:, ref.end_cells[1]] = 1.0 / len(ref.rates)  # everyone at line 1's last cell
    y = tr.step(x)
    assert y.sum() == pytest.approx(1.0, rel=1e-5)
    left = 1.0 - y[:, : ref.P][:, ref.slide_of == 1].sum()  # what moved past line 1
    assert y[:, ref.P + 1].sum() == pytest.approx(0.25 * left, rel=1e-4)
    # the wait holds (no tempo) and leaks at wait_leave to where line 1 goes
    z = tr.step(y)
    assert z[:, ref.P + 1].sum() >= 0.9 * y[:, ref.P + 1].sum()


def test_waits_exist_only_with_a_vocal_source(monkeypatch):
    from auto_presenter.follower import online

    monkeypatch.setattr(online, "WAIT_ENTER", 0.05)
    assert OnlineFollower(reference()).N == reference().P
    assert OnlineFollower(reference(), vocal_source=True).N > reference().P
    monkeypatch.setattr(online, "WAIT_ENTER", None)
    assert OnlineFollower(reference(), vocal_source=True).N == reference().P


def _sing_line_1_then_silence(f: OnlineFollower, ref: SegmentReference, until: float) -> int | None:
    f.anchor(1, 0.0)
    for i, w in enumerate(["amazing", "grace", "how", "sweet"]):
        f.push_word(0.5 + 0.8 * i, 1.0 + 0.8 * i, w)
    for k in range(int(until * 10)):
        t = k / 10
        f.push_vocal(t, t + 0.5, 1.0 if t < 3.5 else 0.0)
    r = None
    for k in range(int(until * 2)):
        r = f.readout(k / 2)
        if r.slide is not None:
            f.shown = r.slide
    return None if r is None else r.slide


def test_when_nobody_sings_after_a_line_the_band_waits_on_it(monkeypatch):
    from auto_presenter.follower import online

    monkeypatch.setattr(online, "WAIT_ENTER", 0.05)
    monkeypatch.setattr(online, "WAIT_LEAVE", 0.005)
    ref = reference()
    waiting = OnlineFollower(ref, lookahead_s=0.0, vocal_source=True)
    assert _sing_line_1_then_silence(waiting, ref, 20.0) == 1
    assert waiting.wait_now > 0.5
    # without the wait states, the tempo carries the belief on into line 2
    monkeypatch.setattr(online, "WAIT_ENTER", None)
    walking = OnlineFollower(ref, lookahead_s=0.0, vocal_source=True)
    assert _sing_line_1_then_silence(walking, ref, 20.0) != 1


def test_a_slide_contained_in_others_gets_no_place_inside_them():
    from auto_presenter.follower.reference import contained_slides

    slides = [
        [],
        ["what", "can", "wash", "away", "my", "sin", "nothing", "but", "the", "blood"],
        ["nothing", "but", "the", "blood"],  # the tag: only ever sung as part of slide 1
        ["what", "can", "wash"],  # contained too, but it would keep a place of its own
    ]
    assert contained_slides(slides) == {2, 3}
    assert contained_slides([[], ["a", "b"], ["a", "b"]]) == set()  # identical, not contained


def test_a_slide_ending_with_a_tag_can_repeat_it(monkeypatch):
    from auto_presenter.follower import reference as ref_mod

    slides = [
        [],
        ["amazing", "grace", "how", "sweet"],
        ["the", "sound", "that", "saved", "a", "wretch"],
        ["a", "wretch"],
    ]
    sections = ["other:0", "verse:1", "verse:2", "verse:3"]
    monkeypatch.setattr(ref_mod, "LOOPS", False)
    ref = SegmentReference(sc.from_extract(score_json()), slides, sections)
    seg2 = int(ref.seg_of[ref.word_cell[(2, 0)]])
    assert ref.tag_restart == {seg2: ref.word_cell[(2, 4)]}  # "a wretch" ends slide 2
    # with counted loops the tag is sung again as passes of its own
    monkeypatch.setattr(ref_mod, "LOOPS", True)
    ref = SegmentReference(sc.from_extract(score_json()), slides, sections)
    assert ref.tag_restart == {}
    passes = [g for g, sl in enumerate(ref.seg_slides) if sl == 2]
    assert len(passes) == ref_mod.LOOP_MAX
    tag = ref.copy_of[ref.jump_targets[passes[1]]]
    assert tag == ref.word_cell[(2, 4)]  # a further pass starts at the tag


def test_a_jump_to_a_repeated_slide_prefers_the_copy_near_the_belief():
    from auto_presenter.follower import online as fo

    ref = reference()
    f = OnlineFollower(ref, lookahead_s=0.0)
    # relabel the cells as five slides: 1 and 4 are copies of one refrain
    idx = np.full(ref.P, -1)
    idx[1:] = 1 + (np.arange(ref.P - 1) * 4) // (ref.P - 1)
    f._slide_idx, f._n_slides = idx, 5
    a = np.zeros((len(ref.rates), ref.P), np.float32)
    a[:, idx == 3] = 1.0  # the belief is on slide 3, next to copy 4
    a /= a.sum()
    dist = np.where((idx == 1) | (idx == 4), 1.0, 0.0).astype(np.float32)
    dist /= dist.sum()  # a jump to the refrain: either copy
    old = fo.COPY_NEAR
    try:
        fo.COPY_NEAR = 0.1
        near = f._near_copies(a, dist)
    finally:
        fo.COPY_NEAR = old
    assert near[idx == 4].sum() > 0.8 > dist[idx == 4].sum()


def test_a_shown_slide_that_lost_the_belief_gives_way_to_a_clear_leader(monkeypatch):
    """Five verses opening alike: the next one leads on a third of the belief,
    the shown slide holds almost none."""
    from auto_presenter.follower import online as fo

    ref = reference()
    idx = np.full(ref.P, -1)
    idx[1:] = 1 + (np.arange(ref.P - 1) * 4) // (ref.P - 1)  # four slides, 1-4

    def follower():
        f = OnlineFollower(ref)
        f._slide_idx, f._n_slides = idx, 5
        f.shown = 1
        return f

    m = np.zeros(ref.P)
    for sl, share in ((1, 0.02), (2, 0.34), (3, 0.32), (4, 0.32)):
        m[idx == sl] = share / (idx == sl).sum()
    monkeypatch.setattr(fo, "COLLAPSE_SHARE", 0.0)
    assert follower()._pooled_slide(m)[0] == 1  # off: under half, hold
    monkeypatch.setattr(fo, "COLLAPSE_SHARE", 0.2)
    assert follower()._pooled_slide(m)[0] == 2
    m[idx == 1] = 0.2 / (idx == 1).sum()  # the shown slide still holds a fair share
    assert follower()._pooled_slide(m)[0] == 1


def test_the_first_line_is_not_held_back_by_the_lyric_hold(monkeypatch):
    from auto_presenter.follower import online as fo

    ref = reference()
    m = np.zeros(ref.P)
    m[ref.slide_of == 1] = 0.6 / (ref.slide_of == 1).sum()
    m[ref.intro_cell] = 0.4
    f = OnlineFollower(ref, lookahead_s=0.0)
    f.anchor(0, 0.0)
    assert f._choose(m, 3.0)[0] == 0  # no lyric phrase yet: the blank stays
    # the words of the line arrived (the scorer has not yet heard it)
    f.push_word(1.0, 2.0, "amazing")
    f.push_word(1.4, 2.0, "grace")
    monkeypatch.setattr(fo, "FIRST_LINE_UNHELD", False)
    assert f._choose(m, 3.0)[0] == 0  # nothing heard yet: the hold's 0.8
    monkeypatch.setattr(fo, "FIRST_LINE_UNHELD", True)
    assert f._choose(m, 3.0)[0] == 1
    f.shown = 2  # mid-song, nothing heard lately: still held
    m[:] = 0
    m[ref.slide_of == 1] = 0.6 / (ref.slide_of == 1).sum()
    m[ref.slide_of == 2] = 0.4 / (ref.slide_of == 2).sum()
    assert f._choose(m, 3.0)[0] == 2


# ---------------------------------------------------------------- continuous mode


def test_a_read_out_becomes_a_position_over_the_slides_words():
    from auto_presenter.follower.live import flow_evidence
    from auto_presenter.follower.online import Readout

    lf = live()
    sf = lf.songs[0]
    m = np.zeros(sf.ref.P)
    m[sf.ref.word_cell[(2, 3)]] = 1.0  # "saved"
    flow = flow_evidence(sf, Readout(2, 1.0, m, 1.0, 4.0), 12.0)
    assert flow is not None and flow.item_idx == 0 and flow.t == 12.0
    assert flow.mass == {(2, 3): 1.0}
    assert flow.words == {1: 4, 2: 6}
    assert 0 < flow.rate_wps <= 4.0
    intro = np.zeros(sf.ref.P)
    intro[sf.ref.intro_cell] = 1.0
    assert flow_evidence(sf, Readout(0, 1.0, intro, 1.0, 4.0), 1.0) is None  # nobody singing yet


def test_continuous_mode_follows_the_score_follower_in_a_followed_song(monkeypatch):
    import asyncio

    from auto_presenter import server_follower as sfm
    from auto_presenter.follower.live import FlowEvidence
    from auto_presenter.server_state import _state

    engine, lf = engine_with_followers()
    sent = []

    async def broadcast(msg):
        sent.append(msg)

    class Director:
        state = "tracking"

    monkeypatch.setattr("auto_presenter.server_state._broadcast", broadcast)
    monkeypatch.setattr(_state, "matcher", engine)
    monkeypatch.setattr(_state, "director", Director())
    monkeypatch.setattr(_state, "followers", lf)
    monkeypatch.setattr(_state, "flow_follower", None)
    assert sfm.following_current_song()
    flow = FlowEvidence(0, 20.0, {(2, 3): 0.9, (2, 4): 0.1}, {1: 4, 2: 6}, 1.5)
    asyncio.run(sfm.observe_follower_flow(flow))
    assert len(sent) == 1
    msg = sent[0]
    # slide 1 has 4 words, so slide 2's word 3 is global word 7
    assert msg.item_idx == 0 and msg.jump and abs(msg.word - 7.1) < 1e-6
    assert msg.rate_wps == 1.5 and msg.limit == 10.0
    stale = FlowEvidence(1, 21.0, {(1, 0): 1.0}, {1: 3}, 1.0)
    asyncio.run(sfm.observe_follower_flow(stale))  # another song: not on screen any more
    assert len(sent) == 1


def test_a_song_without_a_lead_sheet_scrolls_with_its_follower(monkeypatch):
    """The library-only follower drives the scrolling column too."""
    import asyncio

    from auto_presenter import server_follower as sfm
    from auto_presenter.follower.live import FlowEvidence
    from auto_presenter.server_state import _state

    engine, _sheet_followers = engine_with_followers()
    lf = build_live_followers([(0, SLIDES, SECTIONS, None)], without_sheet=True)
    lf.enter(0, 1, 0.0)
    sent = []

    async def broadcast(msg):
        sent.append(msg)

    class Director:
        state = "tracking"

    monkeypatch.setattr("auto_presenter.server_state._broadcast", broadcast)
    monkeypatch.setattr(_state, "matcher", engine)
    monkeypatch.setattr(_state, "director", Director())
    monkeypatch.setattr(_state, "followers", lf)
    monkeypatch.setattr(_state, "flow_follower", None)
    assert lf.active is not None and lf.active.ref.source == "library"
    assert sfm.following_current_song()
    asyncio.run(sfm.observe_follower_flow(FlowEvidence(0, 20.0, {(2, 3): 1.0}, {1: 4, 2: 6}, 1.5)))
    assert len(sent) == 1 and sent[0].item_idx == 0


def test_the_word_tracker_leaves_continuous_mode_to_the_follower(monkeypatch):
    import asyncio

    from auto_presenter import server
    from auto_presenter.server_state import _state

    engine, lf = engine_with_followers()
    sent = []

    async def broadcast(msg):
        sent.append(msg)

    monkeypatch.setattr(server, "_broadcast", broadcast)
    monkeypatch.setattr(_state, "followers", lf)
    monkeypatch.setattr(_state, "flow_follower", None)
    event = TranscriptEvent(
        words=[Word(text="grace", t_start=1.0, t_end=1.3)], is_final=True, t_start=1.0, t_end=1.3
    )
    asyncio.run(server._observe_flow(engine, object(), event))
    assert sent == [] and _state.flow_follower is None


def test_a_song_change_heard_mid_slide_leaves_the_band_anywhere_in_it():
    ref = reference()
    f = OnlineFollower(ref)
    f.anchor(2, 10.0)  # an operator's choice: the slide's start
    start = int(ref.jump_targets[ref.seg_of[ref.word_cell[(2, 0)]]])
    assert f.alpha[:, start].sum() == pytest.approx(1.0)
    f.anchor(2, 10.0, mid_slide=True)  # heard in the words: somewhere inside it
    on_slide = f.alpha[:, ref.slide_of == 2].sum(axis=0)
    assert on_slide.sum() == pytest.approx(1.0)
    assert (on_slide > 0).sum() > 3 and on_slide.max() < 0.5
    assert f.shown == 2


def test_after_the_last_slide_the_band_repeats_it_or_goes_back_not_to_the_top(monkeypatch):
    from auto_presenter.follower import reference as fr

    p = fr.order_prior([1, 2, 3, 4], [], [1, 2, 3, 4])
    assert p[3, 3] == pytest.approx(fr.LAST_REPEAT)
    assert p[3, 0] == pytest.approx(p[3, 1]) == pytest.approx(p[3, 2])
    assert p[0, 1] == 1.0  # the rest of the song keeps its order
    # a last slide with no segment (a tag slide): the last one that has one
    q = fr.order_prior([1, 2, 3, 4], [], [1, 2, 3, 4, 5])
    assert q[3, 3] == pytest.approx(fr.LAST_REPEAT)
    assert q[3, 0] == pytest.approx((1 - fr.LAST_REPEAT) / 3)
    monkeypatch.setattr(fr, "LAST_REPEAT", None)
    assert fr.order_prior([1, 2, 3, 4], [], [1, 2, 3, 4])[3, 0] == 1.0  # the old wrap


def test_the_column_reads_the_slide_on_screen_and_sits_behind_a_wide_belief(monkeypatch):
    """Belief split between slide 2 and the slide 1 the read-out keeps: the
    column stays in slide 1, at a low quantile of the belief there."""
    from auto_presenter.follower import live as fl
    from auto_presenter.follower.live import flow_evidence
    from auto_presenter.follower.online import Readout

    lf = live()
    sf = lf.songs[0]
    m = np.zeros(sf.ref.P)
    for j in range(4):  # slide 1, evenly over its four words
        m[sf.ref.word_cell[(1, j)]] = 0.1
    m[sf.ref.word_cell[(2, 3)]] = 0.6  # most of the belief has moved on
    r = Readout(1, 0.4, m, 1.0, 4.0)
    monkeypatch.setattr(fl, "FLOW_QUANTILE", None)
    assert set(flow_evidence(sf, r, 5.0).mass) == {(1, 0), (1, 1), (1, 2), (1, 3)}
    monkeypatch.setattr(fl, "FLOW_QUANTILE", 0.1)
    assert set(flow_evidence(sf, r, 5.0).mass) == {(1, 0)}
    monkeypatch.setattr(fl, "FLOW_ON_SHOWN_SLIDE", False)
    monkeypatch.setattr(fl, "FLOW_QUANTILE", None)
    assert (2, 3) in flow_evidence(sf, r, 5.0).mass  # the old behaviour


def test_a_tag_closing_several_verses_gets_no_place_of_its_own_from_library_slides():
    from auto_presenter.follower.spec import library_spec, tag_slides

    slides = [
        [],
        ["what", "can", "wash", "away", "my", "sin", "nothing", "but", "the", "blood"],
        ["what", "can", "make", "me", "whole", "nothing", "but", "the", "blood"],
        ["nothing", "but", "the", "blood"],  # the tag: inside two verses
        ["oh", "precious", "is", "the", "flow"],
        ["oh", "precious", "is", "the", "flow", "that", "makes", "me", "white"],  # 4, longer
    ]
    assert tag_slides(slides, 2) == {3}  # slide 4 sits inside only one other: a line of its own
    spec = library_spec(slides)
    assert [g.slide for g in spec.segments] == [1, 2, 4, 5]
    assert spec.covered == [1, 2, 3, 4, 5]  # the tag is still covered, by the verses


def test_a_cell_without_a_chord_is_neither_for_nor_against_its_slide(monkeypatch):
    from auto_presenter.follower import reference as fr

    ref = reference()
    chord = ref.chord_sheet.copy()
    chord[ref.slide_of == 2] = -1  # slide 2 knows no chords (not learned yet)
    ref.chord_sheet = chord
    probs = np.zeros(25, np.float32)
    probs[int(ref.chord_sheet[ref.slide_of == 1][0])] = 1.0  # the band plays slide 1's chord
    ll = ref.chords(probs)[0]
    has = ref.chord >= 0
    assert np.allclose(ll[~has], ll[has].mean())  # the mean, not 0
    monkeypatch.setattr(fr, "CHORDLESS_AT_MEAN", False)
    assert np.allclose(ref.chords(probs)[0][~has], 0.0)


