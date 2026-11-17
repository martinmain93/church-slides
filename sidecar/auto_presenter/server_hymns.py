"""Hymn request handlers: search the Open Hymnal, fetch one hymn.

Editor-time only, and only when an operator opens "Add hymns" — nothing here
runs during a service or on a timer. Both handlers run off the event loop:
the first search of the week fetches three pages (~1 MB) from the Open
Hymnal, and fetching a hymn is another request.

Failures come back as `error` text the dialog shows as-is: offline with no
cached index, the site down, a hymn whose file has no words.
"""

from __future__ import annotations

import asyncio
import logging

from websockets.asyncio.server import ServerConnection

from auto_presenter.hymns import openhymnal
from auto_presenter.protocol import (
    GetHymnRequest,
    GetHymnResponse,
    HymnDetail,
    HymnSummary,
    SearchHymnsRequest,
    SearchHymnsResponse,
)
from auto_presenter.server_state import _send

log = logging.getLogger(__name__)

_OFFLINE = (
    "Couldn't reach the Open Hymnal (openhymnal.org). Check the internet connection and try again."
)


async def _handle_search_hymns(ws: ServerConnection, msg: SearchHymnsRequest) -> None:
    def work() -> SearchHymnsResponse:
        try:
            entries, fetched = openhymnal.load_index(refresh=msg.refresh)
        except OSError:
            log.info("Open Hymnal index unavailable", exc_info=True)
            return SearchHymnsResponse(id=msg.id, error=_OFFLINE)
        results = [
            HymnSummary(
                hymn_id=e.id,
                title=e.title,
                also_known_as=e.also_known_as,
                author=e.author,
                tune=e.tune,
                first_line=e.first_line,
                status=e.status or "check",  # type: ignore[arg-type]
                status_label=e.status_label,
                explanation=e.explanation,
            )
            for e in openhymnal.search(entries, msg.query)
        ]
        return SearchHymnsResponse(id=msg.id, results=results, fetched_at=fetched)

    await _send(ws, await asyncio.to_thread(work))


async def _handle_get_hymn(ws: ServerConnection, msg: GetHymnRequest) -> None:
    def work() -> GetHymnResponse:
        try:
            hymn = openhymnal.fetch_hymn(msg.hymn_id)
        except OSError:
            log.info("fetching hymn %s failed", msg.hymn_id, exc_info=True)
            return GetHymnResponse(id=msg.id, error=_OFFLINE)
        except ValueError as err:
            return GetHymnResponse(id=msg.id, error=str(err))
        return GetHymnResponse(
            id=msg.id,
            hymn=HymnDetail(
                hymn_id=hymn.id,
                title=hymn.title,
                body_text=hymn.body_text,
                slide_order=hymn.slide_order,
                author=hymn.author,
                copyright=hymn.copyright,
                status=hymn.status,  # type: ignore[arg-type]
                status_label=hymn.status_label,
                explanation=hymn.explanation,
                source_url=hymn.source_url,
            ),
        )

    await _send(ws, await asyncio.to_thread(work))
