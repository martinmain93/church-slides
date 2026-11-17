"""Tests for the setlist-indexing helper."""

from __future__ import annotations

from auto_presenter.index.library import build_index
from auto_presenter.index.types import IndexedSetlist  # noqa: F401
from auto_presenter.protocol import SetlistContent, SetlistItemContent


def _content(*items: tuple[int, str, list[list[str]]]) -> SetlistContent:
    return SetlistContent(
        items=[
            SetlistItemContent(item_idx=item_idx, song_id=song_id, slides=slides)
            for item_idx, song_id, slides in items
        ]
    )


def test_build_index_preserves_item_and_slide_order():
    content = _content(
        (0, "song_a", [["amazing", "grace"], ["how", "sweet", "the", "sound"]]),
        (1, "song_b", [["holy", "holy", "holy"]]),
    )
    idx = build_index(content)
    assert isinstance(idx, IndexedSetlist)
    assert idx.item_count == 2
    item0 = idx.item(0)
    assert item0 is not None
    assert item0.song_id == "song_a"
    assert len(item0.slides) == 2
    assert item0.slides[0].words == ("amazing", "grace")
    assert item0.slides[1].text == "how sweet the sound"


def test_build_index_keeps_empty_slides_so_locator_indices_align():
    """An instrumental / divider slide is legitimately empty."""
    content = _content(
        (0, "song_a", [["verse", "one"], [], ["chorus"]]),
    )
    idx = build_index(content)
    item = idx.item(0)
    assert item is not None
    assert len(item.slides) == 3
    assert item.slides[1].words == ()


def test_indexed_setlist_item_out_of_range_returns_none():
    idx = build_index(_content((0, "song_a", [["a"]])))
    assert idx.item(99) is None
    assert idx.item(-1) is None


# --- Phase 6 extensions -----------------------------------------------------


def test_build_index_section_index_with_repeated_sections():
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=0,
                song_id="song_a",
                slides=[["v1a"], ["v1b"], ["c"], ["v2"], ["c"]],
                slide_sections=[
                    "verse:1",
                    "verse:1",
                    "chorus:1",
                    "verse:2",
                    "chorus:1",
                ],
            )
        ]
    )
    idx = build_index(content)
    item = idx.item(0)
    assert item is not None
    assert item.section_index["verse:1"] == (0, 1)
    assert item.section_index["chorus:1"] == (2, 4)
    assert item.section_index["verse:2"] == (3,)


def test_build_index_section_index_empty_when_no_sections_supplied():
    """Backwards-compat: omitting `slide_sections` leaves section_index empty."""
    content = _content((0, "song_a", [["a"], ["b"]]))
    idx = build_index(content)
    item = idx.item(0)
    assert item is not None
    assert item.section_index == {}
    # And per-slide section keys default to empty strings.
    for s in item.slides:
        assert s.section_key == ""


def test_a_reading_switched_off_keeps_its_slides_but_no_words():
    """The read-along kill switch, and any session not fast enough to follow
    speech, silence a passage without moving the slides after it."""
    from auto_presenter.index.library import build_index
    from auto_presenter.protocol import SetlistContent, SetlistItemContent

    content = SetlistContent(items=[
        SetlistItemContent(item_idx=0, song_id="item_r", spoken=True,
                           slides=[[], ["for", "god", "so"], ["loved", "the", "world"]]),
        SetlistItemContent(item_idx=1, song_id="s", slides=[[], ["amazing", "grace"]]),
    ])
    on = build_index(content)
    off = build_index(content, follow_readings=False)
    assert [s.words for s in on.item(0).slides] == [(), ("for", "god", "so"), ("loved", "the", "world")]
    assert [s.words for s in off.item(0).slides] == [(), (), ()]
    assert off.item(1).slide(1).words == ("amazing", "grace")
