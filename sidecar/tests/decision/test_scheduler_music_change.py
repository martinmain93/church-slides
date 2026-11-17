"""A key or tempo change lowers the bar for leaving a song, briefly."""

from __future__ import annotations

from auto_presenter.decision.scheduler import LeadTimeScheduler, LeadTimeSchedulerConfig


def test_relief_only_inside_the_window():
    s = LeadTimeScheduler.__new__(LeadTimeScheduler)
    s.config = LeadTimeSchedulerConfig()
    s.music_change_t = None
    assert s._music_change_relief(100.0) == 0.0
    s.note_music_change(100.0)
    assert s._music_change_relief(100.0) == s.config.cross_song_music_change_relief > 0
    assert s._music_change_relief(100.0 + s.config.cross_song_music_change_window_s + 1) == 0.0
    assert s._music_change_relief(99.0) == 0.0  # not before it happened
