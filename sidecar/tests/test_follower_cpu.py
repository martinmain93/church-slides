"""The score follower off Apple silicon: words and chords, no span scorer,
and only with `score_follower_cpu` on."""

from __future__ import annotations

import pytest

from auto_presenter.protocol import ParticleFilterTunables, SetlistContent, SetlistItemContent
from auto_presenter.server_follower import build_session_followers
from auto_presenter.server_state import _state

CONTENT = SetlistContent(
    items=[
        SetlistItemContent(
            item_idx=0,
            song_id="song_a",
            slides=[
                [],
                "amazing grace how sweet the sound".split(),
                "that saved a wretch like me".split(),
            ],
            slide_sections=["other:0", "verse:1", "verse:2"],
        )
    ]
)


class CpuWhisper:
    """Not MLX: an Intel Mac's or a Windows PC's speech engine."""


@pytest.fixture
def tunables(monkeypatch, tmp_path):
    monkeypatch.setattr(_state, "library_db_path", tmp_path / "library.db")

    def use(**kw):
        monkeypatch.setattr(_state, "tunables", ParticleFilterTunables(**kw))

    return use


def test_off_apple_silicon_the_follower_waits_for_its_switch(tunables):
    tunables()
    assert build_session_followers(CONTENT, CpuWhisper()) is None


def test_with_the_switch_it_follows_without_a_span_scorer(tunables):
    tunables(score_follower_cpu=True)
    followers = build_session_followers(CONTENT, CpuWhisper())
    assert followers is not None
    assert followers.scorer is None
    assert 0 in followers.songs
    # the side pass has nothing to score and does nothing
    assert followers.side_pass([], 2.0) is None


def test_the_kill_switch_still_wins(tunables):
    tunables(score_follower=False, score_follower_cpu=True)
    assert build_session_followers(CONTENT, CpuWhisper()) is None
