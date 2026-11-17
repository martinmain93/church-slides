"""A song added to the end mid-service joins the saved setlist; other edits don't."""

from auto_presenter.eval.types import SetlistItemSnapshot
from auto_presenter.recording.sink import RecordingSink


def _item(i: int, song: str) -> SetlistItemSnapshot:
    return SetlistItemSnapshot(item_idx=i, song_id=song, slides=[[], ["la"]])


def test_appended_songs_join_and_reorders_dont(tmp_path):
    sink = RecordingSink("rec_t", tmp_path, "s1", setlist_content=[_item(0, "a"), _item(1, "b")])
    assert sink.extend_setlist([_item(0, "a"), _item(1, "b"), _item(2, "c")])
    assert [i.song_id for i in sink._setlist_content] == ["a", "b", "c"]
    assert not sink.extend_setlist([_item(0, "b"), _item(1, "a"), _item(2, "c"), _item(3, "d")])
    assert not sink.extend_setlist([_item(0, "a")])
    assert [i.song_id for i in sink._setlist_content] == ["a", "b", "c"]
