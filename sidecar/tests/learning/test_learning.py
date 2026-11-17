"""Learning from services: look-back alignment, label cleaning, storage, the
session observer, and the learned reference."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from auto_presenter.asr.types import Word
from auto_presenter.follower.live import build_live_followers
from auto_presenter.follower.reference import SegmentReference
from auto_presenter.follower.spec import library_spec
from auto_presenter.learning import labels, profile
from auto_presenter.learning.lookback import HeardWord, SongModel, align
from auto_presenter.learning.session import LearningSession
from auto_presenter.learning.store import Store
from auto_presenter.learning.summary import PerformanceSummary, slide_key
from auto_presenter.protocol import SetlistContent, SetlistItemContent

SLIDES = [
    [],
    ["amazing", "grace", "how", "sweet", "the", "sound"],
    ["that", "saved", "a", "wretch", "like", "me"],
    ["i", "once", "was", "lost", "but", "now", "am", "found"],
    ["was", "blind", "but", "now", "i", "see"],
]


def sung(order: list[int], t0: float = 0.0, gap: float = 2.0) -> list[HeardWord]:
    out, t = [], t0
    for si in order:
        for w in SLIDES[si]:
            out.append(HeardWord(t, w))
            t += 0.7
        t += gap
    return out


# ---------------------------------------------------------------- look-back


def test_lookback_recovers_library_order_and_line_starts():
    al = align(SongModel(SLIDES), sung([1, 2, 3, 4]))
    assert al.sequence == [1, 2, 3, 4]
    assert all(p.confidence > 0.8 for p in al.passes)
    assert al.passes[1].t_start == sung([1, 2])[6].t  # the first word of slide 2


def test_lookback_follows_a_repeat_the_library_does_not_print():
    # the band goes back to line 3 after line 4, and sings line 4 twice
    al = align(SongModel(SLIDES), sung([1, 2, 3, 4, 3, 4, 4]))
    assert al.sequence == [1, 2, 3, 4, 3, 4, 4]


def test_lookback_shrugs_off_junk_words():
    words = sung([1, 2])
    words.insert(3, HeardWord(words[2].t + 0.1, "hallelujah"))
    words.insert(9, HeardWord(words[8].t + 0.1, "okay"))
    assert align(SongModel(SLIDES), words).sequence == [1, 2]


def test_lookback_of_nothing_is_empty():
    assert align(SongModel(SLIDES), []).passes == []
    assert align(SongModel([[], []]), sung([1])).passes == []


# ---------------------------------------------------------------- labels


def test_scrubbing_and_corrected_ai_moves_are_dropped():
    moves = [
        (0.0, 1, "manual"),
        (10.0, 2, "manual"),
        (10.4, 3, "manual"),  # passed through on the way to 4
        (10.9, 4, "manual"),
        (30.0, 2, "ai"),  # wrong: the operator puts it back
        (31.5, 4, "override"),
        (50.0, 1, "ai"),
    ]
    assert labels.clean_moves(moves) == [
        (0.0, 1, "manual"),
        (10.9, 4, "manual"),
        (50.0, 1, "ai"),
    ]


def test_who_led():
    assert labels.led_by([(0, 1, "manual"), (5, 2, "override"), (9, 3, "voice")]) == "operator"
    assert labels.led_by([(0, 1, "ai"), (5, 2, "ai"), (9, 3, "manual")]) == "mixed"
    assert labels.led_by([(0, 1, "ai")] * 5) == "ai"
    assert labels.led_by([]) == "none"


def test_weights_follow_provenance_leadership_and_agreement():
    w = labels.performance_weight
    assert w("injected", "operator", 1.0, 1.0) == 0.0
    assert w("live", "operator", 0.9, 0.9) == labels.W_OPERATOR
    assert w("live", "operator", 0.2, 0.9) == labels.W_OPERATOR_DISAGREES
    assert w("live", "ai", None, 0.9) == labels.W_AI
    assert w("live", "ai", None, 0.4) == 0.0


def test_agreement_counts_the_operators_share_of_confident_line_changes():
    al = align(SongModel(SLIDES), sung([1, 2, 3, 4]))
    assert (
        labels.agreement(
            al.passes, [(0, 1, "manual"), (5, 2, "manual"), (9, 3, "manual"), (14, 4, "manual")]
        )
        == 1.0
    )
    assert labels.agreement(al.passes, [(0, 1, "manual"), (14, 4, "manual")]) == 0.5
    assert labels.agreement(al.passes, [(0, 1, "ai")]) is None


# ---------------------------------------------------------------- session + store


def content() -> SetlistContent:
    return SetlistContent(
        items=[
            SetlistItemContent(item_idx=0, song_id="grace", slides=SLIDES),
            SetlistItemContent(item_idx=1, song_id="other", slides=[[], ["la", "la", "la"]]),
        ]
    )


def words_of(heard: list[HeardWord]) -> list[Word]:
    return [Word(text=h.text, t_start=h.t, t_end=h.t + 0.5, confidence=1.0) for h in heard]


def test_a_session_writes_one_row_per_song_it_heard(tmp_path: Path):
    store = Store(tmp_path / "learning.db")
    s = LearningSession(content(), store, start=(0, 1))
    heard = sung([1, 2, 3, 4, 3, 4], t0=1.0)
    t_next = heard[6].t  # the operator moves when line 2 starts
    s.on_slide_shown(0, 2, "manual", t=t_next - 0.2)
    s.on_slide_shown(0, 3, "manual", t=heard[12].t - 0.2)
    s.on_slide_shown(0, 4, "manual", t=heard[20].t - 0.2)
    s.on_slide_shown(0, 3, "manual", t=heard[26].t - 0.2)
    s.on_slide_shown(0, 4, "manual", t=heard[34].t - 0.2)
    s.on_words(words_of(heard))
    s.on_slide_shown(1, 1, "ai", t=heard[-1].t + 5)  # on to a song nobody sings
    s.stop()

    rows = store.load("grace")
    assert len(rows) == 1
    row = rows[0]
    assert [p.slide for p in row.passes] == [1, 2, 3, 4, 3, 4]
    assert row.led_by == "operator" and row.agreement == 1.0 and row.weight == 1.0
    assert row.final and row.provenance == "live"
    assert row.passes[0].t_start == 0.0  # times are relative to the first word
    assert row.slide_keys[1] == slide_key(SLIDES[1])
    assert store.load("other") == []  # too few words to be a performance


def test_words_are_filed_under_the_song_on_screen_when_sung(tmp_path: Path):
    s = LearningSession(content(), None, start=(0, 1))
    heard = sung([1, 2, 3], t0=1.0)
    s.on_slide_shown(1, 1, "manual", t=heard[-1].t + 1.0)
    s.on_words(words_of(heard))  # arrives after the song changed
    snap = s.snapshot()
    grace = next(x for x in snap["songs"] if x["song_id"] == "grace")
    assert grace["heard_words"] == len(heard)


def test_drafts_are_replaced_not_duplicated(tmp_path: Path):
    store = Store(tmp_path / "learning.db")
    s = LearningSession(content(), store, start=(0, 1))
    s.on_words(words_of(sung([1, 2, 3], t0=1.0)))
    s.tick()
    s.on_words(words_of(sung([4], t0=40.0)))
    s.stop()
    rows = store.load("grace")
    assert len(rows) == 1 and rows[0].final
    assert [p.slide for p in rows[0].passes] == [1, 2, 3, 4]


def test_injected_audio_is_kept_but_never_counts(tmp_path: Path):
    store = Store(tmp_path / "learning.db")
    s = LearningSession(content(), store, provenance="injected", start=(0, 1))
    s.on_words(words_of(sung([1, 2, 3, 4], t0=1.0)))
    s.stop()
    (row,) = store.load("grace")
    assert row.provenance == "injected" and row.weight == 0.0


def test_summaries_round_trip_and_reject_other_versions(tmp_path: Path):
    store = Store(tmp_path / "learning.db")
    s = LearningSession(content(), store, start=(0, 1))
    s.on_words(words_of(sung([1, 2, 3, 4], t0=1.0)))
    s.stop()
    (row,) = store.load("grace")
    again = PerformanceSummary.from_json(row.to_json())
    assert again == row
    assert (
        PerformanceSummary.from_json(row.to_json().replace('"version":1', '"version":99')) is None
    )
    assert PerformanceSummary.from_json("not json") is None


def test_a_store_beside_no_library_is_none(tmp_path: Path):
    assert Store.beside(None) is None
    store = Store.beside(tmp_path / "library.db")
    assert store is not None and store.path == tmp_path / "learning.db"


# ---------------------------------------------------------------- profile


DAY_MS = 86_400_000


def performance(
    order: list[int], provenance: str = "live", started_at_ms: int = 0
) -> PerformanceSummary:
    s = LearningSession(content(), None, provenance=provenance, start=(0, 1))
    heard = sung(order, t0=1.0)
    for i, si in enumerate(order):
        s.on_slide_shown(0, si, "manual", t=heard[sum(len(SLIDES[x]) for x in order[:i])].t - 0.2)
    s.on_words(words_of(heard))
    s.tick(final=True)
    row = s._perfs[0].summary
    row.started_at_ms = started_at_ms
    return row


def test_profile_learns_the_order_the_band_sang():
    song = profile.aggregate([performance([1, 2, 3, 4, 3, 4])], SLIDES, now_ms=0)
    assert song.used == 1 and song.n_eff == 1.0
    # each line change, weighted by how sure the look-back was of both lines
    assert [seq for seq, _w in song.sequences] == [[1, 2], [2, 3], [3, 4], [4, 3], [3, 4]]
    assert all(0.9 < w <= 1.0 for _seq, w in song.sequences)
    assert set(song.timing) == {1, 2, 3, 4}
    # one word-spacing per word: the synthetic band sings evenly
    assert song.timing[1].onsets[:3] == [0.0, 1.0, 2.0]


def test_profile_decays_with_age_and_ignores_injected_audio():
    old = performance([1, 2, 3, 4], started_at_ms=0)
    song = profile.aggregate([old], SLIDES, now_ms=int(profile.HALF_LIFE_DAYS * DAY_MS))
    assert abs(song.n_eff - 0.5) < 1e-9
    assert profile.aggregate([performance([1, 2, 3, 4], provenance="injected")], SLIDES).used == 0


def test_profile_rebinds_moved_slides_and_drops_vanished_ones():
    row = performance([1, 2, 3, 4])
    moved = [SLIDES[0], SLIDES[2], SLIDES[1], SLIDES[4]]  # 1 and 2 swapped, 3 deleted
    song = profile.aggregate([row], moved, now_ms=0)
    # the order breaks where the deleted line was, rather than inventing 2 -> 4
    assert [seq for seq, _w in song.sequences] == [[2, 1]]
    assert song.unbound == 1


def test_learned_order_takes_a_growing_share_of_the_prior():
    rows = [performance([1, 2, 3, 4, 3, 4])]

    def back_to_3(n: int) -> float:
        spec = profile.learned_spec(
            library_spec(SLIDES),
            profile.aggregate(
                # n services, a day apart (the same start would be one service)
                [replace(rows[0], started_at_ms=k * 86_400_000) for k in range(n)],
                SLIDES,
                now_ms=0,
            ),
        )
        ref = SegmentReference(spec, SLIDES)
        seg = ref.seg_slides.index(4)
        return float(ref.succ[seg, ref.seg_slides.index(3)])

    plain = SegmentReference(library_spec(SLIDES), SLIDES)
    base = float(plain.succ[plain.seg_slides.index(4), plain.seg_slides.index(3)])
    assert base < back_to_3(1) < back_to_3(10) <= 0.9 + 1e-9


def test_learned_timing_moves_library_lines_part_of_the_way():
    spec = profile.learned_spec(
        library_spec(SLIDES), profile.aggregate([performance([1, 2, 3, 4])], SLIDES, now_ms=0)
    )
    assert spec.source == "library+learned"
    plain = library_spec(SLIDES)
    assert [g.cells for g in spec.segments] != [g.cells for g in plain.segments]


def test_a_live_follower_carries_what_was_learned():
    items = [(0, SLIDES, [], None)]
    song = profile.aggregate([performance([1, 2, 3, 4, 3, 4])], SLIDES, now_ms=0)
    lf = build_live_followers(items, without_sheet=True, learned={0: song})
    assert lf.songs[0].ref.source == "library+learned"
    assert build_live_followers(items, without_sheet=True).songs[0].ref.source == "library"


def test_stopping_twice_writes_once(tmp_path: Path):
    store = Store(tmp_path / "learning.db")
    s = LearningSession(content(), store, start=(0, 1))
    s.on_words(words_of(sung([1, 2, 3, 4], t0=1.0)))
    s.stop()
    runs = s.passes_run
    s.stop()
    assert s.passes_run == runs


def test_finishing_an_old_session_leaves_the_new_one_alone(tmp_path: Path):
    from auto_presenter.server_learning import stop_learning
    from auto_presenter.server_state import _state

    old = LearningSession(content(), None, start=(0, 1))
    new = LearningSession(content(), None, start=(0, 1))
    _state.learning = new
    try:
        stop_learning(old)  # the old session ended on its own, late
        assert _state.learning is new
        stop_learning()
        assert _state.learning is None
    finally:
        _state.learning = None


def test_each_learned_change_carries_its_evidence():
    row = performance([1, 2, 3, 4, 3, 4])  # the operator followed every move
    song = profile.aggregate([row], SLIDES, now_ms=0)
    back = [ev for ev in song.evidence if (ev.from_slide, ev.to_slide) == (4, 3)]
    assert len(back) == 1
    ev = back[0]
    assert ev.performance_id == row.id
    assert abs(ev.weight - ev.conf_from * ev.conf_to) < 0.002
    assert ev.conf_from >= 0.8 and ev.conf_to >= 0.8
    assert ev.operator_agrees is True


def test_the_panel_explains_a_learned_line():
    from auto_presenter.server_learning import _order

    song = profile.aggregate([performance([1, 2, 3, 4, 3, 4])], SLIDES, now_ms=0)
    view = {t.from_slide: t for t in _order(SLIDES, song)}
    four = view[4]
    assert four.library_next is None and four.learned_next == 3
    assert [s.slide for s in four.successors] == [3]
    assert len(four.evidence) == 1 and four.evidence[0].operator_agrees
    # what was learned moves the follower's odds of going back to line 3
    p = {s.slide: s.p for s in four.prior}
    base = {s.slide: s.p for s in four.base_prior}
    assert p[3] > base.get(3, 0.0)


def test_inspecting_a_recording_scores_the_lookback_against_its_golden():
    from auto_presenter.eval.types import GroundTruthEvent, Recording, SetlistItemSnapshot
    from auto_presenter.learning.inspect import inspect_lookback
    from auto_presenter.protocol import InspectLookbackResponse

    order = [1, 2, 3, 4, 3, 4]
    heard = sung(order, t0=1.0)
    starts, i = [], 0
    for si in order:
        starts.append((heard[i].t - 0.2, si))
        i += len(SLIDES[si])
    golden = [
        GroundTruthEvent(t_seconds=t, item_idx=0, slide_idx=si, source="manual") for t, si in starts
    ]
    rec = Recording(
        id="r",
        setlist_id="s",
        started_at_ms=0,
        duration_seconds=heard[-1].t + 5,
        ground_truth=golden,
        audio_path="",
        setlist_content=[SetlistItemSnapshot(item_idx=0, song_id="grace", slides=SLIDES)],
    )
    (item,) = inspect_lookback(rec, golden, words_of(heard))
    InspectLookbackResponse(items=[item])  # what the inspector is sent must validate
    assert item["lookback_sequence"] == item["golden_sequence"] == order
    assert item["precision"] == item["recall"] == 1.0
    assert all(p["agrees"] for p in item["passes"])
    assert abs(item["median_offset_s"]) < 0.5
    assert len(item["words"]) == len(heard) and item["words"][0]["slide"] == 1


def _row(confs: list[tuple[int, float]]) -> PerformanceSummary:
    """A stored performance whose passes are `(slide, confidence)`."""
    from auto_presenter.learning.summary import PassRecord

    return PerformanceSummary(
        id="p",
        song_id="grace",
        started_at_ms=0,
        provenance="live",
        led_by="operator",
        weight=1.0,
        passes=[
            PassRecord(
                slide=s,
                key=slide_key(SLIDES[s]),
                t_start=10.0 * i,
                t_end=10.0 * i + 5,
                onsets=[(0, 0.0), (1, 0.7), (2, 1.4)],
                confidence=c,
            )
            for i, (s, c) in enumerate(confs)
        ],
        operator=[],
        agreement=None,
        slide_keys=[slide_key(sw) for sw in SLIDES],
        heard_words=30,
    )


def test_unsure_passes_and_changes_do_not_count():
    # 2 is too unsure to count at all, so 1 -> 3 must not be invented across it;
    # 3 -> 4 is sure enough line by line (0.8 each) but not as a change (0.64)
    song = profile.aggregate(
        [_row([(1, 0.99), (2, 0.6), (3, 0.8), (4, 0.8), (1, 0.95)])], SLIDES, now_ms=0
    )
    assert [seq for seq, _w in song.sequences] == [[4, 1]]
    (w,) = [w for _s, w in song.sequences]
    assert abs(w - 0.8 * 0.95) < 1e-9


def test_timing_leans_on_the_surer_pass():
    from auto_presenter.learning.profile import _weighted_median

    assert _weighted_median([(1.0, 0.95), (3.0, 0.71), (2.0, 0.9)]) == 2.0
    assert _weighted_median([(1.0, 0.99), (5.0, 0.2)]) == 1.0


def test_a_malformed_summary_row_hides_only_itself():
    from auto_presenter.learning.summary import SUMMARY_VERSION, PerformanceSummary

    assert PerformanceSummary.from_json("[1, 2]") is None
    row = '{"version": ' + str(SUMMARY_VERSION) + ', "passes": [{}]}'
    assert PerformanceSummary.from_json(row) is None
    assert PerformanceSummary.from_json("not json") is None


def test_a_session_restarted_mid_service_counts_once():
    from types import SimpleNamespace

    from auto_presenter.learning.profile import SAME_SERVICE_MS, one_per_service

    def row(t, n, provenance="live", weight=1.0):
        return SimpleNamespace(
            started_at_ms=t,
            provenance=provenance,
            weight=weight,
            passes=[SimpleNamespace(onsets=[(0, 0.0)] * n)],
        )

    a, b, c = row(0, 40), row(10 * 60 * 1000, 90), row(SAME_SERVICE_MS + 60 * 60 * 1000, 30)
    assert one_per_service([a, b, c]) == [b, c]
    # an injected row, fuller, doesn't push out the live one
    injected = row(5 * 60 * 1000, 500, provenance="injected")
    assert one_per_service([a, injected]) == [a]


def _setlist(*songs: str) -> SetlistContent:
    return SetlistContent(
        items=[
            SetlistItemContent(item_idx=i, song_id=s, slides=[[], [s, "line", "one"]])
            for i, s in enumerate(songs)
        ]
    )


def test_a_reordered_setlist_keeps_every_songs_performance():
    from auto_presenter.learning.session import LearningSession

    s = LearningSession(_setlist("a", "b", "c"), store=None)
    for idx in range(3):
        s.on_slide_shown(idx, 1, "operator", t=float(idx))
    before = {p.song_id: p for p in s._perfs.values()}
    s.update_content(_setlist("b", "a", "c"))  # a swap
    assert {i: p.song_id for i, p in s._perfs.items()} == {0: "b", 1: "a", 2: "c"}
    s.update_content(_setlist("a", "c", "b"))  # b to the end
    assert {i: p.song_id for i, p in s._perfs.items()} == {0: "a", 1: "c", 2: "b"}
    assert all(s._perfs[i] is before[p.song_id] for i, p in s._perfs.items())
