"""Applying tunables to whichever matcher is actually running.

The handler was written when the only matcher was a particle cloud and reached
straight for `filter.config.n_particles`. Once the dense forward filter became
the default that attribute stopped existing, so the handler raised — and the
scheduler settings applied *after* it, which are shared by both trackers and
are the ones an operator reaches for mid-service, never got applied at all.
"""

from __future__ import annotations

import pytest

from auto_presenter import server as server_mod
from auto_presenter.decision.production import build_engine
from auto_presenter.index.library import build_index
from auto_presenter.protocol import (
    ParticleFilterTunables,
    SetlistContent,
    SetlistItemContent,
    SetTunablesRequest,
)


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def _indexed():
    return build_index(
        SetlistContent(
            items=[
                SetlistItemContent(
                    item_idx=0,
                    song_id="s",
                    slides=[["all", "to", "jesus"], ["i", "surrender", "all"]],
                    slide_sections=["v1", "c1"],
                )
            ]
        )
    )


@pytest.fixture
def running_engine(monkeypatch):
    engine = build_engine(_indexed())
    monkeypatch.setattr(server_mod._state, "matcher", engine)
    monkeypatch.setattr(server_mod._state, "director", object())
    monkeypatch.setattr(server_mod._state, "clients", set())
    return engine


@pytest.mark.asyncio
async def test_target_margin_reaches_the_forward_filter_engine(running_engine):
    ws = _FakeWS()
    await server_mod._handle_set_tunables(
        ws, SetTunablesRequest(id=1, tunables=ParticleFilterTunables(target_margin_ms=2200))
    )
    assert running_engine.scheduler.config.target_margin_ms == 2200


@pytest.mark.asyncio
async def test_particle_only_knobs_are_skipped_rather_than_raising(running_engine):
    """`n_particles` has no meaning for a dense posterior; the request must
    still succeed and still apply everything that does."""
    ws = _FakeWS()
    await server_mod._handle_set_tunables(
        ws,
        SetTunablesRequest(
            id=1, tunables=ParticleFilterTunables(n_particles=999, marginal_threshold=0.7)
        ),
    )
    assert running_engine.scheduler.config.marginal_threshold == 0.7
    assert ws.sent  # acked, not swallowed by an exception


