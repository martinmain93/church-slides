"""Library search by meaning: rank songs against the UI's parsed query texts.

Editor-time: the library's search box asks. The one other piece of work is
warming the index once the engine has started (`warm_library_search`), so the
first search doesn't wait for it; that build pauses while a service runs.
Requests run off the event loop (a query is ~2 ms to embed, but a search may
first re-embed a few edited songs).
"""

from __future__ import annotations

import asyncio
import logging

from websockets.asyncio.server import ServerConnection

from auto_presenter.library_search.index import LibraryIndex, library_index
from auto_presenter.protocol import SemanticSearchHit, SemanticSearchRequest, SemanticSearchResponse
from auto_presenter.server_state import _send, _state

log = logging.getLogger(__name__)

#: How long after the engine starts before the index is warmed: the app's own
#: start-up (and a service started straight away) goes first.
WARM_DELAY_S = 30.0


def _service_live() -> bool:
    session = _state.audio_session
    return session is not None and session.is_live


def _index() -> LibraryIndex | None:
    return library_index(_state.library_db_path, busy=_service_live)


async def warm_library_search(delay: float = WARM_DELAY_S) -> None:
    """Build or refresh the index in the background, a while after start-up.
    Once a library has been indexed this only reads the cache and re-embeds
    songs edited since."""
    await asyncio.sleep(delay)
    index = _index()
    if index is None:
        return
    try:
        await asyncio.to_thread(index.refresh)
    except Exception:
        log.exception("library search: warm-up failed")


async def _handle_semantic_search(ws: ServerConnection, msg: SemanticSearchRequest) -> None:
    def work() -> SemanticSearchResponse:
        index = _index()
        if index is None:
            return SemanticSearchResponse(id=msg.id, status="unavailable")
        # Filtered searches ("hymns in E about ...") rank the whole library and
        # filter afterwards, so the cap is a sanity bound, not a page size.
        status, hits = index.search(msg.texts, limit=max(1, min(msg.limit, 5000)))
        return SemanticSearchResponse(
            id=msg.id,
            status=status,
            hits=[
                SemanticSearchHit(song_id=h.song_id, score=h.score, passage=h.passage) for h in hits
            ],
        )

    await _send(ws, await asyncio.to_thread(work))
