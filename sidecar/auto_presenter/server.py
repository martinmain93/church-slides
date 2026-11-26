"""WebSocket server.

Accepts JSON messages from connected clients (the React UI), dispatches them by
`kind`, and replies on the same socket. Maintains a singleton `AudioSession`
so transcript events can be broadcast to every connected client (useful when
the operator has the Settings page open in one window and the debug panel in
another).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import websockets
from pydantic import TypeAdapter, ValidationError
from websockets.asyncio.server import ServerConnection

from auto_presenter import server_assist
from auto_presenter.api_keys import (
    get_key as get_instruction_api_key,
)
from auto_presenter.asr.backend import ASRBackend
from auto_presenter.asr.groq_backend import GroqWhisperBackend
from auto_presenter.asr.hosted_backend import HostedCredentials, HostedSpeechBackend
from auto_presenter.asr.junk import DoubtfulWordFilter
from auto_presenter.asr.machine_check import ASSIST_TRANSCRIBE_EVERY_S
from auto_presenter.asr.mlx_backend import MLXWhisperBackend
from auto_presenter.asr.model_manager import (
    DownloadProgressEvent,
    detect_hardware,
    download_failed_message,
    download_size_mb,
    looks_offline,
    recommend_model,
)
from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.asr.whisper_backend import SHORT_CONTEXT_PAD_S, WhisperBackend
from auto_presenter.audio.capture import AudioCapture
from auto_presenter.audio.live_capture import LiveCapture
from auto_presenter.decision.director import Director
from auto_presenter.decision.engine import MatchingEngine, ParticleFilterEngine
from auto_presenter.decision.flow import FlowFollower
from auto_presenter.decision.follower_engine import FollowerEngine
from auto_presenter.decision.production import build_engine
from auto_presenter.decision.scheduler import (
    LeadTimeSchedulerConfig,
    TrackedPosition,
)
from auto_presenter.decision.types import AIStatus
from auto_presenter.eval.types import SessionConfig, SetlistItemSnapshot
from auto_presenter.index.library import build_index
from auto_presenter.index.phonemic import warm as warm_pronunciations
from auto_presenter.matcher.types import MatcherDecision
from auto_presenter.pipeline.audio_session import (
    AudioLevel,
    AudioSession,
    PipelineHealth,
    StateUpdate,
    make_mock_backend,
)
from auto_presenter.pipeline.perf import STAGE_MATCHER
from auto_presenter.protocol import (
    AIStatusEvent,
    AssessMachineRequest,
    AssessMachineResponse,
    AssistDismissRequest,
    AudioInputDeviceInfo,
    AudioLevelMessage,
    AudioSessionStateMessage,
    BeliefStateMessage,
    ClientMessage,
    CommitScriptureImportRequest,
    DeletePerformanceRequest,
    DeleteRecordingRequest,
    DownloadInstructionModelRequest,
    EngineTelemetryMessage,
    ExportRecordingRequest,
    FindScriptureReferencesRequest,
    FlowPositionMessage,
    GetHymnRequest,
    GetLearningStateRequest,
    Hypothesis,
    InspectLookbackRequest,
    InspectMusicRequest,
    InstallScriptureTranslationRequest,
    ListAudioDevicesRequest,
    ListAudioDevicesResponse,
    ListInstructionModelsRequest,
    ListRecordingsRequest,
    ListScriptureTranslationsRequest,
    LiveScriptureNotification,
    LoadRecordingAudioRequest,
    LoadRecordingRequest,
    ModelDownloadProgressMessage,
    ModelStatusRequest,
    ModelStatusResponse,
    ParseLeadSheetRequest,
    ParticleFilterTunables,
    PassageContextsRequest,
    PingMessage,
    PipelineHealthMessage,
    PongMessage,
    PrepareModelRequest,
    PrepareModelResponse,
    PreviewScriptureImportRequest,
    RecommendModelRequest,
    RecommendModelResponse,
    RecordingFinalizedMessage,
    RecordSlideChangeNotification,
    RemoveScriptureTranslationRequest,
    ReplayRecordingRequest,
    ResolvePassagesRequest,
    SaveGoldenRequest,
    SearchHymnsRequest,
    SearchScriptureCatalogRequest,
    SearchScriptureRequest,
    SemanticSearchRequest,
    SetAIModeRequest,
    SetBlankWhenIdleRequest,
    SetInstructionModelApiKeyRequest,
    SetInstructionModelRequest,
    SetlistContent,
    SetlistContentUpdatedMessage,
    SetMusicAnalysisRequest,
    SetSpokenInstructionsRequest,
    SetTunablesRequest,
    SingingStateMessage,
    SlideChangeMessage,
    StartAudioSessionRequest,
    StopAudioSessionRequest,
    SuggestChordsRequest,
    TranscriptPartialMessage,
    TunablesUpdatedMessage,
    UpdateSetlistContentRequest,
    VoiceChartRequest,
)
from auto_presenter.protocol import Word as ProtocolWord
from auto_presenter.recording.sink import (
    RecordingSink,
    new_recording_id,
)
from auto_presenter.server_chords import _handle_suggest_chords, _handle_voice_chart
from auto_presenter.server_follower import (
    assist_tier,
    build_session_followers,
    build_session_openings,
    cpu_path_enabled,
    follower_frame_observer,
    following_current_song,
    machine_verdict_path,
    note_ai_move,
    note_operator_move,
    onnx_scorer_dir,
    saved_verdict,
    session_side_pass,
)
from auto_presenter.server_follower import observe_health as observe_follower_health
from auto_presenter.server_hymns import _handle_get_hymn, _handle_search_hymns
from auto_presenter.server_instructions import (
    _handle_download_instruction_model,
    _handle_list_instruction_models,
    _handle_set_instruction_model,
    _handle_set_instruction_model_api_key,
    _handle_set_spoken_instructions,
    _observe_spoken_instructions,
    apply_session_spoken_instructions,
    note_live_scripture,
)
from auto_presenter.server_leadsheet import _handle_parse_lead_sheet
from auto_presenter.server_learning import (
    delete_performance,
    learning_content,
    learning_frame,
    learning_slide,
    learning_state,
    learning_words,
    start_learning,
    stop_learning,
)
from auto_presenter.server_library import (
    clear_library_search,
    observe_off_setlist,
    prepare_library_search,
)
from auto_presenter.server_library_search import _handle_semantic_search, warm_library_search
from auto_presenter.server_music import _handle_set_music_analysis
from auto_presenter.server_music import observe_frame as observe_music
from auto_presenter.server_recordings import (
    _handle_delete_recording,
    _handle_export_recording,
    _handle_inspect_lookback,
    _handle_inspect_music,
    _handle_list_recordings,
    _handle_load_recording,
    _handle_load_recording_audio,
    _handle_replay_recording,
    _handle_save_golden,
)
from auto_presenter.server_scripture import (
    _handle_commit_scripture_import,
    _handle_find_scripture_references,
    _handle_install_scripture_translation,
    _handle_list_scripture_translations,
    _handle_passage_contexts,
    _handle_preview_scripture_import,
    _handle_remove_scripture_translation,
    _handle_resolve_passages,
    _handle_search_scripture,
    _handle_search_scripture_catalog,
)
from auto_presenter.server_state import (
    _broadcast,
    _indexed_setlist,
    _matcher_belief,
    _send,
    _send_error,
    _state,
    check_ai_move_landed,
    deliver_decision,
    restore_screen_record,
    sync_transcribing,
)
from auto_presenter.tracker.types import BeliefState

log = logging.getLogger(__name__)

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)

#: One load per model at a time. A "Download now" in Settings and a service
#: start can both ask for the same model at once (they no longer queue behind
#: each other); the second waits and gets the first's backend instead of
#: starting a second download into the same cache.
#:
#: Per (backend, size), not global: a "Download now" of a bigger model used
#: to hold up a service start that needed a smaller one already on disk —
#: while that start held the session start lock.
_whisper_load_locks: dict[str, threading.Lock] = {}
_whisper_load_locks_guard = threading.Lock()
#: The waiting happens on the event loop, rather than on the thread locks
#: above: each "Download now" pressed during a first download used to park an
#: executor thread for the whole download, and the default executor is small.
_whisper_load_async_locks: dict[str, asyncio.Lock] = {}
_mlx_download_locks: dict[str, asyncio.Lock] = {}


def _whisper_load_lock(model_size: str) -> threading.Lock:
    with _whisper_load_locks_guard:
        return _whisper_load_locks.setdefault(model_size, threading.Lock())


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


def _env_hosted_credentials() -> HostedCredentials | None:
    """Research only (`asr_hosted`): online speech's endpoint, model and token
    from the environment, until the app hands over a server-issued key."""
    url = os.environ.get("AUTO_PRESENTER_HOSTED_SPEECH_URL")
    model = os.environ.get("AUTO_PRESENTER_HOSTED_SPEECH_MODEL")
    token = os.environ.get("AUTO_PRESENTER_HOSTED_SPEECH_TOKEN")
    if not (url and model and token):
        return None
    return HostedCredentials(base_url=url, model=model, token=token)


async def _load_whisper_backend_async(
    model_size: str,
    *,
    progress_callback: Callable[[DownloadProgressEvent], None] | None = None,
) -> WhisperBackend:
    # Already loaded: nothing to wait for, whatever else is downloading.
    if (
        _state.whisper_backend is not None
        and _state.whisper_model_size == model_size
    ):
        return _state.whisper_backend
    lock = _whisper_load_async_locks.setdefault(model_size, asyncio.Lock())
    async with lock:
        return await asyncio.to_thread(
            _get_or_load_whisper_backend,
            model_size,
            progress_callback=progress_callback,
        )


def _get_or_load_whisper_backend(
    model_size: str,
    *,
    progress_callback: Callable[[DownloadProgressEvent], None] | None = None,
) -> WhisperBackend:
    """Return a cached `WhisperBackend` if its size matches, else load fresh.

    Module-level so tests can monkeypatch without touching `_state`. Runs
    on a worker thread (see `_handle_start_audio`) — `WhisperModel.__init__`
    blocks on the model download and on CTranslate2 initialization, both
    of which are slow on first use. The optional `progress_callback`
    receives `DownloadProgressEvent`s emitted by the shared `ModelManager`.
    """
    with _whisper_load_lock(model_size):
        return _load_whisper_backend(model_size, progress_callback)


def _load_whisper_backend(
    model_size: str,
    progress_callback: Callable[[DownloadProgressEvent], None] | None,
) -> WhisperBackend:
    if (
        _state.whisper_backend is not None
        and _state.whisper_model_size == model_size
    ):
        return _state.whisper_backend

    # Drive the load through the shared ModelManager so progress events
    # reach the UI uniformly (whether the model is already on disk or
    # being downloaded for the first time).
    # Progress goes to this load's own callback: two loads of different
    # sizes can now run at once, and a callback set on the shared manager
    # was cleared by whichever finished first.
    model = _state.model_manager.ensure(model_size, progress=progress_callback)

    backend = WhisperBackend(model, model_name=f"whisper-{model_size}")
    _state.whisper_backend = backend
    _state.whisper_model_size = model_size
    return backend


def _resolve_recordings_root(db_path: str | None) -> Path:
    """Pick the directory where recordings get written.

    Tauri passes `--db-path <app_data>/library.db`; recordings live in a
    sibling `recordings/` folder. Without that arg (e.g. test invocations
    or ad-hoc CLI use), fall back to the platform-default app data dir.
    """
    if db_path:
        return Path(db_path).parent / "recordings"
    from auto_presenter.config import app_data_dir

    return app_data_dir() / "recordings"


async def serve(
    *, host: str, port: int, db_path: str | None, dev_tools: bool = False
):
    """Start a WebSocket server. Returns the underlying asyncio server.

    `dev_tools` is the only production seam the agent-testing package
    hangs off: when False (the default, and every release spawn), the
    dispatcher never imports `auto_presenter.dev`.
    """
    _state.dev_tools = dev_tools
    if dev_tools:
        log.info("dev tools enabled (debug builds only)")
    _state.recordings_root = _resolve_recordings_root(db_path)
    _state.recordings_root.mkdir(parents=True, exist_ok=True)
    _state.library_db_path = Path(db_path) if db_path else None
    log.info("recordings root: %s", _state.recordings_root)
    if _state.library_db_path:
        log.info("library db: %s", _state.library_db_path)
        # Held on _state so the task isn't garbage-collected mid-sleep.
        _state.library_search_warmup = asyncio.create_task(warm_library_search())

    return await websockets.serve(
        _handle_client,
        host=host,
        port=port,
        # The default 1 MiB cap refused an imported Bible file (3-30 MB,
        # base64 on top) before any handler saw it. The socket is local-only;
        # 128 MiB fits the importer's own 80 MB file limit after encoding.
        max_size=128 * 1024 * 1024,
        ping_interval=20,
        ping_timeout=20,
        # Browsers let any web page open a WebSocket to 127.0.0.1 and always
        # say which page it is. Only the app's own window gets in; local
        # programs (the app shell, tools, tests) send no Origin at all.
        origins=APP_ORIGINS,
    )


#: Requests that don't touch the live session and may each take a while.
#: They run alongside everything else instead of in the connection's queue.
#:
#: The UI sends every request over one shared socket, and a socket's queue
#: used to be the only one: a first service start downloading the speech
#: model held it for minutes, so the device list behind it timed out (an
#: empty picker in Settings), and the model-status check behind a "Download
#: now" did too (Settings stuck on "Checking…").
CONCURRENT_KINDS = frozenset(
    {
        "ping",
        "list_audio_devices",
        "model_status",
        "prepare_model",
        "recommend_model",
        "list_recordings",
        "load_recording",
        "load_recording_audio",
        "export_recording",
        "inspect_music",
        "parse_lead_sheet",
        "resolve_passages",
        "find_scripture_references",
        "passage_contexts",
        "search_scripture",
        "list_scripture_translations",
        "install_scripture_translation",
        "remove_scripture_translation",
        "search_scripture_catalog",
        "preview_scripture_import",
        "commit_scripture_import",
        "search_hymns",
        "get_hymn",
        "semantic_search",
        "list_instruction_models",
        "download_instruction_model",
        # Editor-time and read-only: the recordings list, the golden editor,
        # the learning inspector.
        "get_learning_state",
        "delete_recording",
        "delete_performance",
        "suggest_chords",
        "voice_chart",
        "inspect_lookback",
        "save_golden",
        # Settings that a session start neither reads nor writes: the start
        # path never looks at spoken-instruction, music-analysis or
        # blank-when-idle state (they are read live, per event or per frame).
        "set_spoken_instructions",
        "set_instruction_model",
        "set_instruction_model_api_key",
        "set_blank_when_idle",
        "set_music_analysis",
        # Deliberately NOT here: `set_tunables` (a start writes
        # `_state.tunables` and builds the matcher from it; run alongside, the
        # new knobs land on the old matcher and the start overwrites them).
    }
)


def _message_kind(raw: Any) -> str | None:
    if isinstance(raw, bytes):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    kind = payload.get("kind") if isinstance(payload, dict) else None
    return kind if isinstance(kind, str) else None


async def _dispatch_logged(ws: ServerConnection, raw: Any) -> None:
    try:
        await _dispatch(ws, raw)
    except websockets.ConnectionClosed:
        pass
    except Exception as exc:
        # Answer the request before logging: without a reply the UI waits out
        # its whole timeout — ten minutes on "AI: starting" for a session start.
        try:
            await _send_error(ws, _message_id(raw), f"internal error: {exc}")
        except Exception:
            pass
        log.exception("unhandled error handling %s", _message_kind(raw))


def _message_id(raw: Any) -> int | None:
    if isinstance(raw, bytes):
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    msg_id = payload.get("id") if isinstance(payload, dict) else None
    return msg_id if isinstance(msg_id, int) and not isinstance(msg_id, bool) else None


#: Origins allowed to connect: the app's window on macOS/Linux (tauri://) and
#: Windows (http(s)://tauri.localhost), the dev server, and no Origin header.
APP_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    None,
]


async def _handle_client(ws: ServerConnection) -> None:
    """Read one client's messages: session-affecting ones in send order, the
    rest (`CONCURRENT_KINDS`) as soon as they arrive.

    Order still matters for most messages — a stop must land after the start
    it follows, a slide change after the session it belongs to — so those
    go through one queue, handled one at a time as before.
    """
    log.info("client connected: %s", ws.remote_address)
    _state.clients.add(ws)
    ordered: asyncio.Queue[Any] = asyncio.Queue()
    side_tasks: set[asyncio.Task[None]] = set()

    async def drain_ordered() -> None:
        while (raw := await ordered.get()) is not None:
            await _dispatch_logged(ws, raw)

    worker = asyncio.create_task(drain_ordered())
    try:
        async for raw in ws:
            if _message_kind(raw) in CONCURRENT_KINDS:
                task = asyncio.create_task(_dispatch_logged(ws, raw))
                side_tasks.add(task)
                task.add_done_callback(side_tasks.discard)
            else:
                ordered.put_nowait(raw)
    except websockets.ConnectionClosedOK:
        pass
    except websockets.ConnectionClosedError as err:
        log.info("client disconnected: %s", err)
    except asyncio.CancelledError:
        # The server is shutting down: nothing in flight gets to finish.
        worker.cancel()
        for task in side_tasks:
            task.cancel()
        raise
    except Exception:
        log.exception("unhandled error in client handler")
    finally:
        _state.clients.discard(ws)
        log.info("client gone: %s", ws.remote_address)
    # A client that disconnects gets what's already in flight finished, as it
    # did when the read loop awaited each handler — a session start abandoned
    # halfway would leave the engine half-built.
    ordered.put_nowait(None)
    await asyncio.gather(worker, *side_tasks, return_exceptions=True)


async def _dispatch(ws: ServerConnection, raw: Any) -> None:
    if isinstance(raw, bytes):
        await _send_error(ws, None, "binary frames not supported")
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        await _send_error(ws, None, f"invalid json: {err}")
        return

    msg_id = payload.get("id") if isinstance(payload, dict) else None

    try:
        msg = _client_adapter.validate_python(payload)
    except ValidationError as err:
        if _state.dev_tools:
            from auto_presenter.dev.server import try_dispatch

            if await try_dispatch(ws, payload):
                return
        await _send_error(ws, msg_id, f"invalid message: {err.errors()[0]['msg']}")
        return

    if isinstance(msg, PingMessage):
        await _send(ws, PongMessage(id=msg.id))
        return

    if isinstance(msg, ListAudioDevicesRequest):
        from auto_presenter.audio.devices import list_input_devices

        # Rescan only while no capture stream is open or opening:
        # re-initializing PortAudio under one would kill its audio. Off the
        # event loop: a rescan can take a while on some drivers.
        found = await asyncio.to_thread(
            list_input_devices,
            rescan=not _state.audio_start_lock.locked() and _no_capture_alive(),
        )
        devices = [
            AudioInputDeviceInfo(
                index=d.index,
                name=d.name,
                is_default=d.is_default,
                max_input_channels=d.max_input_channels,
                default_samplerate=d.default_samplerate,
            )
            for d in found
        ]
        await _send(ws, ListAudioDevicesResponse(id=msg.id, devices=devices))
        return

    if isinstance(msg, StartAudioSessionRequest):
        await _handle_start_audio(ws, msg)
        return

    if isinstance(msg, StopAudioSessionRequest):
        await _handle_stop_audio(ws, msg)
        return

    if isinstance(msg, LiveScriptureNotification):
        note_live_scripture(msg.reference, msg.display)
        return

    if isinstance(msg, RecordSlideChangeNotification):
        _handle_slide_change(msg)
        return

    if isinstance(msg, ListRecordingsRequest):
        await _handle_list_recordings(ws, msg)
        return
    if isinstance(msg, GetLearningStateRequest):
        state = await asyncio.to_thread(learning_state)
        await _send(ws, state.model_copy(update={"id": msg.id}))
        return
    if isinstance(msg, DeletePerformanceRequest):
        await asyncio.to_thread(delete_performance, msg.performance_id)
        await _send(ws, PongMessage(id=msg.id))
        return

    if isinstance(msg, DeleteRecordingRequest):
        await _handle_delete_recording(ws, msg)
        return

    if isinstance(msg, ExportRecordingRequest):
        await _handle_export_recording(ws, msg)
        return

    if isinstance(msg, LoadRecordingRequest):
        await _handle_load_recording(ws, msg)
        return

    if isinstance(msg, LoadRecordingAudioRequest):
        await _handle_load_recording_audio(ws, msg)
        return

    if isinstance(msg, InspectLookbackRequest):
        await _handle_inspect_lookback(ws, msg)
        return
    if isinstance(msg, ReplayRecordingRequest):
        await _handle_replay_recording(ws, msg)
        return

    if isinstance(msg, InspectMusicRequest):
        await _handle_inspect_music(ws, msg)
        return

    if isinstance(msg, SaveGoldenRequest):
        await _handle_save_golden(ws, msg)
        return

    if isinstance(msg, RecommendModelRequest):
        await _handle_recommend_model(ws, msg)
        return
    if isinstance(msg, AssessMachineRequest):
        await _handle_assess_machine(ws, msg)
        return

    if isinstance(msg, ModelStatusRequest):
        await _handle_model_status(ws, msg)
        return

    if isinstance(msg, PrepareModelRequest):
        await _handle_prepare_model(ws, msg)
        return

    if isinstance(msg, SetAIModeRequest):
        await _handle_set_ai_mode(ws, msg)
        return

    if isinstance(msg, AssistDismissRequest):
        await server_assist.handle_dismiss(ws, msg)
        return

    if isinstance(msg, SetBlankWhenIdleRequest):
        await _handle_set_blank_when_idle(ws, msg)
        return

    if isinstance(msg, SetMusicAnalysisRequest):
        await _handle_set_music_analysis(ws, msg)
        return

    if isinstance(msg, ResolvePassagesRequest):
        await _handle_resolve_passages(ws, msg)
        return
    if isinstance(msg, FindScriptureReferencesRequest):
        await _handle_find_scripture_references(ws, msg)
        return
    if isinstance(msg, PassageContextsRequest):
        await _handle_passage_contexts(ws, msg)
        return

    if isinstance(msg, ParseLeadSheetRequest):
        await _handle_parse_lead_sheet(ws, msg)
        return

    if isinstance(msg, SearchScriptureRequest):
        await _handle_search_scripture(ws, msg)
        return

    if isinstance(msg, ListScriptureTranslationsRequest):
        await _handle_list_scripture_translations(ws, msg)
        return

    if isinstance(msg, InstallScriptureTranslationRequest):
        await _handle_install_scripture_translation(ws, msg)
        return

    if isinstance(msg, RemoveScriptureTranslationRequest):
        await _handle_remove_scripture_translation(ws, msg)
        return

    if isinstance(msg, SearchScriptureCatalogRequest):
        await _handle_search_scripture_catalog(ws, msg)
        return

    if isinstance(msg, PreviewScriptureImportRequest):
        await _handle_preview_scripture_import(ws, msg)
        return

    if isinstance(msg, CommitScriptureImportRequest):
        await _handle_commit_scripture_import(ws, msg)
        return

    if isinstance(msg, SearchHymnsRequest):
        await _handle_search_hymns(ws, msg)
        return

    if isinstance(msg, GetHymnRequest):
        await _handle_get_hymn(ws, msg)
        return

    if isinstance(msg, SemanticSearchRequest):
        await _handle_semantic_search(ws, msg)
        return

    if isinstance(msg, SuggestChordsRequest):
        await _handle_suggest_chords(ws, msg)
        return

    if isinstance(msg, VoiceChartRequest):
        await _handle_voice_chart(ws, msg)
        return

    if isinstance(msg, SetSpokenInstructionsRequest):
        await _handle_set_spoken_instructions(ws, msg)
        return

    if isinstance(msg, ListInstructionModelsRequest):
        await _handle_list_instruction_models(ws, msg)
        return

    if isinstance(msg, SetInstructionModelRequest):
        await _handle_set_instruction_model(ws, msg)
        return

    if isinstance(msg, DownloadInstructionModelRequest):
        await _handle_download_instruction_model(ws, msg)
        return

    if isinstance(msg, SetInstructionModelApiKeyRequest):
        await _handle_set_instruction_model_api_key(ws, msg)
        return

    if isinstance(msg, SetTunablesRequest):
        await _handle_set_tunables(ws, msg)
        return

    if isinstance(msg, UpdateSetlistContentRequest):
        await _handle_update_setlist_content(ws, msg)
        return

    log.debug("unhandled message kind: %s", msg.kind)
    await _send_error(ws, msg.id, f"unhandled message kind: {msg.kind}")


# --- Audio session handlers ----------------------------------------------------


async def _handle_start_audio(
    ws: ServerConnection, msg: StartAudioSessionRequest
) -> None:
    """Start the singleton audio session, one caller at a time.

    The lock is what makes the ranking below mean anything. Building a
    session is slow and `await`-heavy, and until it finishes
    `audio_session.is_running` is False — so without serialization two
    starts overlapping in that window both passed the guard and both
    assigned `_state.matcher`, last writer winning. See
    `_ServerState.audio_start_lock`.

    Waiting is the right trade: a caller that will be rejected anyway waits
    out the incumbent's model load, which is slower than failing fast but
    cannot leave the service session's matcher quietly destroyed.
    """
    async with _state.audio_start_lock:
        await _start_audio_session(ws, msg)


async def restart_with_capture(
    ws: ServerConnection,
    msg: StartAudioSessionRequest,
    capture: AudioCapture,
    *,
    ack: bool = True,
) -> bool:
    """Replace the live capture without the UI resending session params.

    The only production code the debug inject-audio path calls besides
    `serve(dev_tools=True)`. Holds the start lock so a concurrent mic
    start cannot sneak in between stop and start. Returns whether a
    session is running afterwards.
    """
    async with _state.audio_start_lock:
        if _state.audio_session is not None and _state.audio_session.is_live:
            await _stop_audio_session()
        await _start_audio_session(ws, msg, capture=capture, ack=ack)
        return _state.audio_session is not None


def _no_capture_alive() -> bool:
    """No session holds the microphone, or is opening it, or is still closing it.

    `audio_session is None` alone was too strict: a session whose microphone
    failed to open stays in `_state.audio_session`, so during a presentation a
    newly plugged mic never appeared in the list and Retry couldn't open it.
    """
    session = _state.audio_session
    if session is None:
        return True
    return not session.is_live and not session.capture_thread_alive


def _mic_capture(msg: StartAudioSessionRequest) -> LiveCapture:
    """Default capture factory. Debug inject-audio passes a FileCapture
    in instead; this is the production path and the only place that
    opens PortAudio from a session start."""
    device_selector: int | str | None = (
        msg.device_name if msg.device_name else msg.device_index
    )
    return LiveCapture(device=device_selector)


async def _start_audio_session(
    ws: ServerConnection,
    msg: StartAudioSessionRequest,
    *,
    capture: AudioCapture | None = None,
    ack: bool = True,
) -> None:
    # A capture handed in is injected audio (dev tools): a replay or a test.
    injected = capture is not None
    if _state.audio_session is not None and _state.audio_session.is_live:
        # One microphone, one session — but not all sessions are equal. The
        # debug panel opens a transcript-only stream (no `setlist_content`,
        # so no matcher); a service opens one that drives slides. Rejecting
        # whoever asks second means a debug panel left open silently prevents
        # the service session from ever existing: transcripts appear, the
        # belief panel stays empty, and the AI never advances a slide, with
        # the only clue an error on a request the operator never saw.
        #
        # So rank them. A matcher-bearing request preempts a transcript-only
        # session; anything else still yields.
        wants_matcher = bool(msg.setlist_content and msg.setlist_content.items)
        if not (wants_matcher and _state.matcher is None):
            await _send_error(ws, msg.id, "audio session already running")
            return
        log.info("preempting transcript-only session for a matcher session")
        await _stop_audio_session()
    elif _state.audio_session is not None:
        # A session that ended on its own — a microphone that failed to open,
        # a device that went away, injected audio that ran out. Tear it down
        # properly before replacing it (a Retry lands here), or its recording
        # is never closed and its capture thread never joined.
        await _stop_audio_session()

    if msg.record and not msg.setlist_id:
        await _send_error(ws, msg.id, "setlist_id required when record=true")
        return

    # Load the requested backend. Whisper's `.load()` is slow and may
    # download a model, so we run it on a worker thread to avoid blocking
    # the asyncio loop (and the other WS clients).
    backend: ASRBackend
    loop = asyncio.get_running_loop()
    if msg.backend == "whisper":
        await _broadcast_state_update(StateUpdate(state="starting"), setlist_id=msg.setlist_id)

        def progress_callback(event: DownloadProgressEvent) -> None:
            # ModelManager fires the callback on the worker thread; bounce
            # back to the asyncio loop so the broadcast hits every client.
            asyncio.run_coroutine_threadsafe(
                _broadcast_model_progress(event), loop
            )

        try:
            backend = await _load_whisper_backend_async(
                msg.model_size, progress_callback=progress_callback
            )
        except Exception as exc:
            err = f"whisper backend failed to load: {exc}"
            log.exception("whisper load failed")
            await _broadcast_state_update(
                StateUpdate(state="error", error=err), setlist_id=msg.setlist_id
            )
            await _send_error(ws, msg.id, err)
            return
        # The backend is cached across sessions: this session's choice. The
        # CPU path (a computer that passed its machine check) always runs
        # short-context passes: it was measured and judged with them.
        tun = msg.tunables or _state.tunables
        if tun is not None:
            _state.tunables = tun
        # The Assist-only tier (`assist_tier`) runs the same short-context
        # passes, half as often: that is what its machine check timed.
        assist = assist_tier()
        backend.short_context_s = (
            SHORT_CONTEXT_PAD_S
            if (tun is not None and tun.asr_short_context) or cpu_path_enabled() or assist
            else None
        )
        backend.min_step_seconds = ASSIST_TRANSCRIBE_EVERY_S if assist else 0.0
        if tun is not None and tun.asr_hosted:
            backend = HostedSpeechBackend(_env_hosted_credentials, fallback=backend)
        elif tun is not None and tun.asr_remote and _env("AUTO_PRESENTER_REMOTE_SPEECH_URL"):
            from auto_presenter.asr.remote_backend import RemoteStreamingBackend

            backend = RemoteStreamingBackend(
                os.environ["AUTO_PRESENTER_REMOTE_SPEECH_URL"],
                lambda: os.environ.get("AUTO_PRESENTER_REMOTE_SPEECH_TOKEN"),
                fallback=backend,
            )
            backend.warm()  # starts waking the GPU; doesn't wait for it
    elif msg.backend == "mlx":
        if not MLXWhisperBackend.is_available():
            await _send_error(
                ws,
                msg.id,
                "mlx-whisper is not installed — it needs Apple silicon and "
                'the "mlx" extra (see sidecar/pyproject.toml)',
            )
            return
        await _broadcast_state_update(StateUpdate(state="starting"), setlist_id=msg.setlist_id)
        try:
            await _download_mlx_model(msg.model_size)
        except Exception as exc:
            err = str(exc)
            await _broadcast_state_update(
                StateUpdate(state="error", error=err), setlist_id=msg.setlist_id
            )
            await _send_error(ws, msg.id, err)
            return
        backend = MLXWhisperBackend(msg.model_size)
        try:
            # First Metal call compiles kernels (1–5 s). Do it here, off the
            # sung-word path, so the first live window is the steady-state
            # ~100–300 ms rather than a multi-second stall.
            await asyncio.to_thread(backend.warm)
        except Exception as exc:
            log.warning("mlx-whisper warm failed (first live pass will retry): %s", exc)
        backend = await _maybe_qwen_backend(backend, msg.tunables or _state.tunables)
    elif msg.backend == "groq":  # deprecated (2026-11-23): the app no longer asks for it
        api_key = get_instruction_api_key("groq")
        if not api_key:
            await _send_error(
                ws, msg.id, "groq backend needs an API key — add one from the debug panel"
            )
            return
        backend = GroqWhisperBackend(api_key=api_key, model=msg.model_size)
    else:
        backend = make_mock_backend()
    _state.session_asr_backend = msg.backend
    _state.session_asr_model = msg.model_size

    if capture is None:
        # PortAudio only sees devices that were there when it initialized; a
        # microphone plugged in since — the usual fix for one that failed —
        # is invisible until it rescans. Nothing holds a stream here: the
        # start lock is ours and any previous session was torn down above.
        if _no_capture_alive():
            from auto_presenter.audio.devices import rescan_input_devices

            await asyncio.to_thread(rescan_input_devices)
        capture = _mic_capture(msg)

    # Optionally open a RecordingSink that writes audio + ground truth.
    recording_sink: RecordingSink | None = None
    if msg.record:
        if _state.recordings_root is None:
            await _send_error(ws, msg.id, "recordings root not initialized")
            return
        recording_id = new_recording_id()
        # If the UI shipped setlist_content, embed it in the recording so
        # baseline replays can re-feed the matcher with the same words.
        snapshot: list[SetlistItemSnapshot] = []
        if msg.setlist_content is not None:
            snapshot = [
                # Sections and titles too: without them a replay runs the
                # engine with no transition priors and asks the spoken-request
                # model about songs with no verses (found 2026-10-17 — every
                # recording before this lacked both).
                SetlistItemSnapshot(
                    item_idx=i.item_idx, song_id=i.song_id, slides=list(i.slides),
                    slide_sections=list(i.slide_sections), title=i.title, spoken=i.spoken,
                )
                for i in msg.setlist_content.items
            ]
        # Capture the settings this trace runs under. Read from the objects
        # that will actually be used rather than echoed from the request:
        # a value the UI clamped on its way here is precisely the kind of
        # discrepancy this exists to expose, and one has already shipped.
        tun = msg.tunables or _state.tunables or ParticleFilterTunables()
        session_config = SessionConfig(
            asr_backend=msg.backend,
            asr_model=msg.model_size if msg.backend != "mock" else "",
            ai_mode=msg.ai_mode,
            tunables={
                k: float(v)
                for k, v in tun.model_dump().items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            },
            window_seconds=SlidingWindowWrapper.window_seconds,
            # a hosted backend's rate limit sets a slower floor (see
            # `GroqWhisperBackend.min_step_seconds`)
            step_seconds=max(
                SlidingWindowWrapper.step_seconds, getattr(backend, "min_step_seconds", 0.0)
            ),
            stable_margin_seconds=SlidingWindowWrapper.stable_margin_seconds,
        )
        recording_sink = RecordingSink(
            recording_id=recording_id,
            output_dir=_state.recordings_root / recording_id,
            setlist_id=msg.setlist_id or "",
            setlist_content=snapshot,
            config=session_config,
        )
        try:
            recording_sink.open()
        except Exception as exc:
            await _send_error(ws, msg.id, f"failed to open recording: {exc}")
            return
        _state.active_recording = recording_sink

    # Everything from here can fail after the recording is open and the
    # session half-built — an openings or library-search build that raises,
    # the session itself. Undo it all
    # rather than leave a recording open and a matcher with no session;
    # the error itself still reaches the request (`_dispatch_logged`).
    try:
        await _finish_start_audio_session(
            ws,
            msg,
            loop=loop,
            backend=backend,
            capture=capture,
            recording_sink=recording_sink,
            injected=injected,
            ack=ack,
        )
    except websockets.ConnectionClosed:
        # Only the reply failed: the client went away while the session came
        # up. The session itself is fine — tearing it down would cost a
        # working microphone for a dropped socket that reconnects in a second.
        raise
    except Exception:
        await _abort_start_audio_session()
        raise


async def _finish_start_audio_session(
    ws: ServerConnection,
    msg: StartAudioSessionRequest,
    *,
    loop: asyncio.AbstractEventLoop,
    backend: ASRBackend,
    capture: AudioCapture,
    recording_sink: RecordingSink | None,
    injected: bool,
    ack: bool,
) -> None:
    """The second half of `_start_audio_session`: matcher, observers, the
    session itself. Split out so the caller can undo all of it on failure."""

    def level_observer(level: AudioLevel) -> None:
        # Runs on the audio worker thread — bounce the broadcast back to
        # the asyncio loop.
        asyncio.run_coroutine_threadsafe(_broadcast_audio_level(level), loop)

    # Build the matcher + director when the UI supplied a setlist payload.
    # No payload → transcript-only session (the matcher needs words to score
    # against; without them it would just sit idle).
    matcher: MatchingEngine | None = None
    director: Director | None = None
    _drop_followers()
    _state.openings = None
    _state.openings_pending = None
    _state.band.reset()
    _state.pipeline_lag_s = 0.0
    _state.audio_clock = None
    _state.ai_move_pending = None
    _state.song_probation = None
    _state.song_unconfirmed = False
    _state.last_ai_item = None
    # Each session's audio clock starts at zero. A detector still holding the
    # last session's times would read the new one as long silence — and with
    # blanking on, a black screen — until the clock caught up.
    _state.singing.reset()
    _state.blanking = False
    if msg.setlist_id is not None:
        # A service (the debug panel's transcript session has no setlist id).
        apply_session_spoken_instructions(msg.spoken_instructions)
    if msg.setlist_content is not None and msg.setlist_content.items:
        try:
            tunables = msg.tunables or _state.tunables
            indexed = build_index(msg.setlist_content, follow_readings=_follow_readings(tunables))
            _state.tunables = tunables
            # The pronunciation dictionary and the lyrics' pronunciations,
            # now rather than on the first sung word (`phonemic.warm`).
            await asyncio.to_thread(warm_pronunciations, _lyric_words(msg.setlist_content))
            matcher = build_engine(indexed, tunables)
            start_item = msg.start_item_idx if msg.start_item_idx is not None else 0
            _state.last_ai_item = start_item
            if 0 <= start_item < indexed.item_count:
                matcher.note_displayed(start_item, msg.start_slide_idx)
            # Songs with a lead sheet are followed by the score follower;
            # the production engine keeps deciding which song. A follower
            # that fails to build costs only itself: the session runs on
            # the production engine, as it would without lead sheets.
            try:
                followers = await asyncio.to_thread(
                    build_session_followers, msg.setlist_content, backend
                )
            except Exception:
                log.exception("score follower failed to start; matcher only this session")
                followers = None
            if followers is not None:
                matcher = FollowerEngine.wrap(matcher, followers)
                if 0 <= start_item < indexed.item_count:
                    followers.enter(start_item, msg.start_slide_idx, 0.0)
                _state.followers = followers
                if recording_sink is not None and recording_sink._config is not None:
                    recording_sink._config.score_follower = True
                    recording_sink._config.follower_items = sorted(followers.songs)
            director = Director(
                matcher=matcher,
                on_decision_broadcast=lambda d: asyncio.run_coroutine_threadsafe(
                    _broadcast_ai_decision(d), loop
                ),
                on_status_broadcast=lambda s: asyncio.run_coroutine_threadsafe(
                    _broadcast_ai_status(s), loop
                ),
                on_assist_move=lambda d: server_assist.engine_move_threadsafe(loop, d),
                # the same gate as switching it on later: a session may not
                # start with live AI on a computer that hasn't earned it
                mode=_allowed_start_mode(msg.ai_mode),
            )
        except Exception as exc:
            log.exception("failed to build matcher from setlist_content")
            _drop_followers()
            await _abandon_recording()
            await _send_error(ws, msg.id, f"matcher init failed: {exc}")
            return
    _state.matcher = matcher
    _state.director = director
    server_assist.reset()
    if matcher is not None:
        start_learning(msg, injected=injected)
    if matcher is not None and msg.setlist_content is not None:
        _state.openings = await asyncio.to_thread(
            build_session_openings, msg.setlist_content, backend
        )
    _state.last_chord = None
    _state.last_chord_t = None
    if matcher is not None:
        await asyncio.to_thread(prepare_library_search)
    else:
        clear_library_search()

    def health_observer(health: PipelineHealth) -> None:
        asyncio.run_coroutine_threadsafe(_broadcast_pipeline_health(health), loop)

    _state.transcript_handler = _make_transcript_handler(matcher, director)

    # The live chord/beat analysis broadcasts from the capture thread.
    _state.loop = asyncio.get_running_loop()

    _state.session_generation += 1
    generation = _state.session_generation
    fanout = _frame_fanout(recording_sink)
    follower_observer = follower_frame_observer(loop)  # the session's music, followers or not

    def frame_observer(frame):
        fanout(frame)
        learning_frame(frame)
        if follower_observer is not None:
            follower_observer(frame)

    session = AudioSession(
        capture,
        backend,
        on_event=_state.transcript_handler,
        on_state=_session_state_handler(_state.learning, msg.setlist_id, generation),
        wrapper_kwargs=(
            {"side_pass": session_side_pass(loop)}
            if _state.followers is not None or _state.openings is not None
            else None
        ),
        frame_observer=frame_observer,
        level_observer=level_observer,
        health_observer=health_observer,
    )
    _state.audio_session = session
    _state.last_start_audio = msg
    sync_transcribing()
    session.start(loop)
    # The microphone opens on the capture thread, after `start()` returns.
    # Replying straight away meant a mic that failed to open was reported
    # only as an `audio_session_state` broadcast, and the start request —
    # the thing the presentation view watches — had already succeeded.
    # Wait, briefly, to see which way it goes; a device slower than this
    # still reports through the broadcast.
    if not await _wait_for_capture(session):
        err = session.last_error or "the audio session failed to start"
        # Tear the failed session down now — its recording (empty: the mic
        # never opened, so it is discarded), matcher, director and followers.
        # Leaving them for the next start to finalize meant every Retry left
        # one more empty recording in Settings.
        await _abandon_recording()
        await _stop_audio_session()
        if ack:
            await _send_error(ws, msg.id, err)
        return
    # Ack with no body — the actual session-state updates flow as
    # `audio_session_state` broadcasts. Inject-audio suppresses this so
    # it can reply with its own result on the same request id.
    if ack:
        await _send(ws, PongMessage(id=msg.id))


async def _abort_start_audio_session() -> None:
    """Undo a session start that raised part-way.

    Any previous session was torn down before the start began, so a session
    in `_state` now is the half-started one: stop it the usual way, which
    releases the capture. Short of that, unwind what the start had set up.
    """
    await _abandon_recording()
    if _state.audio_session is not None:
        await _stop_audio_session()
        return
    await asyncio.to_thread(stop_learning)
    _state.matcher = None
    _state.director = None
    _drop_followers()
    _state.openings = None
    clear_library_search()


#: How long a session start waits for the microphone to open before replying.
#: PortAudio usually takes a few hundred milliseconds; a Bluetooth or
#: aggregate device can take a second or two.
CAPTURE_OPEN_WAIT_SECONDS = 4.0


async def _wait_for_capture(session: AudioSession) -> bool:
    """False once the session has failed; True when it is running, has
    already finished normally, or is still opening when the wait runs out."""
    deadline = time.monotonic() + CAPTURE_OPEN_WAIT_SECONDS
    while time.monotonic() < deadline:
        if session.state == "error":
            return False
        if session.state == "running":
            return True
        if session.state in ("stopping", "stopped") and not session.capture_thread_alive:
            return True
        await asyncio.sleep(0.02)
    return session.state != "error"


def _drop_followers() -> None:
    """Forget the session's score followers, detaching them first so their
    in-service learner stops and nothing keeps waiting on their alignments."""
    followers = _state.followers
    _state.followers = None
    if followers is None:
        return
    try:
        followers.detach()
    except Exception:
        log.exception("score followers failed to detach")


async def _abandon_recording() -> None:
    """Close a recording opened for a session start that then failed."""
    sink = _state.active_recording
    _state.active_recording = None
    if sink is None:
        return
    def close_and_discard() -> None:
        sink.close()
        # Nothing was captured, so nothing is worth keeping: an empty
        # recording would only clutter the list in Settings.
        if sink.frames_written == 0:
            shutil.rmtree(sink.output_dir, ignore_errors=True)

    try:
        await asyncio.to_thread(close_and_discard)
    except Exception:
        log.exception("failed to close an abandoned recording")


#: How long a microphone stop may take before the engine stops waiting.
_AUDIO_STOP_SECONDS = 5.0


async def _stop_audio_session() -> None:
    """Tear down the running session, matcher, director and recording.

    Shared by the stop handler and by session preemption, so a preempted
    session releases the microphone and finalizes its recording the same way
    an explicitly stopped one does.
    """
    session = _state.audio_session
    if session is None:
        return
    _state.session_asr_backend = None
    _state.session_asr_model = None
    # Stopping the microphone goes through PortAudio into CoreAudio, which has
    # deadlocked there (2026-10-22). On the event loop that froze the whole
    # engine — every later request, a Bible download included, hung. In a
    # thread with a deadline, the worst case is one stuck thread and a warning.
    try:
        await asyncio.wait_for(asyncio.to_thread(session.stop), _AUDIO_STOP_SECONDS)
    except TimeoutError:
        log.error("the audio device didn't stop within %.0f s; carrying on", _AUDIO_STOP_SECONDS)
    await asyncio.to_thread(session.join, 2.0)
    await asyncio.to_thread(stop_learning)
    _state.audio_session = None
    _state.last_slide = None
    _state.last_slide_source = None
    # Matcher + director are scoped to a single session.
    _state.matcher = None
    _state.director = None
    _state.flow_follower = None
    _drop_followers()
    _state.openings = None
    clear_library_search()

    # Finalize the recording on a worker thread so the I/O doesn't stall
    # the event loop.
    sink = _state.active_recording
    _state.active_recording = None
    if sink is not None:
        try:
            recording = await asyncio.to_thread(sink.close)
        except Exception:
            log.exception("failed to finalize recording")
        else:
            if sink.frames_written == 0:
                # A session that never captured a frame (its mic failed after
                # the start replied, say) leaves nothing worth keeping.
                await asyncio.to_thread(shutil.rmtree, sink.output_dir, True)
                return
            await _broadcast(
                RecordingFinalizedMessage(
                    recording_id=recording.id,
                    duration_seconds=recording.duration_seconds,
                    ground_truth_count=len(recording.ground_truth),
                )
            )


async def _handle_stop_audio(
    ws: ServerConnection, msg: StopAudioSessionRequest
) -> None:
    if _state.audio_session is None:
        await _send_error(ws, msg.id, "no audio session running")
        return
    await _stop_audio_session()
    await _send(ws, PongMessage(id=msg.id))


# --- Recording handlers --------------------------------------------------------


def _handle_slide_change(msg: RecordSlideChangeNotification) -> None:
    """Fan a slide change out to the recording sink and the matcher director.

    Sync — fires and forgets. Both subscribers are thread-safe.
    Operator-sourced changes (`source="manual"|"override"`) are what
    flip the director to `following_human`; AI-sourced changes are
    informational only (they originate from the director itself and
    shouldn't trigger another transition).
    """
    sink = _state.active_recording
    if sink is not None:
        sink.record_slide_change(
            item_idx=msg.item_idx, slide_idx=msg.slide_idx, source=msg.source
        )
    _state.last_slide = (msg.item_idx, msg.slide_idx)
    _state.last_slide_source = msg.source
    pending = _state.ai_move_pending
    if pending is not None and (msg.item_idx, msg.slide_idx) == pending[:2]:
        _state.ai_move_pending = None
    learning_slide(msg.item_idx, msg.slide_idx, msg.source)
    director = _state.director
    server_assist.displayed(msg.item_idx, msg.slide_idx)
    if director is not None and director.mode == "assist" and msg.source in ("manual", "override"):
        # Assist: the engine runs free. Re-anchoring it on every click would
        # make it agree with the operator's mistakes — the thing assist is
        # there to catch. A spoken request still re-anchors (its own path).
        return
    if msg.source != "ai" and note_operator_move(msg.item_idx):
        asyncio.get_running_loop().create_task(refresh_blanking())
    if director is not None and msg.source != "ai":
        # stamped with the session clock now, so the cooldown runs from the
        # click (not from the AI's last decision, possibly long before)
        director.on_override(
            item_idx=msg.item_idx, slide_idx=msg.slide_idx, t_seconds=_state.audio_now()
        )


# --- Broadcast helpers ----------------------------------------------------
# `_broadcast`/`_safe_send`/`_send`/`_send_error` live in `server_state.py`
# now, alongside `_state` itself — see that module's docstring for why.


async def _broadcast_transcript_event(event: TranscriptEvent) -> None:
    msg = TranscriptPartialMessage(
        words=[
            ProtocolWord(
                text=w.text,
                t_start=w.t_start,
                t_end=w.t_end,
                confidence=w.confidence,
            )
            for w in event.words
        ],
        is_final=event.is_final,
    )
    await _broadcast(msg)


async def _broadcast_state_update(
    update: StateUpdate, *, setlist_id: str | None = None
) -> None:
    msg = AudioSessionStateMessage(
        state=update.state, error=update.error, setlist_id=setlist_id,
        spoken_instructions=(
            _state.spoken_instructions_enabled if update.state == "running" else None
        ),
    )
    await _broadcast(msg)


def _session_state_handler(learning, setlist_id: str | None = None, generation: int | None = None):
    """The session's state callback: broadcast it, and when the session ends
    on its own (injected audio ran out, the device went away) write the
    learning observer's final rows — the stop request is not the only way out.

    An error from a session that has since been replaced is not broadcast: a
    microphone open that hung (a pending permission prompt, CoreAudio stuck)
    is abandoned by the stop's deadline, and when it finally fails minutes
    later, the app read it as the *current* session failing — on 2026-10-26
    it ended a service that was running fine on its replacement."""

    async def on_state(update: StateUpdate) -> None:
        if (
            update.state == "error"
            and generation is not None
            and generation != _state.session_generation
        ):
            log.warning("a replaced audio session failed late (ignored): %s", update.error)
        else:
            await _broadcast_state_update(update, setlist_id=setlist_id)
        if learning is not None and update.state in ("stopped", "error"):
            await asyncio.to_thread(stop_learning, learning)

    return on_state


_QWEN_LYRICS: dict[str, str] | None = None


def _qwen_follow_song(matcher: Any, session: Any) -> None:
    """Trial: give a Qwen session the lyrics of the song the tracker is on, as
    its context prompt. Lyrics come from a JSON map (song id -> text) named by
    AUTO_PRESENTER_QWEN_LYRICS; nothing happens without it."""
    global _QWEN_LYRICS
    path = os.environ.get("AUTO_PRESENTER_QWEN_LYRICS")
    backend = getattr(session, "_backend", None)
    if not path or backend is None or not hasattr(backend, "context"):
        return
    if _QWEN_LYRICS is None:
        _QWEN_LYRICS = json.loads(Path(path).read_text())
    item = matcher.setlist.item(getattr(matcher, "current_item_idx", -1))
    text = _QWEN_LYRICS.get(item.song_id, "") if item is not None else ""
    near = os.environ.get("AUTO_PRESENTER_QWEN_LYRICS_NEAR")
    belief = getattr(matcher, "belief", None)
    if text and near and belief is not None and item is not None:
        before, after = (int(x) for x in near.split(","))
        top = belief.top_hypothesis[1]
        slide = item.slides[top] if 0 <= top < len(item.slides) else None
        text = _near_lines(text, slide.words if slide is not None else (), before, after)
    backend.context = text


def _near_lines(text: str, slide_words: tuple[str, ...], before: int, after: int) -> str:
    """Trial: the lines of `text` around where `slide_words` start."""
    def norm(w: str) -> str:
        return "".join(c for c in w.lower() if c.isalnum())

    lines = [ln for ln in text.splitlines() if ln.strip()]
    want = [norm(w) for w in slide_words if norm(w)][:4]
    if not lines or not want:
        return text
    best, at = -1, 0
    for i, ln in enumerate(lines):
        got = [norm(w) for w in ln.split() if norm(w)][: len(want)]
        hit = sum(a == b for a, b in zip(got, want, strict=False))
        if hit > best:
            best, at = hit, i
    return "\n".join(lines[max(0, at - before) : at + after + 1])


async def _maybe_qwen_backend(
    whisper: MLXWhisperBackend, tunables: ParticleFilterTunables | None
) -> ASRBackend:
    """Qwen3-ASR streaming in place of Whisper's transcription, when the
    `asr_qwen_streaming` tunable asks for it (off by default). Whisper stays
    loaded for the follower. Any failure leaves the session on Whisper: the
    service never waits on an experiment."""
    if tunables is None or not tunables.asr_qwen_streaming:
        return whisper
    from auto_presenter.asr.qwen_backend import QwenStreamingBackend

    if not QwenStreamingBackend.is_available():
        log.warning("asr_qwen_streaming is on but mlx-qwen3-asr isn't installed; using Whisper")
        return whisper
    qwen = QwenStreamingBackend(whisper)
    try:
        await asyncio.to_thread(qwen.warm)
    except Exception:
        log.exception("Qwen3-ASR failed to load; using Whisper")
        return whisper
    log.info("speech: Qwen3-ASR 0.6B streaming (follower scores with %s)", whisper.name())
    return qwen


def _follow_readings(tunables: ParticleFilterTunables | None) -> bool:
    """Whether to follow a passage read aloud this session.

    Only on MLX, Apple silicon's speech engine — the one path measured fast
    enough. On the processor path a pass takes long enough that the 6 s
    window drops 21-48% of continuous speech (LibriVox readings, 2026-10-24),
    and Groq's online speech is unmeasured on readings. Elsewhere a passage
    keeps its slides but no words, and the AI holds on it. Plus the kill
    switch, `ParticleFilterTunables.follow_readings`.
    """
    if tunables is not None and not tunables.follow_readings:
        return False
    return _state.session_asr_backend == "mlx"


def _lyric_words(content: SetlistContent) -> list[str]:
    return [w for item in content.items for slide in item.slides for w in slide]


async def _tick_watchdog(audio_seconds: float) -> None:
    """Give the freeze watchdog a turn on the audio clock.

    The scheduler is otherwise only reached from `matcher.update`, so a
    display frozen *because the transcript stopped* had nothing to notice it —
    the watchdog ran on the clock that had stalled. Health reports arrive on a
    fixed cadence whether or not any words do, which makes this the one place
    already ticking during a drought.

    A decision from here takes exactly the same route as one from a transcript
    event, blanking check included: it is a real slide change, not a
    diagnostic.
    """
    matcher = _state.matcher
    director = _state.director
    if matcher is None or director is None:
        return
    tick = getattr(matcher, "tick", None)
    if tick is None:
        return
    try:
        decision = tick(audio_seconds)
    except Exception:  # pragma: no cover - defensive
        log.exception("watchdog tick raised; leaving the display alone")
        return
    if decision is None:
        return
    if holding_for_talking():
        restore_screen_record()
        return
    log.info(
        "watchdog moved the display to item %d slide %d with no new transcript",
        decision.item_idx, decision.slide_idx,
    )
    deliver_decision(decision)
    await _broadcast_belief_for_engine(matcher)


def _describe_power(power) -> str:
    """The power state for the log line, so a diagnostics paste shows it."""
    if power is None:
        return "-"
    parts = ["low-power-mode" if power.low_power_mode else None]
    if power.battery_percent is not None:
        parts.append(f"battery={power.battery_percent}%")
        parts.append("on-battery" if power.on_battery else "charging")
    return ",".join(p for p in parts if p) or "mains"


async def _broadcast_pipeline_health(health: PipelineHealth) -> None:
    import time

    _state.audio_clock = (health.audio_seconds, time.monotonic())
    note_time = getattr(_state.director, "note_session_time", None)
    if note_time is not None:
        # a cooldown ends on time even when no decision arrives
        note_time(health.audio_seconds)
    check_ai_move_landed()
    await _tick_watchdog(health.audio_seconds)
    if _state.matcher is not None:
        await _tick_singing(health.audio_seconds)
    power = health.power
    observe_follower_health(health.healthy)
    _state.pipeline_lag_s = health.lag_seconds
    if not health.healthy:
        log.warning(
            "pipeline falling behind: lag=%.1fs dropped_chunks=%d "
            "cpu=%.0f%% rss=%.0fMB asr=%.0fms matcher=%.0fms power=%s",
            health.lag_seconds,
            health.dropped_chunks,
            health.cpu_percent,
            health.rss_mb,
            health.asr_ms,
            health.matcher_ms,
            _describe_power(health.power),
        )
    await _broadcast(
        PipelineHealthMessage(
            lag_seconds=round(health.lag_seconds, 2),
            dropped_chunks=health.dropped_chunks,
            healthy=health.healthy,
            cpu_percent=health.cpu_percent,
            rss_mb=health.rss_mb,
            asr_ms=round(health.asr_ms, 1),
            matcher_ms=round(health.matcher_ms, 1),
            asr_refused=health.asr_refused,
            low_power_mode=power.low_power_mode if power else False,
            battery_percent=power.battery_percent if power else None,
            on_battery=power.on_battery if power else False,
            **_scorer_health(),
        )
    )


def _scorer_health() -> dict:
    out: dict = {}
    for name, cost in (("follower", _state.follower_cost), ("chord", _state.chord_cost)):
        window = cost.take()
        if window is not None:
            out[f"{name}_ms_per_s"] = round(window[0], 1)
            out[f"{name}_frame_ms_p95"] = round(window[1], 2)
    followers = _state.followers
    recent = list(getattr(followers, "scorer_ms", ())[-10:]) if followers is not None else []
    if recent:
        out["scorer_ms"] = round(float(sorted(recent)[len(recent) // 2]), 1)
        out["scorer_spans"] = int(getattr(followers, "scorer_spans", 0))
    listener = getattr(followers, "listener", None) if followers is not None else None
    if listener is not None and listener.ms:
        recent = sorted(listener.ms[-20:])
        out["next_line_ms"] = round(float(recent[len(recent) // 2]), 1)
        out["next_line_skipped"] = int(listener.skipped)
    return out


async def _broadcast_audio_level(level: AudioLevel) -> None:
    # Pydantic doesn't serialize ±inf to valid JSON; clamp to a display
    # floor of -120 dBFS (well below any human-meaningful loudness).
    rms = level.rms_dbfs if level.rms_dbfs != float("-inf") else -120.0
    msg = AudioLevelMessage(
        t_seconds=level.t_seconds,
        rms_dbfs=rms,
        peak=level.peak,
    )
    await _broadcast(msg)


async def _broadcast_ai_decision(decision: MatcherDecision) -> None:
    """Broadcast an AI decision as a `SlideChangeMessage(source="ai")`.

    This is what the React `PresentationView` consumes (via the new
    `aiDecisions` hook) and applies through the existing `jumpTo()`
    transition. The `confidence` field rides along so the debug panel
    can show how strongly the matcher committed.
    """
    msg = SlideChangeMessage(
        item_idx=decision.item_idx,
        slide_idx=decision.slide_idx,
        confidence=decision.confidence,
        source="ai",
    )
    await _broadcast(msg)
    if note_ai_move(decision.item_idx, decision.t_seconds):
        await refresh_blanking()


async def _broadcast_ai_status(status: AIStatus) -> None:
    msg = AIStatusEvent(
        mode=status.mode,
        state=status.state,
        last_decision_t_seconds=status.last_decision_t_seconds,
        last_override_t_seconds=status.last_override_t_seconds,
    )
    await _broadcast(msg)


async def _broadcast_particle_belief(
    belief: BeliefState, item_idx: int, is_lost: bool | None = None
) -> None:
    """Belief broadcast: top-3 hypotheses + ESS + whether we're lost.

    `is_lost` comes from the tracker when it knows (the forward filter does).
    The fallback is a diffuseness heuristic, for a tracker that doesn't.
    """
    top_3 = belief.top_3
    n = len(belief.particles)
    if is_lost is None:
        is_lost = (top_3[0][2] if top_3 else 0.0) < 0.2 or belief.ess < 0.1 * max(1, n)
    msg = BeliefStateMessage(
        top_3=[
            Hypothesis(
                song_id=str(item),
                slide_idx=slide,
                marginal=marginal,
            )
            for (item, slide, marginal) in top_3
        ],
        ess=belief.ess,
        is_lost=is_lost,
    )
    await _broadcast(msg)


async def _broadcast_model_progress(event: DownloadProgressEvent) -> None:
    msg = ModelDownloadProgressMessage(
        model_size=event.model_size,
        bytes_downloaded=event.bytes_downloaded,
        bytes_total=event.bytes_total,
        fraction=event.fraction,
        state=event.state,
        detail=event.detail,
    )
    await _broadcast(msg)


# --- AI matcher handlers --------------------------------------------------------


def _apply_observed_lag(matcher: MatchingEngine) -> None:
    """Feed the scheduler the delivery lag its lead time is derived from.

    The lead is `lag + gap + margin`, so this is an input to every commit
    decision, not a readout — it belongs on the path that updates the belief.
    Applied per event rather than at startup so the lead tracks a pipeline
    that slows down mid service. Leaves `observed_lag_seconds` alone until
    something has actually measured it, so the config fallback stands in
    rather than a zero that would read as "instant".
    """
    if not isinstance(matcher, ParticleFilterEngine):
        return
    session = _state.audio_session
    if session is None:
        return
    snap = session.latency_snapshot()
    if snap is None:
        return
    matcher.scheduler.observed_lag_seconds = snap.median_ms / 1000.0


_DOUBTFUL = DoubtfulWordFilter()


def _frame_fanout(recording_sink):
    """Send every captured frame to the recorder and the live music analysis.

    The music analysis is checked per frame rather than captured at setup so
    the debug panel's toggles take effect mid-session without restarting the
    audio pipeline.
    """
    write = recording_sink.write_frame if recording_sink else None

    def observe(frame):
        if write is not None:
            write(frame)
        observe_music(frame)

    return observe


def _make_transcript_handler(
    matcher: MatchingEngine | None,
    director: Director | None,
):
    """Build the AudioSession's `on_event` callback for the current session.

    The handler does three things, in order:

      1. Broadcast every transcript event over the WS (debug panel,
         transcript tab, etc.).
      2. If a matcher exists, refresh the scheduler's measured lag, feed it
         the event, and pass any resulting `MatcherDecision` to the director.
      3. Broadcast a belief snapshot so the debug panel chart animates
         even when no commit fires (`BeliefStateMessage`: top-3 + ESS).

    Steps 1 and 2 are independent — the transcript flows to the UI even
    when there's no matcher (transcript-only sessions).
    """

    async def handler(event: TranscriptEvent) -> None:
        await _broadcast_transcript_event(event)
        if matcher is None or director is None:
            return
        # The matcher consumes *stable* (final) events only. Partial events
        # re-emit words that later finalize, so feeding them would double-count
        # lyrics into the rolling buffer / particle filter and prevent the
        # belief from ever converging on a commit. Every offline pipeline
        # (run_baseline, sweep_pf, run_realtime_eval) filters the same way.
        if not event.is_final:
            # ...but words still arriving mean someone is still singing. Held
            # notes ("Alleluia" in Agnus Dei) went 16 s with partials and no
            # final, and read as silence — a blank mid-song.
            if event.words:
                _state.singing.heard()
            return
        session = _state.audio_session
        _apply_observed_lag(matcher)
        # every final word, doubtful ones included: the plan gate asks what
        # was said, not what the matcher should score
        gate = getattr(getattr(matcher, "scheduler", None), "plan_gate", None)
        if gate is not None:
            gate.hear(event.words)
        # Only the matcher loses doubtful words; everything below keeps the
        # original event — see `asr.junk.DoubtfulWordFilter`.
        kept = _DOUBTFUL.keep(
            event.words,
            talking_score=_state.singing.score,
            silent=_state.singing.silent,
            singing_established=_state.singing.established,
        )
        # An event this *emptied* is skipped outright. Passing it on word-less
        # still runs the scheduler, and at session start the untouched prior
        # already reads as certain of the first lyric slide — measured: the
        # screen left the blank slide at t=0 on an event of pure talk.
        matcher_event = (
            event if len(kept) == len(event.words)
            else event.model_copy(update={"words": kept})
        )
        learning_words(kept)
        if _state.active_recording is not None:
            _state.active_recording.record_words(kept)
        decision = None
        try:
            if event.words and not kept:
                pass
            elif session is not None:
                with session.perf.stage(STAGE_MATCHER):
                    decision = matcher.update(matcher_event)
            else:
                decision = matcher.update(matcher_event)
        except Exception:  # pragma: no cover - defensive
            log.exception("matcher.update raised; dropping event")
            return
        _qwen_follow_song(matcher, session)
        # Reporting and side features. None may abort the decision (each is
        # guarded: an optional feature failing used to skip
        # `director.on_decision` and drop a real slide change), and none may
        # delay it: spoken requests wait on a hosted model and an import
        # rebuilds the opening watch, seconds in all, after which an older
        # decision could land on top of a newer one. So: what the hold check
        # needs (the singing reading), then the decision, then the rest.
        async def run(observers) -> None:
            for observe in observers:
                try:
                    await observe
                except Exception:
                    log.exception("transcript observer failed; the slide decision stands")

        await run((
            _broadcast_belief_for_engine(matcher),
            _observe_singing(matcher, event),
        ))
        if decision is not None:
            # A decision reached while we do not believe anyone is singing
            # these words is not evidence about the screen — it is the tracker
            # following a pastor's sentence into a verse. Hold the display.
            if holding_for_talking():
                restore_screen_record()
            else:
                deliver_decision(decision, director)
        await run((
            _observe_flow(matcher, director, event),
            _observe_spoken_instructions(matcher, director, event),
            observe_off_setlist(matcher, director, event),
            server_assist.observe_assist(matcher),
        ))

    return handler


async def _observe_singing(matcher: MatchingEngine, event) -> None:
    """Fold this event's match quality into the singing detector, and report."""
    belief = _matcher_belief(matcher)
    if belief is None or event.t_end is None:
        return
    was = _state.blanking
    _state.singing.observe(belief.evidence, event.t_end, len(event.words))
    _state.blanking = _should_blank()
    # Leaving a song needs the room to sound like singing — see
    # `LeadTimeSchedulerConfig.cross_song_max_talking_score`. Lands one event
    # late (the matcher already ran), which a smoothed score absorbs.
    scheduler = getattr(matcher, "scheduler", None)
    if scheduler is not None:
        scheduler.talking_score = _state.singing.score
        scheduler.band_playing = _state.band.playing
    await _report_singing(was)


def _should_blank() -> bool:
    """A long silence, and — only when `blank_on_talking` — words that are not
    the ones on screen."""
    if not _state.blank_when_idle:
        return False
    singing = _state.singing
    return (
        singing.long_silence
        or _state.song_unconfirmed
        or (_state.blank_on_talking and singing.off_book)
    )


async def refresh_blanking() -> None:
    """Re-read `_should_blank` outside the singing detector's own events
    (the AI's song change was not heard, or then was)."""
    was = _state.blanking
    _state.blanking = _should_blank()
    if _state.blanking != was:
        await _report_singing(was)


def holding_for_talking() -> bool:
    """Withhold the AI's moves: the tracker would be following a pastor's
    sentence into a verse. Only when blanking on talking is on (see
    `_ServerState.blank_on_talking` for why it is off)."""
    return _state.blank_when_idle and _state.blank_on_talking and _state.singing.off_book


async def _report_singing(was_blanking: bool) -> None:
    await _broadcast(
        SingingStateMessage(
            singing=_state.singing.singing,
            reason=_state.singing.reason,
            blanking=_state.blanking,
            score=_state.singing.score,
        )
    )
    if _state.blanking != was_blanking:
        log.info("display %s (%s)",
                 "blanked — nobody is singing these words" if _state.blanking
                 else "restored", _state.singing.reason)


async def _tick_singing(audio_seconds: float) -> None:
    """Advance the singing detector's clock with no words arriving.

    Silence is noticed only by elapsed time (`SingingDetector.tick`), and
    nothing called it: the detector moved only on transcript events, which
    is exactly what silence doesn't produce. Live, 2026-10-15: gaps of 16, 23
    and 25 s with no words, and not one reading of "silence".
    """
    was = _state.blanking
    _state.singing.tick(audio_seconds)
    _state.blanking = _should_blank()
    if _state.blanking != was:
        await _report_singing(was)


async def _handle_set_blank_when_idle(
    ws: ServerConnection, msg: SetBlankWhenIdleRequest
) -> None:
    _state.blank_when_idle = msg.enabled
    if not msg.enabled:
        _state.blanking = False
    log.info("blank-when-idle %s", "on" if msg.enabled else "off")
    await _send(ws, PongMessage(id=msg.id))


async def _broadcast_engine_telemetry(matcher: MatchingEngine) -> None:
    """Publish the three numbers that decide what happens next.

    Read off the live objects rather than recomputed: the countdown comes from
    `LeadTimeScheduler.project()`, the same call `on_position` branches on, and
    the lag from the wrapper's own emission hook. A debug readout that derives
    its own version of either can drift from the engine and mislead exactly
    when it is being trusted most.

    Strictly read-only. Feeding the scheduler its measured lag used to happen
    here, which made every commit decision depend on a *broadcast* running —
    fine today, but one "skip telemetry when nobody is watching" away from
    silently reverting the lead to `fallback_lag_seconds` with nothing failing.
    That now lives in `_apply_observed_lag`, on the belief-update path.
    """
    msg = EngineTelemetryMessage()

    if isinstance(matcher, ParticleFilterEngine):
        scheduler = matcher.scheduler
        state = scheduler.telemetry_snapshot()
        msg.lead_time_ms = scheduler.effective_lead_seconds() * 1000.0
        msg.lead_lag_ms = (
            scheduler.observed_lag_seconds
            if scheduler.observed_lag_seconds is not None
            else scheduler.config.fallback_lag_seconds
        ) * 1000.0
        msg.lead_gap_ms = (state.gap_seconds or 0.0) * 1000.0
        msg.target_margin_ms = float(scheduler.config.target_margin_ms)
        belief = matcher.belief
        if belief.particles:
            item_idx, mode_slide, _marginal = belief.top_hypothesis
            msg.rate_wps = belief.mode_rate
            projection = scheduler.project(
                TrackedPosition(
                    item_idx=item_idx,
                    slide_idx=mode_slide,
                    confidence=belief.commit_confidence,
                    position=belief.mode_position,
                    rate=belief.mode_rate,
                    t_seconds=belief.t_seconds,
                )
            )
            if projection is not None:
                msg.seconds_remaining = projection.seconds_remaining
                msg.advancing = projection.advancing
                msg.target_slide_idx = projection.target_slide
                msg.position = projection.position
                msg.words_on_slide = projection.n_words
                # The floored rate, which is what the countdown divided by.
                msg.rate_wps = projection.rate_wps
                msg.ending_probability = projection.ending_probability
                msg.ending_probability_bar = scheduler.config.transition_probability
                msg.slides_ahead = projection.slides_ahead
                msg.states_on_slide = len(belief.top_position_spread())

            msg.stall_after_seconds = scheduler.config.stall_after_seconds
            if state.stall_since is not None:
                msg.stalled_seconds = belief.t_seconds - state.stall_since
            if state.pending_item is not None:
                msg.pending_item_idx = state.pending_item
                msg.pending_item_observations = state.pending_count

    session = _state.audio_session
    if session is not None:
        snap = session.latency_snapshot()
        if snap is not None:
            msg.latency_median_ms = snap.median_ms
            msg.latency_p90_ms = snap.p90_ms
            msg.latency_samples = snap.samples

    await _broadcast(msg)


async def _observe_flow(
    matcher: MatchingEngine, director: Director, event: TranscriptEvent
) -> None:
    """Advance the continuous-mode track and broadcast it when it changes.

    Runs whatever the display mode: which screen consumes it is the
    frontend's choice, and the follower is cheap next to the matcher. It reads
    the forward filter's posterior directly.
    """
    if not event.words:
        return
    if following_current_song():
        return  # the score follower places the column (`observe_follower_flow`)
    setlist = _indexed_setlist(matcher)
    inner = getattr(getattr(matcher, "filter", None), "filter", None)
    alpha = getattr(inner, "_alpha", None)
    if setlist is None or alpha is None:
        return
    follower = _state.flow_follower
    if follower is None or follower.setlist is not setlist:
        # A reindex swaps the setlist object; the word offsets go with it.
        follower = _state.flow_follower = FlowFollower(setlist=setlist)
    follower.observe_words([w.t_end for w in event.words])
    if getattr(director, "state", None) == "following_human":
        current = setlist.item(getattr(matcher, "current_item_idx", -1))
        if current is None or not current.spoken:
            # The operator has the screen. Drop the track rather than keep
            # extrapolating one nobody is showing: the first update after the
            # cooldown starts fresh from wherever the override re-anchored the
            # tracker, which is where the operator put the column.
            follower.track = None
            return
        # A reading put up (or moved) by hand or by voice is to be followed
        # from there straight away: the cooldown is a line or two read past
        # a still screen. Restart the track once per override, from where it
        # re-anchored the tracker, so an old track can't pull the column back.
        override_t = getattr(director, "last_override_t", None)
        if override_t != _state.flow_override_t:
            _state.flow_override_t = override_t
            follower.track = None
    t_observed = event.words[-1].t_end
    session = _state.audio_session
    t_now = session.latency.last_available_at if session is not None else None
    track = follower.update(
        alpha,
        t_observed=t_observed,
        t_now=max(t_observed, t_now) if t_now is not None else t_observed,
    )
    if track is None:
        return
    await _broadcast(
        FlowPositionMessage(
            # Positional, like `SlideChangeMessage` from `_broadcast_ai_decision`:
            # both the index and the UI's presentation context skip empty slots.
            item_idx=track.item_idx,
            word=track.word0,
            rate_wps=track.rate_wps,
            correction=track.correction,
            settle_seconds=track.settle_seconds,
            limit=track.limit,
            jump=track.jump,
            confidence=track.confidence,
        )
    )


async def _broadcast_belief_for_engine(matcher: MatchingEngine) -> None:
    """The belief and telemetry broadcasts for the running engine."""
    if isinstance(matcher, ParticleFilterEngine):
        # The tracker knows better than the diffuseness fallback, which is
        # shaped for a particle cloud and inverts on a dense posterior.
        lost = getattr(matcher.filter, "is_lost", None)
        await _broadcast_particle_belief(
            matcher.belief, matcher.current_item_idx, is_lost=lost
        )
    await _broadcast_engine_telemetry(matcher)


async def _handle_set_tunables(
    ws: ServerConnection, msg: SetTunablesRequest
) -> None:
    director = _state.director
    matcher = _state.matcher
    # Always update the persisted defaults so the next session starts with
    # the operator's chosen knobs.
    _state.tunables = msg.tunables
    await _broadcast(TunablesUpdatedMessage(tunables=msg.tunables))

    if matcher is None or director is None or not isinstance(
        matcher, ParticleFilterEngine
    ):
        await _send(ws, PongMessage(id=msg.id))
        return

    # Only the scheduler's knobs are reachable live; the rest of the
    # tunables are read when a session starts.
    matcher.scheduler.config = LeadTimeSchedulerConfig(
        target_margin_ms=msg.tunables.target_margin_ms,
        marginal_threshold=msg.tunables.marginal_threshold,
    )
    await _send(ws, PongMessage(id=msg.id))



def _reanchor_after_edit(matcher, indexed, item_idx: int) -> None:
    """After a mid-service setlist edit, put the engine back on the slide on
    screen. `reindex` resets the tracker to "any song's opening" and clears
    the scheduler's record of the screen, and with no record the bar for
    leaving the song and the plan gate are skipped: two stray words from
    another song's first line moved the screen there. `item_idx` is the
    current item in the edited setlist; the slide is the one last shown,
    within the (possibly edited) song."""
    item = indexed.item(item_idx)
    if item is None or not item.slides:
        return
    slide_idx = _state.last_slide[1] if _state.last_slide is not None else 0
    slide_idx = max(0, min(slide_idx, len(item.slides) - 1))
    try:
        matcher.commit_external(item_idx, slide_idx)
    except Exception:  # pragma: no cover - defensive
        log.exception("re-anchoring after a setlist edit failed")
        return
    _state.last_slide = (item_idx, slide_idx)


async def _handle_update_setlist_content(
    ws: ServerConnection, msg: UpdateSetlistContentRequest
) -> None:
    """Operator edited the running setlist — rebuild the matcher's index.

    Re-anchors the engine to `current_item_idx` so tracking continues from
    where the operator is rather than restarting at the first slide. No-op
    (but acked) for transcript-only sessions with no matcher.

    Note: the recording's baked `setlist_content` snapshot is left at its
    start-of-service value — the eval harness pairs ground-truth indices with
    that snapshot, and rewriting it mid-recording would desync earlier
    ground-truth events. Songs added *to the end* renumber nothing, so those
    are appended (a voice import's slide changes were otherwise unreplayable).
    """
    sink = _state.active_recording
    if sink is not None:
        sink.extend_setlist([
            SetlistItemSnapshot(
                item_idx=i.item_idx, song_id=i.song_id, slides=list(i.slides),
                slide_sections=list(i.slide_sections), title=i.title, spoken=i.spoken,
            )
            for i in msg.setlist_content.items
        ])
    matcher = _state.matcher
    item_count = len(msg.setlist_content.items)
    if matcher is None:
        await _send(
            ws,
            SetlistContentUpdatedMessage(
                id=msg.id,
                item_count=item_count,
                applied=False,
                detail="no active matcher (transcript-only session)",
            ),
        )
        return
    try:
        indexed = build_index(
            msg.setlist_content, follow_readings=_follow_readings(_state.tunables)
        )
        matcher.reindex(indexed, msg.current_item_idx)
        _reanchor_after_edit(matcher, indexed, msg.current_item_idx)
        learning_content(msg.setlist_content)
        if isinstance(matcher, FollowerEngine) and _state.audio_session is not None:
            # Fresh references for the edited setlist (songs added, a sheet
            # imported since). A session that started without any lead sheet
            # keeps the plain engine until the next session.
            followers = await asyncio.to_thread(
                build_session_followers, msg.setlist_content, _state.audio_session._backend
            )
            _state.followers = followers
            matcher.replace_followers(followers)
        if _state.audio_session is not None:
            # the other songs' openings, for the edited setlist
            _state.openings = await asyncio.to_thread(
                build_session_openings, msg.setlist_content, _state.audio_session._backend
            )
            if _state.followers is not None or _state.openings is not None:
                _state.audio_session._wrapper.side_pass = session_side_pass(
                    asyncio.get_running_loop()
                )
    except Exception as exc:
        log.exception("failed to re-index setlist content")
        await _send(
            ws,
            SetlistContentUpdatedMessage(
                id=msg.id, item_count=item_count, applied=False, detail=str(exc)
            ),
        )
        return
    await _send(
        ws,
        SetlistContentUpdatedMessage(id=msg.id, item_count=item_count, applied=True),
    )


async def _handle_set_ai_mode(
    ws: ServerConnection, msg: SetAIModeRequest
) -> None:
    director = _state.director
    if director is None:
        await _send_error(
            ws, msg.id, "no active session — start an audio session with a setlist first"
        )
        return
    if msg.mode != "off" and not live_ai_allowed(msg.mode):
        await _send_error(
            ws,
            msg.id,
            "This computer is fast enough for Assist, but not for the AI to change slides "
            "itself. Choose Assist me, or change slides by hand."
            if live_ai_allowed("assist")
            else "This computer isn't fast enough for live AI at full accuracy, so it's off. "
            "Presenting by hand works as always.",
        )
        return
    previous = director.mode
    director.set_mode(msg.mode)
    if previous == "assist" and msg.mode == "active" and _state.last_slide is not None:
        # Assist lets the engine run free of the screen (operator clicks don't
        # re-anchor it), so its belief can be anywhere. Taking over, it starts
        # from what the screen shows, as after an operator's click — live, it
        # otherwise jumped straight to its own guess (2026-10-28).
        director.on_override(*_state.last_slide)
    server_assist.mode_changed(msg.mode)
    sync_transcribing()
    await _send(ws, PongMessage(id=msg.id))


def live_ai_allowed(mode: str = "active") -> bool:
    """Whether live AI may run in `mode` in this session. Apple silicon and
    hosted speech (Groq) are unchanged; a computer on the local CPU path needs
    a passed machine check (2026-10-15: below the 10% accuracy bar, live AI is
    not offered at all, rather than offered worse) — or, for Assist alone,
    the Assist-only tier's bar (`assist_tier`)."""
    if _state.session_asr_backend != "whisper" or MLXWhisperBackend.is_available():
        return True
    from auto_presenter.asr.machine_check import CPU_PATH_MODEL

    # the check judged this computer with small.en, and nothing else measured
    # well enough to offer
    if _state.session_asr_model != CPU_PATH_MODEL:
        return False
    return cpu_path_enabled() or (mode == "assist" and assist_tier())


def _allowed_start_mode(requested: str) -> str:
    """The AI mode a session starts in: the one asked for, or Assist when only
    Assist is allowed here (an operator who last chose Auto on a computer the
    check has since put in the Assist-only tier), else off."""
    if requested == "off" or live_ai_allowed(requested):
        return requested
    return "assist" if live_ai_allowed("assist") else "off"


# --- Hardware recommendation handler --------------------------------------------


def _scorer_with_speech_model(backend: str) -> bool:
    """Off Apple silicon, the local speech model comes with the ONNX span
    scorer: live following's CPU path needs both, so they download and
    count as one ("the AI's speech model")."""
    return backend == "whisper" and not MLXWhisperBackend.is_available()


async def _handle_model_status(ws: ServerConnection, msg: ModelStatusRequest) -> None:
    from auto_presenter.follower import scorer_models

    hosted = msg.backend == "groq"
    downloaded, mb = (
        (False, 0)
        if hosted
        else await asyncio.to_thread(_local_model_status, msg.model_size, msg.backend)
    )
    if _scorer_with_speech_model(msg.backend):
        scorer_here = await asyncio.to_thread(scorer_models.is_downloaded)
        downloaded = downloaded and scorer_here
        mb += 0 if scorer_here else scorer_models.download_mb()
    await _send(
        ws,
        ModelStatusResponse(
            id=msg.id,
            backend=msg.backend,
            model_size=msg.model_size,
            downloaded=downloaded,
            download_mb=mb,
            needs_internet_while_running=hosted,
        ),
    )


def _mlx_speech_parts(model_size: str) -> list[tuple[str, str, str | None, int]]:
    """What "the speech model" is on Apple silicon, as (model-manager
    backend, Hugging Face repo, pinned revision, MiB): Whisper small.en's MLX
    weights (the follower's lyric scorer, and the fallback) and, where the
    engine carries it, Qwen3-ASR 0.6B (the speech engine)."""
    from auto_presenter.asr.mlx_backend import repo_for
    from auto_presenter.asr.qwen_backend import (
        QWEN_DOWNLOAD_MB,
        QWEN_REPO,
        QWEN_REVISION,
        QwenStreamingBackend,
    )

    parts = [("mlx", repo_for(model_size), None, download_size_mb(model_size))]
    if QwenStreamingBackend.is_available():
        parts.append(("qwen", QWEN_REPO, QWEN_REVISION, QWEN_DOWNLOAD_MB))
    return parts


def _missing_mlx_parts(model_size: str) -> list[tuple[str, str, str | None, int]]:
    return [
        p
        for p in _mlx_speech_parts(model_size)
        if not _state.model_manager.is_downloaded(model_size, p[0])
    ]


def _local_model_status(model_size: str, backend: str) -> tuple[bool, int]:
    """(on this computer, MiB still to download) for a local speech model."""
    if backend == "mlx":
        missing = _missing_mlx_parts(model_size)
        return not missing, sum(p[3] for p in missing)
    if _state.model_manager.is_downloaded(model_size, backend):
        return True, 0
    return False, download_size_mb(model_size)


async def _download_mlx_model(model_size: str) -> None:
    """Fetch the MLX weights if they aren't on disk, with progress events.

    Without this, `MLXWhisperBackend.warm()` did the download implicitly: no
    progress, and when it failed offline the session started anyway and every
    pass returned nothing — "AI: ready" with nothing ever tracked.
    """
    # On disk already: return without queueing behind another size's
    # download. Checked again under the lock, for a download of this size
    # that finishes while we wait.
    if not await asyncio.to_thread(_missing_mlx_parts, model_size):
        return
    lock = _mlx_download_locks.setdefault(model_size, asyncio.Lock())
    async with lock:
        await _download_mlx_model_locked(model_size)


async def _download_mlx_model_locked(model_size: str) -> None:
    parts = await asyncio.to_thread(_missing_mlx_parts, model_size)
    if not parts:
        return
    from huggingface_hub import snapshot_download

    # One bar for the parts still missing: each part's bytes continue from
    # the ones before it, so the bar never runs backwards.
    total = sum(p[3] for p in parts) * 1024 * 1024

    def event(state, detail="", done=0):
        return DownloadProgressEvent(
            model_size=model_size,
            bytes_downloaded=done,
            bytes_total=total,
            state=state,
            detail=detail,
        )

    await _broadcast_model_progress(event("starting", "downloading model from Hugging Face"))
    loop = asyncio.get_running_loop()

    offset = 0
    for backend, repo, revision, mb in parts:

        def report(progress: DownloadProgressEvent, offset: int = offset) -> None:
            done = min(total, offset + progress.bytes_downloaded)
            moved = event("downloading", progress.detail, done)
            asyncio.run_coroutine_threadsafe(_broadcast_model_progress(moved), loop)

        def download(backend: str = backend, repo: str = repo, revision: str | None = revision):
            with _state.model_manager.watch_download(model_size, backend, total, report):
                snapshot_download(repo_id=repo, revision=revision)

        try:
            await asyncio.to_thread(download)
        except Exception as exc:
            detail = download_failed_message(model_size, total) if looks_offline(exc) else str(exc)
            await _broadcast_model_progress(event("failed", detail))
            raise RuntimeError(detail) from exc
        offset += mb * 1024 * 1024
    await _broadcast_model_progress(event("completed", "downloaded", total))


async def _handle_prepare_model(ws: ServerConnection, msg: PrepareModelRequest) -> None:
    if msg.backend == "groq":
        await _send(ws, PrepareModelResponse(id=msg.id, downloaded=False))
        return
    loop = asyncio.get_running_loop()
    try:
        if msg.backend == "mlx":
            if not MLXWhisperBackend.is_available():
                await _send_error(ws, msg.id, "the Apple-silicon speech engine isn't installed")
                return
            await _download_mlx_model(msg.model_size)
        else:
            speech_was_here = await asyncio.to_thread(
                _state.model_manager.is_downloaded, msg.model_size, "whisper"
            )
            from auto_presenter.follower import scorer_models

            scorer_follows = _scorer_with_speech_model(msg.backend) and not await asyncio.to_thread(
                scorer_models.is_downloaded
            )
            combined = (
                download_size_mb(msg.model_size) * 2**20 + scorer_models.CPU_SCORER.bytes
                if scorer_follows and not speech_was_here
                else 0
            )

            speech_done = [0]

            def progress_callback(event: DownloadProgressEvent) -> None:
                speech_done[0] = max(speech_done[0], event.bytes_downloaded)
                if combined:
                    # One download to the operator: the speech model is the
                    # first part of the total, and its "completed" isn't the
                    # end (the UI re-asks for status on it, which then
                    # names only the scorer, and the bar fell back to 0).
                    event = dataclasses.replace(
                        event,
                        bytes_total=combined,
                        state="downloading" if event.state == "completed" else event.state,
                    )
                asyncio.run_coroutine_threadsafe(_broadcast_model_progress(event), loop)

            # Loads it too, which is the point: a model that downloaded but
            # can't load on this machine should say so now, not on Sunday.
            await _load_whisper_backend_async(
                msg.model_size, progress_callback=progress_callback
            )
            if _scorer_with_speech_model(msg.backend):
                # Continue from where the speech model's bar stopped: its
                # bytes on disk can be more than the size estimated.
                offset = (
                    0
                    if speech_was_here
                    else max(download_size_mb(msg.model_size) * 2**20, speech_done[0])
                )
                await _download_scorer_model(msg.model_size, offset)
    except Exception as exc:
        await _send_error(ws, msg.id, str(exc))
        return
    await _send(ws, PrepareModelResponse(id=msg.id, downloaded=True))


async def _download_scorer_model(model_size: str, offset: int) -> None:
    """Fetch the ONNX span scorer (`follower.scorer_models`), reporting
    progress as the rest of "the speech model" download: bytes continue
    from `offset` (the speech model's, when it downloaded in this request),
    so the operator's bar never runs backwards."""
    from auto_presenter.follower import scorer_models

    if await asyncio.to_thread(scorer_models.is_downloaded):
        return
    loop = asyncio.get_running_loop()
    total = offset + scorer_models.CPU_SCORER.bytes

    def event(state: str, done: int = 0, detail: str = "downloading the scoring model"):
        return DownloadProgressEvent(
            model_size=model_size, bytes_downloaded=offset + done, bytes_total=total,
            state=state, detail=detail,
        )

    last = [0.0]

    def progress(done: int, _total: int) -> None:
        now = time.monotonic()
        if now - last[0] >= 0.25:
            last[0] = now
            asyncio.run_coroutine_threadsafe(
                _broadcast_model_progress(event("downloading", done)), loop
            )

    try:
        await asyncio.to_thread(scorer_models.download, progress=progress)
    except Exception as exc:
        detail = (
            download_failed_message(model_size, total) if looks_offline(exc) else str(exc)
        )
        await _broadcast_model_progress(event("failed", detail=detail))
        raise RuntimeError(detail) from exc
    done = scorer_models.CPU_SCORER.bytes
    await _broadcast_model_progress(event("completed", done, "downloaded"))


#: One machine check at a time: the home card, the AI pill and Settings each
#: ask, and two checks timed at once slowed each other into a wrong verdict
#: (an M1 that measures 0.72 alone measured 0.94, twice). Requests that
#: arrive while one runs wait for it and share its verdict.
_machine_check_lock = asyncio.Lock()
#: When the last check finished (monotonic), so a rerun that waited on one
#: already running takes its fresh verdict instead of timing another.
_machine_checked_at = float("-inf")


async def _handle_assess_machine(ws: ServerConnection, msg: AssessMachineRequest) -> None:
    """This computer's verdict for live following (`asr.machine_check`): the
    saved one while it is current, else timed now (off the loop: seconds of
    transcription and scoring on a CPU) and saved."""
    global _machine_checked_at
    from auto_presenter.asr import machine_check

    asked_at = time.monotonic()
    async with _machine_check_lock:
        fresh = _machine_checked_at >= asked_at
        a = saved_verdict() if (not msg.rerun or fresh) else None
        if a is None:
            try:
                a = await asyncio.to_thread(machine_check.assess, onnx_scorer_dir())
            except Exception as exc:
                log.exception("machine check failed")
                await _send_error(ws, msg.id, f"the machine check failed: {exc}")
                return
            _machine_checked_at = time.monotonic()
            path = machine_verdict_path()
            if path is not None:
                try:
                    machine_check.save(a, path)
                except OSError:
                    log.exception("couldn't save the machine check's verdict")
    await _send(
        ws,
        AssessMachineResponse(
            id=msg.id,
            verdict=a.verdict,
            backend=a.backend,
            reason=a.reason,
            speech_load=a.speech_load,
            limit=a.limit,
            transcribe_ms_p90=a.transcribe_ms_p90,
            score_ms_p90=a.score_ms_p90,
            chord_ms_per_s=a.chord_ms_per_s,
            assist_load=a.assist_load,
            assist_limit=a.assist_limit,
        ),
    )


async def _handle_recommend_model(
    ws: ServerConnection, msg: RecommendModelRequest
) -> None:
    """Probe the host and return a recommended Whisper model.

    Hardware detection is cheap (no model load), but `psutil` / `torch`
    imports can be a few hundred ms cold. We run on a worker thread so
    the asyncio loop stays responsive.
    """
    profile = await asyncio.to_thread(detect_hardware)
    rec = recommend_model(profile)
    downloaded, mb = await asyncio.to_thread(_local_model_status, rec.model_size, rec.backend)
    await _send(
        ws,
        RecommendModelResponse(
            id=msg.id,
            model_size=rec.model_size,
            device=rec.device,
            compute_type=rec.compute_type,
            reason=rec.reason,
            has_cuda=profile.has_cuda,
            has_mps=profile.has_mps,
            cpu_count=profile.cpu_count,
            total_ram_gb=profile.total_ram_gb,
            backend=rec.backend,
            model_downloaded=downloaded,
            download_mb=mb,
        ),
    )


