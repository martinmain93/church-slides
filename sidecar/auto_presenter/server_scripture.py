"""Scripture request handlers: verse lookup, search, install, import.

Split out of `server.py` on the same principle as `server_music` and
`server_instructions` — a self-contained optional feature that never reaches a
decision. Nothing here runs unless the frontend asks; there is no observer on
the transcript path and no timer. The one piece of state carried between
requests is a Bible file being imported, held between its preview and its
commit (see "Importing a church's own Bible" below). Turning the feature off
in settings means these handlers are simply never called.

Two things about the shape of these handlers are deliberate:

**Every one of them runs off the event loop.** `VerseStore` is synchronous
SQLite and `ensure_translation` downloads ~9 MB and builds a 31,000-row
table. Either inline would stall
audio-level broadcasts and transcript events for the duration — the same
reasoning that already puts `InstructionEngine.decide` in a thread.

**Failures come back as data, not as errors.** One unresolvable reference must
not cost the others.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import secrets
import time
from dataclasses import dataclass

from websockets.asyncio.server import ServerConnection

from auto_presenter.protocol import (
    CommitScriptureImportRequest,
    CommitScriptureImportResponse,
    FindScriptureReferencesRequest,
    FindScriptureReferencesResponse,
    InstallScriptureTranslationRequest,
    InstallScriptureTranslationResponse,
    ListScriptureTranslationsRequest,
    ListScriptureTranslationsResponse,
    PassageContextsRequest,
    PassageContextsResponse,
    PreviewScriptureImportRequest,
    PreviewScriptureImportResponse,
    RemoveScriptureTranslationRequest,
    RemoveScriptureTranslationResponse,
    ResolvePassagesRequest,
    ResolvePassagesResponse,
    ScriptureCatalogEntry,
    ScriptureImportBook,
    ScriptureImportReport,
    ScripturePassage,
    ScriptureTranslationInfo,
    ScriptureVerse,
    SearchScriptureCatalogRequest,
    SearchScriptureCatalogResponse,
    SearchScriptureRequest,
    SearchScriptureResponse,
)
from auto_presenter.scripture import (
    DEFAULT_TRANSLATION,
    Passage,
    Reference,
    ReferenceError,
    VerseStore,
    catalog,
    ensure_translation,
    extract,
    sections,
)
from auto_presenter.scripture.importers import (
    BibleImportError,
    ImportReport,
    parse_file,
    plan,
)
from auto_presenter.scripture.store import (
    all_translations,
    db_path,
    install_imported,
    remove_translation,
)
from auto_presenter.server_state import _send

log = logging.getLogger(__name__)


def _to_passage(passage: Passage, store: VerseStore) -> ScripturePassage:
    return ScripturePassage(
        reference=passage.reference,
        translation=passage.translation,
        verses=[ScriptureVerse(ref=v.ref, text=v.text) for v in passage.verses],
        text=passage.text,
        attribution=store.attribution,
        abbreviation=store.abbreviation,
    )


def _open_store(translation: str) -> VerseStore:
    return VerseStore(translation or DEFAULT_TRANSLATION)


async def _handle_resolve_passages(
    ws: ServerConnection, msg: ResolvePassagesRequest
) -> None:
    def work() -> tuple[list[ScripturePassage], dict[str, str], dict[str, str]]:
        passages: list[ScripturePassage] = []
        errors: dict[str, str] = {}
        resolved: dict[str, str] = {}
        try:
            store = _open_store(msg.translation)
        except FileNotFoundError as err:
            return [], {r: str(err) for r in msg.references}, {}
        with store:
            for reference in msg.references:
                try:
                    passage = _to_passage(store.resolve(reference), store)
                except ReferenceError as err:
                    # Data, not an exception: a setlist with one bad reference
                    # should still display the other nineteen, with the bad one
                    # flagged in the editor.
                    errors[reference] = str(err)
                    continue
                passages.append(passage)
                resolved[reference] = passage.reference
        return passages, errors, resolved

    passages, errors, resolved = await asyncio.to_thread(work)
    await _send(
        ws,
        ResolvePassagesResponse(
            id=msg.id, passages=passages, errors=errors, resolved=resolved
        ),
    )


def _hosted_model() -> tuple[str, str, str] | None:
    """(base_url, model, key) of the chosen hosted instruction model, if its
    key is on file. Reading a pasted message is prep, not a live service, so
    there is no local fallback to wait on — the phrase reading covers it."""
    from auto_presenter.api_keys import endpoint
    from auto_presenter.decision.instruction_models import find_model
    from auto_presenter.server_state import _state

    spec = find_model(_state.instruction_model_id)
    if spec is None or spec.kind != "hosted":
        return None
    where = endpoint(spec.provider, spec.base_url)
    if where is None:
        return None
    return where[0], spec.model_name, where[1]


async def _handle_find_scripture_references(
    ws: ServerConnection, msg: FindScriptureReferencesRequest
) -> None:
    def work() -> FindScriptureReferencesResponse:
        try:
            store = _open_store(msg.translation)
        except FileNotFoundError:
            return FindScriptureReferencesResponse(id=msg.id, passages=[])
        named: list[str] | None = None
        hosted = _hosted_model()
        if hosted is not None and msg.text.strip():
            base_url, model, key = hosted
            named = extract.ask_model(msg.text, base_url=base_url, model=model, api_key=key)
        with store:
            if named is not None:
                found = extract.resolve_named(named, store.resolve)
                unresolved = [r for r in named if not extract.resolve_named([r], store.resolve)]
                return FindScriptureReferencesResponse(
                    id=msg.id,
                    passages=[_to_passage(p, store) for p in found],
                    unresolved=unresolved,
                    used_model=True,
                )
            found = extract.read_phrases(msg.text, store.resolve)
            return FindScriptureReferencesResponse(
                id=msg.id, passages=[_to_passage(p, store) for p in found]
            )

    await _send(ws, await asyncio.to_thread(work))


async def _handle_passage_contexts(
    ws: ServerConnection, msg: PassageContextsRequest
) -> None:
    """"John 3:16" -> 3:16–21, 3:1–21 ("Jesus and Nicodemus"), John 3 — in
    the translation asked for, so offering them costs nothing to show."""

    def work() -> list[ScripturePassage]:
        try:
            store = _open_store(msg.translation)
        except FileNotFoundError:
            return []
        with store:
            try:
                asked = store.resolve(msg.reference)
            except ReferenceError:
                return []
            if len(asked.verses) != 1:
                return []
            v = asked.verses[0]

            def span(first: int, last: int) -> Passage:
                return store.resolve(
                    Reference(book=v.book.lower(), chapter=v.chapter,
                              first_verse=first, last_verse=last, display="")
                )

            last_in_chapter = span(1, 999).verses[-1].verse
            code = store.book_code(v.book) or ""
            out: list[ScripturePassage] = []
            for c in sections.contexts(code, v.chapter, v.verse, last_in_chapter):
                passage = _to_passage(span(c.first, c.last), store)
                passage.heading = c.heading
                out.append(passage)
            return out

    passages = await asyncio.to_thread(work)
    await _send(ws, PassageContextsResponse(id=msg.id, passages=passages))


async def _handle_search_scripture(
    ws: ServerConnection, msg: SearchScriptureRequest
) -> None:
    def work() -> list[ScripturePassage]:
        try:
            store = _open_store(msg.translation)
        except FileNotFoundError:
            log.info("scripture search before any translation is installed")
            return []
        with store:
            return [
                _to_passage(p, store)
                for p in store.search(msg.text, limit=msg.limit)
            ]

    passages = await asyncio.to_thread(work)
    await _send(ws, SearchScriptureResponse(id=msg.id, passages=passages))


async def _handle_list_scripture_translations(
    ws: ServerConnection, msg: ListScriptureTranslationsRequest
) -> None:
    infos = await asyncio.to_thread(all_translations)
    translations = [
        ScriptureTranslationInfo(
            id=info.id,
            name=info.name,
            attribution=info.attribution,
            installed=info.installed,
            abbreviation=info.abbreviation,
            description=info.description,
            imported=info.imported,
            language=info.language,
        )
        for info in infos
    ]
    await _send(
        ws,
        ListScriptureTranslationsResponse(
            id=msg.id,
            translations=translations,
            default_translation=DEFAULT_TRANSLATION,
        ),
    )


async def _handle_install_scripture_translation(
    ws: ServerConnection, msg: InstallScriptureTranslationRequest
) -> None:
    """Download and build a translation. Several seconds, hence the thread.

    No progress stream: the whole thing is one ~9 MB request plus a local
    build, and a spinner is an honest representation of that. If a much larger
    text ever lands here, this is where a progress message would go — the
    instruction-model downloader next door is the pattern to copy.
    """

    def work() -> tuple[str, str | None]:
        try:
            return ensure_translation(msg.translation), None
        except KeyError:
            return msg.translation, f"unknown translation {msg.translation!r}"
        except Exception as err:  # noqa: BLE001 — surfaced to the UI verbatim
            log.exception("installing %s failed", msg.translation)
            return msg.translation, str(err)

    translation, error = await asyncio.to_thread(work)
    await _send(
        ws,
        InstallScriptureTranslationResponse(
            id=msg.id,
            translation=translation,
            installed=error is None,
            error=error,
        ),
    )


async def _handle_search_scripture_catalog(
    ws: ServerConnection, msg: SearchScriptureCatalogRequest
) -> None:
    """Search eBible.org's public-domain translations. The list is fetched at
    most weekly and cached; offline, the cached copy answers."""

    def work() -> SearchScriptureCatalogResponse:
        try:
            entries = catalog.load_catalog(refresh=msg.refresh)
        except OSError:
            log.info("eBible.org catalogue unavailable", exc_info=True)
            return SearchScriptureCatalogResponse(
                id=msg.id,
                error="Couldn't reach eBible.org. Check the internet connection and try again.",
            )
        return SearchScriptureCatalogResponse(
            id=msg.id,
            entries=[
                ScriptureCatalogEntry(
                    id=e.id,
                    store_id=e.store_id,
                    title=e.title,
                    abbreviation=e.abbreviation,
                    description=e.description,
                    language=e.language,
                    language_code=e.language_code,
                    ot_books=e.ot_books,
                    nt_books=e.nt_books,
                    dc_books=e.dc_books,
                    installed=db_path(e.store_id).exists(),
                )
                for e in catalog.search(entries, msg.query)
            ],
        )

    await _send(ws, await asyncio.to_thread(work))


async def _handle_remove_scripture_translation(
    ws: ServerConnection, msg: RemoveScriptureTranslationRequest
) -> None:
    def work() -> tuple[bool, str | None]:
        try:
            return remove_translation(msg.translation), None
        except (ValueError, OSError) as err:
            return False, str(err)

    removed, error = await asyncio.to_thread(work)
    await _send(
        ws,
        RemoveScriptureTranslationResponse(
            id=msg.id, translation=msg.translation, removed=removed, error=error
        ),
    )


# --- Importing a church's own Bible ---------------------------------------
#
# Two steps, because nothing should be installed that the operator hasn't
# seen the report for: `preview` parses and reports, `commit` installs what
# was previewed. Between them the parsed Bible waits here, keyed by a token,
# so the file crosses the socket once. A sidecar restart in between loses it;
# the commit then says to choose the file again, which is the honest answer.


@dataclass
class _PendingImport:
    report: ImportReport
    file_name: str
    created: float


_PENDING: dict[str, _PendingImport] = {}
#: A parsed Bible is tens of MB in memory; keep only the latest few, briefly.
_PENDING_MAX = 2
_PENDING_TTL_SECONDS = 30 * 60


def _remember(report: ImportReport, file_name: str) -> str:
    now = time.monotonic()
    for token in [t for t, p in _PENDING.items() if now - p.created > _PENDING_TTL_SECONDS]:
        del _PENDING[token]
    while len(_PENDING) >= _PENDING_MAX:
        del _PENDING[next(iter(_PENDING))]
    token = secrets.token_hex(8)
    _PENDING[token] = _PendingImport(report, file_name, now)
    return token


def _to_report(report: ImportReport) -> ScriptureImportReport:
    return ScriptureImportReport(
        format=report.format,
        title=report.title,
        abbreviation=report.abbreviation,
        rights=report.rights,
        language=report.language,
        verse_count=report.verse_count,
        books=[
            ScriptureImportBook(
                code=p.code or "",
                name=p.name,
                source_name=p.raw.name,
                matched_by=p.matched_by,
                chapters=len(p.raw.chapters),
                verses=p.raw.verse_count,
                section=p.book.section if p.book else "",
            )
            for p in report.books
        ],
        missing=report.missing,
        unmatched=report.unmatched,
        deuterocanon=report.deuterocanon,
        incomplete=report.incomplete,
        merged_verses=report.merged_verses,
        empty_verses=report.empty_verses,
        warnings=report.warnings,
    )


async def _handle_preview_scripture_import(
    ws: ServerConnection, msg: PreviewScriptureImportRequest
) -> None:
    def work() -> PreviewScriptureImportResponse:
        try:
            data = base64.b64decode(msg.data_base64, validate=True)
        except (binascii.Error, ValueError):
            return PreviewScriptureImportResponse(id=msg.id, error="The file didn't arrive intact.")
        try:
            report = plan(parse_file(data, msg.file_name))
        except BibleImportError as err:
            return PreviewScriptureImportResponse(id=msg.id, error=str(err))
        except Exception:
            # A malformed file must not cost the socket.
            # Logged without the file's contents: a church's Bible text never
            # goes into a log or an error report.
            log.exception("reading Bible file %r failed", msg.file_name)
            return PreviewScriptureImportResponse(
                id=msg.id,
                error="Something in this file couldn't be read. It may be damaged, "
                "or in a variant of the format we haven't seen.",
            )
        return PreviewScriptureImportResponse(
            id=msg.id, token=_remember(report, msg.file_name), report=_to_report(report)
        )

    await _send(ws, await asyncio.to_thread(work))


async def _handle_commit_scripture_import(
    ws: ServerConnection, msg: CommitScriptureImportRequest
) -> None:
    def work() -> CommitScriptureImportResponse:
        if not msg.rights_confirmed:
            return CommitScriptureImportResponse(
                id=msg.id, error="Confirm that your church has the right to use this translation."
            )
        if not (msg.name.strip() and msg.abbreviation.strip() and msg.attribution.strip()):
            return CommitScriptureImportResponse(
                id=msg.id, error="A name, a short name and a credit line are all needed."
            )
        pending = _PENDING.pop(msg.token, None)
        if pending is None:
            return CommitScriptureImportResponse(
                id=msg.id, error="That import has expired — choose the file again."
            )
        try:
            translation = install_imported(
                pending.report,
                name=msg.name,
                abbreviation=msg.abbreviation,
                attribution=msg.attribution,
                source_file=pending.file_name,
            )
        except Exception as err:  # surfaced to the UI verbatim
            log.exception("installing imported Bible failed")
            return CommitScriptureImportResponse(id=msg.id, error=f"Couldn't install it: {err}")
        return CommitScriptureImportResponse(id=msg.id, translation=translation)

    await _send(ws, await asyncio.to_thread(work))
