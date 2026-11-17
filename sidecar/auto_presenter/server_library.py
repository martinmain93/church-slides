"""Off-setlist recovery: load the library index and apply an import.

Split out of `server.py` so the transcript handler can ask "is this an
unplanned song?" without that module growing another 200 lines of retrieval
logic. The matcher is re-indexed here; the UI is told via
`ImportLibrarySongMessage` so it can persist the same append.
"""

from __future__ import annotations

import asyncio
import logging

from auto_presenter.decision.director import Director
from auto_presenter.decision.engine import MatchingEngine
from auto_presenter.decision.library_recovery import (
    ImportSong,
    OffSetlistWatcher,
    append_imported_song,
    setlist_content_from_indexed,
)
from auto_presenter.index.catalog import load_catalog
from auto_presenter.index.fingerprint import FingerprintIndex
from auto_presenter.protocol import ImportLibrarySongMessage, LibrarySearchMessage
from auto_presenter.server_follower import build_session_openings
from auto_presenter.server_state import (
    _broadcast,
    _indexed_setlist,
    _matcher_belief,
    _state,
)

log = logging.getLogger(__name__)


def load_library_index() -> FingerprintIndex | None:
    """Build (or rebuild) the fingerprint index from `library.db`.

    Cheap relative to loading Whisper: a few hundred songs, one pass. Done
    on session start so a song added between services is visible without a
    sidecar restart.
    """
    songs = load_catalog(_state.library_db_path)
    if not songs:
        log.info("library index: empty (no db or no songs)")
        return None
    index = FingerprintIndex.from_songs(songs)
    log.info("library index: %d songs, %d fingerprints",
             len(index.songs), len(index._postings))
    return index


def prepare_library_search() -> None:
    """(Re)load the index and arm the spontaneous watcher for this session."""
    index = load_library_index()
    _state.library_index = index
    _state.off_setlist = OffSetlistWatcher(index) if index is not None else None


def clear_library_search() -> None:
    if _state.off_setlist is not None:
        _state.off_setlist.reset()
    _state.off_setlist = None
    # Keep `library_index` — the next session can reuse it if the db
    # hasn't moved; `prepare_library_search` rebuilds anyway.


async def _refresh_openings(indexed) -> None:
    """Listen for the imported song's lines too, and compare against it.

    The openings watch is built for the setlist a session starts with. A song
    imported by voice was unknown to it, so while that song was sung its
    "current song" score was nothing at all and any other song's line won:
    live, 2026-10-15, Build My Life was cut to Agnus Dei by «worthy is the
    lamb worthy» (gain 2.9 against -inf). An operator's edit already rebuilt
    the watch (`_handle_update_setlist_content`); this is the voice path.
    """
    session = _state.audio_session
    if _state.openings is None or session is None:
        return
    try:
        _state.openings = await asyncio.to_thread(
            build_session_openings, setlist_content_from_indexed(indexed), session._backend
        )
    except Exception:
        log.exception("opening-line watch failed to rebuild after an import; off for this session")
        _state.openings = None


async def apply_imported_song(
    matcher: MatchingEngine,
    director: Director,
    imported: ImportSong,
    *,
    t_seconds: float,
) -> int | None:
    """Append (if needed), re-anchor, and tell the UI.

    Returns the item index landed on, or None if the matcher has no setlist.
    """
    setlist = _indexed_setlist(matcher)
    if setlist is None:
        return None
    indexed, item_idx = append_imported_song(setlist, imported)
    if not imported.already_in_setlist:
        matcher.reindex(indexed, item_idx)
        await _refresh_openings(indexed)
    if imported.reason == "spoken":
        director.on_override(
            item_idx=item_idx, slide_idx=imported.slide_idx, t_seconds=t_seconds
        )
        source = "voice"
    else:
        matcher.commit_external(item_idx, imported.slide_idx)
        source = "ai"
    await _broadcast(ImportLibrarySongMessage(
        song_id=imported.song_id,
        title=imported.title,
        item_idx=item_idx,
        slide_idx=imported.slide_idx,
        section_key=imported.section_key,
        score=imported.score,
        runner_up_score=imported.runner_up_score,
        query=imported.query,
        reason=imported.reason,
        source=source,
    ))
    await _broadcast(LibrarySearchMessage(
        t_seconds=t_seconds,
        reason=imported.reason,
        query=imported.query,
        song_id=imported.song_id,
        title=imported.title,
        score=imported.score,
        runner_up_score=imported.runner_up_score,
        applied=True,
        detail="imported" if not imported.already_in_setlist else "already in set",
    ))
    log.info(
        "library %s %r -> item %d slide %d (score %.2f)",
        imported.reason, imported.title, item_idx, imported.slide_idx, imported.score,
    )
    return item_idx


async def observe_off_setlist(
    matcher: MatchingEngine, director: Director, event
) -> None:
    watcher = _state.off_setlist
    if watcher is None or event.t_end is None:
        return
    setlist = _indexed_setlist(matcher)
    belief = _matcher_belief(matcher)
    if setlist is None or belief is None:
        return
    imported = watcher.observe(
        words=[w.text for w in event.words],
        t_seconds=event.t_end,
        display_marginal=float(getattr(belief, "display_marginal", 1.0)),
        talking_score=_state.singing.score,
        silent=_state.singing.silent,
        setlist=setlist,
        chord=_state.last_chord,
        chord_t=_state.last_chord_t,
        chords_enabled=_state.music_chords,
    )
    if imported is None:
        return
    if director.mode != "active" and imported.reason != "spoken":
        # Only Auto may put a song into the setlist by itself. Elsewhere the
        # UI ignores the import (`usePresentationSession`), and appending it
        # here anyway left the engine's setlist one song longer than the
        # screen's.
        return
    await apply_imported_song(matcher, director, imported, t_seconds=event.t_end)
