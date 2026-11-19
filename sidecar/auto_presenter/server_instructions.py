"""Spoken-instruction orchestration: the wake gate, the model catalog, and
the WS handlers around both.

Split out of `server.py` — see that module's docstring, and
`server_state.py`'s, for why. `_observe_spoken_instructions` is called from
`server.py`'s transcript handler (audio-session core, which stays there for
now); everything else here — enabling the feature, picking a model,
downloading one, saving a hosted key — is reached only from `_dispatch`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time

from websockets.asyncio.server import ServerConnection

from auto_presenter.api_keys import get_key as get_instruction_api_key
from auto_presenter.api_keys import hosted_gateway
from auto_presenter.api_keys import save_key as save_instruction_api_key
from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.decision import ollama_manager
from auto_presenter.decision.director import Director
from auto_presenter.decision.engine import MatchingEngine
from auto_presenter.decision.instruction_models import (
    CATALOG as INSTRUCTION_MODEL_CATALOG,
)
from auto_presenter.decision.instruction_models import (
    FALLBACK_MODEL_ID,
)
from auto_presenter.decision.instruction_models import find_model as find_instruction_model
from auto_presenter.decision.instructions import (
    FallbackBackend,
    GenericLLMBackend,
    GoToSlide,
    InstructionBackend,
    InstructionEngine,
    OllamaBackend,
    ShowScripture,
    WaitForMore,
    looks_like_instruction,
    unsung_part,
)
from auto_presenter.decision.library_recovery import ImportSong
from auto_presenter.index.types import IndexedSetlist
from auto_presenter.protocol import (
    DownloadInstructionModelRequest,
    InstructionModelDownloadProgressMessage,
    InstructionModelInfo,
    ListInstructionModelsRequest,
    ListInstructionModelsResponse,
    PongMessage,
    SetInstructionModelApiKeyRequest,
    SetInstructionModelRequest,
    SetSpokenInstructionsRequest,
    ShowScriptureMessage,
    SlideChangeMessage,
    SpokenInstructionMessage,
)
from auto_presenter.scripture import (
    DEFAULT_TRANSLATION,
    ReferenceError,
    VerseStore,
    installed_translations,
    match_translation,
)
from auto_presenter.server_library import apply_imported_song
from auto_presenter.server_state import (
    _broadcast,
    _indexed_setlist,
    _matcher_belief,
    _send,
    _send_error,
    _state,
)

log = logging.getLogger(__name__)


def _scripture_checker():
    """A reference -> canonical-reference check against an installed
    translation, or `None` when none is installed (and so the model is not
    offered the scripture tools at all).

    Any installed translation will do: it is only asked whether a passage
    exists, and every translation has the same books, chapters and verses.
    The text itself is looked up by the frontend, in the operator's chosen
    translation, exactly as a typed reference is.
    """
    installed = installed_translations()
    if not installed:
        return None
    translation = DEFAULT_TRANSLATION if DEFAULT_TRANSLATION in installed else installed[0]

    def check(reference: str) -> str | None:
        try:
            with VerseStore(translation) as store:
                return store.resolve(reference).reference
        except (ReferenceError, FileNotFoundError):
            return None

    return check


async def _observe_spoken_instructions(
    matcher: MatchingEngine, director: Director, event: TranscriptEvent
) -> None:
    """Buffer words while someone talks, and act on a flushed utterance.

    Runs every transcript event but does nothing unless the feature is on —
    same shape as `_observe_singing`. The model call is blocking I/O
    (`urllib`, not `aiohttp`), so it runs off the event loop; at the measured
    ~1 s per call, doing it inline would stall every other broadcast (audio
    levels, transcript events) for the duration of every utterance.
    """
    if not _state.spoken_instructions_enabled or _state.instruction_engine is None:
        return
    # With the AI off nothing listens, voice included — transcription is
    # paused then (`transcription_needed`), and this catches the last pass
    # still in flight when the operator switches it off.
    if director.mode == "off":
        _state.utterance_buffer.reset()
        _state.sentence_gate.reset()
        return
    if event.t_end is None:
        return
    # No acoustic score any more: the buffer triggers on the words. See
    # `UtteranceBuffer` — the talking score could not represent a short
    # command at all, and removing its rate term measured worse rather than
    # better.
    utterance = _state.utterance_buffer.observe(
        [w.text for w in event.words],
        event.t_end,
        # The backend's own sentence boundary, when it reports one. Without it
        # the buffer can only guess where a request ended from time gaps.
        ends_segment=event.ends_segment,
    )
    _state.voice_last_t = event.t_end
    if utterance is None:
        return
    # Worship lyrics are full of trigger words ("you were *singing* over
    # me"). If what's left once the sung lines are taken out holds none, this
    # is the band, not a request — and asking the model about every sung line
    # is what ran a hosted free tier out of calls mid-song.
    setlist = _indexed_setlist(matcher)
    if isinstance(setlist, IndexedSetlist) and not looks_like_instruction(
        unsung_part(utterance, setlist).split()
    ):
        return
    # Not straight to the model: a request is offered again every time it
    # grows, and acting on each version put a half-heard request on screen.
    # The gate lets it through once it's finished (`SentenceGate`).
    released = _state.sentence_gate.offer(
        utterance, time.monotonic(), ends_segment=event.ends_segment
    )
    if released is None:
        _start_gate_ticker()
        return
    await _dispatch(released, matcher, director)


GATE_TICK_SECONDS = 0.25


def _start_gate_ticker() -> None:
    """Release a held request after a pause — nothing else would: no
    transcript events arrive while the speaker is quiet."""
    ticker = _state.voice_gate_ticker
    if ticker is not None and not ticker.done():
        return
    _state.voice_gate_ticker = asyncio.get_running_loop().create_task(_gate_loop())


async def _gate_loop() -> None:
    while _state.sentence_gate.holding:
        await asyncio.sleep(GATE_TICK_SECONDS)
        released = _state.sentence_gate.poll(time.monotonic())
        matcher, director = _state.matcher, _state.director
        if released is not None and matcher is not None and director is not None:
            await _dispatch(released, matcher, director)


async def _dispatch(utterance: str, matcher: MatchingEngine, director: Director) -> None:
    """One model call at a time. A request released while one is out waits;
    a newer one replaces it, and also replaces the result of the call that is
    out — which answers an older version of what's being said. (Overlapping
    calls also used to finish out of order, a hosted backend's latency
    varying call to call, and burned a free tier's rate limit.)"""
    if _state.voice_busy:
        _state.voice_next = utterance
        return
    _state.voice_busy = True
    try:
        next_up: str | None = utterance
        while next_up is not None:
            await _decide_and_apply(next_up, matcher, director)
            next_up, _state.voice_next = _state.voice_next, None
    finally:
        _state.voice_busy = False


async def _decide_and_apply(
    utterance: str, matcher: MatchingEngine, director: Director
) -> None:
    if director.mode == "off":
        return
    setlist = _indexed_setlist(matcher)
    if setlist is None:
        return
    belief = _matcher_belief(matcher)
    current_slide_idx = belief.slide_idx if belief is not None else None
    engine = _state.instruction_engine
    if engine is None:
        return
    t_seconds = _state.voice_last_t
    result = await asyncio.to_thread(
        engine.decide, utterance, setlist, matcher.current_item_idx, current_slide_idx,
        _state.library_index, scripture=_scripture_checker(),
        passage_on_screen=_state.scripture_on_screen,
        passage_last=_state.scripture_last,
    )
    if _state.voice_next is not None:
        # A newer version of what's being said is already waiting: this
        # answer is to an older one. «…» marks what someone said — the local
        # log keeps it, error reports strip it (a service's speech can be a
        # prayer request).
        log.info(
            "spoken instruction «%s» superseded by a newer utterance; dropping stale result",
            utterance,
        )
        return
    if isinstance(result, WaitForMore):
        # The model heard the start of a request: keep listening for the
        # rest, even without a trigger word ("...of Build My Life").
        _state.utterance_buffer.keep_listening(t_seconds)
        log.info("spoken instruction «%s» -> waiting to hear the rest", utterance)
        return
    applied = False
    item_idx = None
    slide_idx = None
    song_id = None
    title = None
    score = None
    scripture_reference = None
    scripture_display = None
    if isinstance(result, ShowScripture):
        # Not a slide move: the screens lay a passage over (or beside) the
        # lyrics, and the AI keeps following the song underneath.
        # A named translation that isn't installed (or is ambiguous) falls
        # back to the one in use: the verse still shows, in the usual text.
        translation = match_translation(result.translation) if result.translation else None
        if result.translation and translation is None:
            log.info("spoken translation «%s» isn't installed; using the current one",
                     result.translation)
        await _broadcast(ShowScriptureMessage(
            reference=result.reference, display=result.display,
            translation=translation or "",
        ))
        applied = True
        scripture_reference = result.reference
        scripture_display = result.display
        log.info(
            "spoken instruction «%s» -> scripture %s (%s)",
            utterance, result.reference or "-", result.display,
        )
    elif isinstance(result, ImportSong):
        landed = await apply_imported_song(
            matcher, director, result, t_seconds=t_seconds
        )
        applied = landed is not None
        item_idx = landed
        slide_idx = result.slide_idx if applied else None
        song_id = result.song_id
        title = result.title
        score = result.score
        if applied:
            log.info(
                "spoken instruction «%s» -> imported %s item %d slide %d",
                utterance, result.title, landed, result.slide_idx,
            )
    elif isinstance(result, GoToSlide):
        director.on_override(
            item_idx=result.item_idx, slide_idx=result.slide_idx, t_seconds=t_seconds
        )
        await _broadcast(SlideChangeMessage(
            item_idx=result.item_idx, slide_idx=result.slide_idx,
            confidence=1.0, source="voice",
        ))
        applied = True
        item_idx = result.item_idx
        slide_idx = result.slide_idx
        log.info(
            "spoken instruction «%s» -> item %d slide %d",
            utterance, result.item_idx, result.slide_idx,
        )
    else:
        log.info("spoken instruction «%s» -> no action", utterance)
    if applied:
        # Acted on: forget these words, so the next request doesn't arrive
        # with this one still attached and get acted on twice.
        _state.utterance_buffer.consume()
    await _broadcast(SpokenInstructionMessage(
        t_seconds=t_seconds,
        utterance=utterance,
        item_idx=item_idx,
        slide_idx=slide_idx,
        applied=applied,
        song_id=song_id,
        title=title,
        score=score,
        scripture_reference=scripture_reference,
        scripture_display=scripture_display,
    ))


def note_live_scripture(reference: str, display: str) -> None:
    """The screens say which passage is up (`LiveScriptureNotification`)."""
    if display == "off" or not reference:
        _state.scripture_on_screen = ""
        return
    _state.scripture_on_screen = reference
    _state.scripture_last = reference


def _hosted_endpoint(spec) -> tuple[str, str] | None:
    """(base_url, key) for a hosted model: the person's own key first, then
    the organization's AI allowance (`api_keys.endpoint`)."""
    own = get_instruction_api_key(spec.provider)
    if own:
        return spec.base_url.rstrip("/"), own
    return hosted_gateway() if spec.provider == "groq" else None


def hosted_key_on_file() -> bool:
    """Whether the chosen instruction model is a hosted one it can reach: a
    key on file, or the organization's AI allowance. What "on by default"
    means for spoken requests."""
    spec = find_instruction_model(_state.instruction_model_id)
    return spec is not None and spec.kind == "hosted" and _hosted_endpoint(spec) is not None


def apply_session_spoken_instructions(choice: bool | None) -> bool:
    """Set spoken requests for a starting service, and say what was chosen.

    `choice` is the operator's saved setting; None means they never chose,
    and the feature is on when a hosted model can run (its key is on file).
    The hosted model is the one measured good enough to act on what is heard
    (15/15, 0.5 s); with no key there would be only the on-device fallback,
    which nobody asked to have listening.
    """
    enabled = choice if choice is not None else hosted_key_on_file()
    _state.spoken_instructions_enabled = enabled
    if enabled and _state.instruction_engine is None:
        _rebuild_instruction_engine()
    if not enabled:
        _state.utterance_buffer.reset()
        _state.sentence_gate.reset()
    log.info(
        "spoken instructions %s for this service (%s)",
        "on" if enabled else "off",
        "saved setting" if choice is not None else "default: hosted key on file"
        if enabled else "default: no hosted key",
    )
    return enabled


async def _handle_set_spoken_instructions(
    ws: ServerConnection, msg: SetSpokenInstructionsRequest
) -> None:
    _state.spoken_instructions_enabled = msg.enabled
    if msg.enabled and _state.instruction_engine is None:
        _rebuild_instruction_engine()
    if not msg.enabled:
        _state.utterance_buffer.reset()
        _state.sentence_gate.reset()
    log.info("spoken instructions %s", "on" if msg.enabled else "off")
    await _send(ws, PongMessage(id=msg.id))


#: `qwen2.5:3b-instruct` (`_state.instruction_model_id`'s default) is a
#: strict upgrade over the first model measured (llama3.2:3b), found
#: 2026-09-03 after the initial "qwen is too slow" reading turned out to be
#: resource contention from a long session, not the model — re-tested
#: uncontended at the same ~0.7-1.5 s/call as llama3.2:3b, one size class,
#: same tool-calling API. 8/10 synthetic commands correct (vs 7/10) and
#: 10/10 correctly silent, and it resolves the one case prompt-engineering
#: could not fix on llama3.2:3b ("let's do the second verse one more time"
#: -> verse:2, not a confidently-wrong verse:1). Still only synthetic, typed
#: utterances — see `docs/done/SPOKEN_INSTRUCTIONS.md`.
def _build_instruction_backend(model_id: str) -> InstructionBackend | None:
    """Turn a catalog id into a callable backend, or `None` with a logged
    reason if it can't be built right now (unknown id, hosted model with no
    key on file). `None` is the same "nothing to do" signal the backend
    protocol already uses for a failed call — building no backend and
    failing every call are the same outcome from the engine's point of view,
    so callers can treat this exactly like an unreachable server.
    """
    spec = find_instruction_model(model_id)
    if spec is None:
        log.warning("unknown instruction model id %r", model_id)
        return None
    if spec.kind == "local":
        return OllamaBackend(model=spec.model_name, host=ollama_manager.DEFAULT_HOST)
    where = _hosted_endpoint(spec)
    if where is None:
        # No key: fall all the way back rather than disabling the feature.
        # A hosted default that silently does nothing when no key was ever
        # entered is worse than quietly running on-device.
        log.warning(
            "instruction model %r needs a %s API key — none on file; "
            "falling back to %s", model_id, spec.provider, FALLBACK_MODEL_ID
        )
        return _build_local_fallback()
    base_url, api_key = where
    hosted = GenericLLMBackend(
        base_url=base_url, model=spec.model_name, api_key=api_key
    )
    local = _build_local_fallback()
    if local is None:
        return hosted
    # A worship service is exactly where the wifi fails and the failure is
    # public, which is why the catalog long refused to default to a hosted
    # model. The objection is against *depending* on a network, not against
    # using one: with a fallback, losing the connection costs accuracy rather
    # than the feature.
    return FallbackBackend(primary=hosted, secondary=local)


def _build_local_fallback() -> InstructionBackend | None:
    """The on-device model, if it is actually installed."""
    spec = find_instruction_model(FALLBACK_MODEL_ID)
    if spec is None or spec.kind != "local":
        return None
    if spec.pull_name and spec.pull_name not in ollama_manager.installed_models():
        log.warning("fallback model %r is not installed", FALLBACK_MODEL_ID)
        return None
    return OllamaBackend(model=spec.model_name, host=ollama_manager.DEFAULT_HOST)


def _rebuild_instruction_engine() -> None:
    """(Re)build the engine against `_state.instruction_model_id`.

    Called on enable, on a model switch, and on a key change for the
    currently-active hosted provider. If the backend can't be built (unknown
    id, hosted model with no key), the engine is cleared rather than left
    pointed at a stale model — every call then behaves like an unreachable
    server (returns `None`), which is the existing, already-safe failure
    mode, not a new one.
    """
    backend = _build_instruction_backend(_state.instruction_model_id)
    _state.instruction_engine = InstructionEngine(backend=backend) if backend else None


def _instruction_model_info(model_id: str) -> InstructionModelInfo | None:
    spec = find_instruction_model(model_id)
    if spec is None:
        return None
    installed = spec.kind == "local" and spec.pull_name in ollama_manager.installed_models()
    # The organization's AI allowance counts: nothing to enter.
    has_key = spec.kind == "hosted" and _hosted_endpoint(spec) is not None
    return InstructionModelInfo(
        id=spec.id, label=spec.label, model_kind=spec.kind, size_mb=spec.size_mb,
        provider=spec.provider, note=spec.note, installed=installed, has_api_key=has_key,
    )


async def _handle_list_instruction_models(
    ws: ServerConnection, msg: ListInstructionModelsRequest
) -> None:
    ollama_available = await asyncio.to_thread(ollama_manager.is_available)
    models = [_instruction_model_info(spec.id) for spec in INSTRUCTION_MODEL_CATALOG]
    await _send(ws, ListInstructionModelsResponse(
        id=msg.id,
        models=[m for m in models if m is not None],
        active_model_id=_state.instruction_model_id,
        ollama_available=ollama_available,
    ))


async def _handle_set_instruction_model(
    ws: ServerConnection, msg: SetInstructionModelRequest
) -> None:
    if find_instruction_model(msg.model_id) is None:
        await _send_error(ws, msg.id, f"unknown instruction model: {msg.model_id!r}")
        return
    _state.instruction_model_id = msg.model_id
    if _state.spoken_instructions_enabled:
        _rebuild_instruction_engine()
    log.info("spoken instructions model set to %s", msg.model_id)
    await _send(ws, PongMessage(id=msg.id))


async def _handle_download_instruction_model(
    ws: ServerConnection, msg: DownloadInstructionModelRequest
) -> None:
    spec = find_instruction_model(msg.model_id)
    if spec is None or spec.kind != "local":
        await _send_error(ws, msg.id, f"not a downloadable local model: {msg.model_id!r}")
        return
    loop = asyncio.get_running_loop()
    sent: list[concurrent.futures.Future[None]] = []

    def on_progress(event: ollama_manager.PullProgressEvent) -> None:
        # Runs on the worker thread `pull()` blocks in — hop back onto the
        # event loop to broadcast, same pattern the audio capture thread
        # uses for its own cross-thread broadcasts.
        sent.append(asyncio.run_coroutine_threadsafe(
            _broadcast(InstructionModelDownloadProgressMessage(
                model_id=msg.model_id,
                bytes_downloaded=event.bytes_downloaded,
                bytes_total=event.bytes_total,
                fraction=event.fraction,
                state=event.state,
                detail=event.detail,
            )),
            loop,
        ))

    await _send(ws, PongMessage(id=msg.id))
    try:
        await asyncio.to_thread(
            ollama_manager.pull, spec.pull_name, progress_callback=on_progress
        )
    except Exception:  # pragma: no cover - defensive; on_progress already
        # broadcast a "failed" event with the real reason before this raised.
        log.exception("download of %s failed", msg.model_id)
    # The last progress message ("completed" or "failed") was handed to the
    # loop from the worker thread; finish sending it before this returns, or
    # it can arrive after the download is reported done (seen on Linux CI).
    await asyncio.gather(*(asyncio.wrap_future(f) for f in sent), return_exceptions=True)


async def _handle_set_instruction_model_api_key(
    ws: ServerConnection, msg: SetInstructionModelApiKeyRequest
) -> None:
    save_instruction_api_key(msg.provider, msg.api_key)
    active = find_instruction_model(_state.instruction_model_id)
    active_matches = active is not None and active.provider == msg.provider
    if _state.spoken_instructions_enabled and active_matches:
        _rebuild_instruction_engine()
    log.info("%s API key %s", msg.provider, "saved" if msg.api_key else "cleared")
    await _send(ws, PongMessage(id=msg.id))
