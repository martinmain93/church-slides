"""Recording lifecycle + golden-editing + trace-replay WS handlers.

Split out of `server.py` (which had grown to 1770 lines / 55 top-level defs
mixing this with audio-session lifecycle, belief broadcasting,
spoken-instruction orchestration, and dispatch). This group is one of the
cleaner boundaries in that file: every handler here only touches
`_state.recordings_root`, replies over `_send`, and calls into
`recording.golden`/`recording.sink`/`eval.trace` — none of it reaches into
the live audio-session/matcher machinery, so it doesn't participate in the
tighter coupling that keeps that part of `server.py` together for now.
"""

from __future__ import annotations

import asyncio
import logging

from websockets.asyncio.server import ServerConnection

from auto_presenter.protocol import (
    DeleteRecordingRequest,
    DeleteRecordingResponse,
    ExportRecordingRequest,
    ExportRecordingResponse,
    InspectMusicRequest,
    InspectMusicResponse,
    ListRecordingsRequest,
    ListRecordingsResponse,
    LoadRecordingAudioRequest,
    LoadRecordingAudioResponse,
    LoadRecordingRequest,
    LoadRecordingResponse,
    MusicBarPatternMsg,
    MusicChordSpanMsg,
    MusicNoteMsg,
    MusicOnsetBandMsg,
    MusicPitchMsg,
    MusicPulseMsg,
    MusicRhythmEventMsg,
    RecordingSummary,
    InspectLookbackRequest,
    InspectLookbackResponse,
    ReplayRecordingRequest,
    ReplayRecordingResponse,
    SaveGoldenRequest,
    SaveGoldenResponse,
    SlideScore,
    TraceDecisionMsg,
    TraceSampleMsg,
)
from auto_presenter.recording.golden import (
    effective_ground_truth,
    load_audio_b64,
    load_golden,
    save_golden,
)
from auto_presenter.recording.sink import (
    delete_recording,
    export_recording_zip,
    list_recordings,
    load_recording_metadata,
)
from auto_presenter.server_state import _send, _state

log = logging.getLogger(__name__)


async def _handle_list_recordings(
    ws: ServerConnection, msg: ListRecordingsRequest
) -> None:
    if _state.recordings_root is None:
        await _send(ws, ListRecordingsResponse(id=msg.id, recordings=[]))
        return
    rows = await asyncio.to_thread(list_recordings, _state.recordings_root)
    summaries = [
        RecordingSummary(
            id=row["id"],
            setlist_id=row["setlist_id"],
            started_at_ms=row["started_at_ms"],
            duration_seconds=row["duration_seconds"],
            ground_truth_count=row["ground_truth_count"],
            song_ids=row.get("song_ids", []),
            has_golden=row.get("has_golden", False),
            golden_approved=row.get("golden_approved", False),
        )
        for row in rows
    ]
    await _send(ws, ListRecordingsResponse(id=msg.id, recordings=summaries))


async def _handle_delete_recording(
    ws: ServerConnection, msg: DeleteRecordingRequest
) -> None:
    if _state.recordings_root is None:
        await _send(ws, DeleteRecordingResponse(id=msg.id, deleted=False))
        return
    deleted = await asyncio.to_thread(
        delete_recording, _state.recordings_root, msg.recording_id
    )
    await _send(ws, DeleteRecordingResponse(id=msg.id, deleted=deleted))


async def _handle_export_recording(
    ws: ServerConnection, msg: ExportRecordingRequest
) -> None:
    if _state.recordings_root is None:
        await _send(
            ws,
            ExportRecordingResponse(id=msg.id, error="recordings root not set"),
        )
        return
    recording_dir = _state.recordings_root / msg.recording_id
    if not recording_dir.exists():
        await _send(
            ws,
            ExportRecordingResponse(id=msg.id, error=f"unknown recording: {msg.recording_id}"),
        )
        return
    out_path = _state.recordings_root / f"{msg.recording_id}.zip"
    try:
        await asyncio.to_thread(export_recording_zip, recording_dir, out_path)
    except Exception as exc:
        await _send(ws, ExportRecordingResponse(id=msg.id, error=str(exc)))
        return
    await _send(ws, ExportRecordingResponse(id=msg.id, zip_path=str(out_path)))


def _music_response(msg_id: int | None, payload: dict) -> InspectMusicResponse:
    return InspectMusicResponse(
        id=msg_id,
        duration_seconds=payload.get("duration_seconds", 0),
        partial=bool(payload.get("partial", False)),
        through_seconds=float(
            payload.get("through_seconds", payload.get("duration_seconds", 0))
        ),
        beats=payload.get("beats", []),
        downbeats=payload.get("downbeats", []),
        beat_backend=payload.get("beat_backend", ""),
        chords=[MusicChordSpanMsg(**c) for c in payload.get("chords", [])],
        chord_backend=payload.get("chord_backend", ""),
        pulse=[MusicPulseMsg(**p) for p in payload.get("pulse", [])],
        onsets=[MusicOnsetBandMsg(**o) for o in payload.get("onsets", [])],
        pitch=[MusicPitchMsg(**p) for p in payload.get("pitch", [])],
        notes=[MusicNoteMsg(**n) for n in payload.get("notes", [])],
        pitch_backend=payload.get("pitch_backend", ""),
        source=payload.get("source", "mix"),
        stage=payload.get("stage", ""),
        rhythm=[MusicRhythmEventMsg(**e) for e in payload.get("rhythm", [])],
        rhythm_bars=[MusicBarPatternMsg(**b) for b in payload.get("rhythm_bars", [])],
        error=payload.get("error"),
    )


async def _handle_inspect_music(
    ws: ServerConnection, msg: InspectMusicRequest
) -> None:
    if _state.recordings_root is None:
        await _send(ws, InspectMusicResponse(id=msg.id, error="recordings root not set"))
        return
    root = _state.recordings_root.resolve()
    recording_dir = (root / msg.recording_id).resolve()
    if recording_dir.parent != root or not recording_dir.is_dir():
        await _send(
            ws,
            InspectMusicResponse(
                id=msg.id, error=f"unknown recording: {msg.recording_id}"
            ),
        )
        return
    session = _state.audio_session
    if session is not None and session.is_live:
        await _send(
            ws,
            InspectMusicResponse(
                id=msg.id,
                error="music analysis waits until the live session stops",
            ),
        )
        return
    from auto_presenter.music.inspect import (
        CACHE_NAME,
        STEM_CACHE_NAME,
        iter_analyse,
        iter_analyse_stems,
    )

    analyse = iter_analyse_stems if msg.source == "stems" else iter_analyse
    cache_name = STEM_CACHE_NAME if msg.source == "stems" else CACHE_NAME
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def worker() -> None:
        try:
            for payload in analyse(recording_dir):
                loop.call_soon_threadsafe(queue.put_nowait, ("ok", payload))
        except Exception as exc:
            log.exception("inspect_music failed")
            loop.call_soon_threadsafe(queue.put_nowait, ("err", exc))
        else:
            loop.call_soon_threadsafe(queue.put_nowait, ("end", None))

    worker_task = asyncio.create_task(asyncio.to_thread(worker))
    try:
        while True:
            kind, item = await queue.get()
            if kind == "end":
                break
            if kind == "err":
                await _send(ws, InspectMusicResponse(id=msg.id, error=str(item)))
                return
            try:
                reply = _music_response(msg.id, item)
            except Exception as exc:
                log.exception("inspect_music response failed")
                # A readable cache with a field the protocol rejects would
                # otherwise raise here, send nothing, and leave the panel waiting.
                (recording_dir / cache_name).unlink(missing_ok=True)
                await _send(ws, InspectMusicResponse(id=msg.id, error=str(exc)))
                return
            await _send(ws, reply)
    finally:
        await worker_task


async def _handle_load_recording(
    ws: ServerConnection, msg: LoadRecordingRequest
) -> None:
    if _state.recordings_root is None:
        await _send(ws, LoadRecordingResponse(id=msg.id, error="recordings root not set"))
        return
    recording_dir = _state.recordings_root / msg.recording_id
    recording = await asyncio.to_thread(load_recording_metadata, recording_dir)
    if recording is None:
        await _send(
            ws,
            LoadRecordingResponse(id=msg.id, error=f"unknown recording: {msg.recording_id}"),
        )
        return
    golden = await asyncio.to_thread(load_golden, recording_dir)
    await _send(
        ws,
        LoadRecordingResponse(
            id=msg.id,
            recording=recording,
            golden_ground_truth=(list(golden.ground_truth) if golden else None),
            golden_approved=bool(golden and golden.approved),
        ),
    )


async def _handle_load_recording_audio(
    ws: ServerConnection, msg: LoadRecordingAudioRequest
) -> None:
    if _state.recordings_root is None:
        await _send(
            ws, LoadRecordingAudioResponse(id=msg.id, error="recordings root not set")
        )
        return
    recording_dir = _state.recordings_root / msg.recording_id
    stem = (msg.stem or "").strip()
    if stem:
        from auto_presenter.music.realtime_stems import STEMS

        if stem not in STEMS:
            await _send(
                ws, LoadRecordingAudioResponse(id=msg.id, error=f"unknown stem: {stem}")
            )
            return
        audio_file = recording_dir / "stems" / f"{stem}.wav"
    else:
        audio_file = None
    try:
        audio_b64, sample_rate, duration = await asyncio.to_thread(
            load_audio_b64, recording_dir, audio_file
        )
    except FileNotFoundError as exc:
        await _send(ws, LoadRecordingAudioResponse(id=msg.id, error=str(exc)))
        return
    await _send(
        ws,
        LoadRecordingAudioResponse(
            id=msg.id,
            audio_base64=audio_b64,
            sample_rate=sample_rate,
            duration_seconds=duration,
        ),
    )


async def _handle_replay_recording(
    ws: ServerConnection, msg: ReplayRecordingRequest
) -> None:
    """Replay a stored recording through the matcher and return its trace.

    Runs on a worker thread: a few hundred belief updates over a five-minute
    recording takes long enough to stall the event loop, and this is a debug
    tool that must not disturb a live session.
    """
    if _state.recordings_root is None:
        await _send(
            ws, ReplayRecordingResponse(id=msg.id, error="recordings root not set")
        )
        return
    recording_dir = _state.recordings_root / msg.recording_id
    recording = await asyncio.to_thread(load_recording_metadata, recording_dir)
    if recording is None:
        await _send(
            ws,
            ReplayRecordingResponse(
                id=msg.id, error=f"unknown recording: {msg.recording_id}"
            ),
        )
        return
    if not recording.setlist_content:
        await _send(
            ws,
            ReplayRecordingResponse(
                id=msg.id, error="recording has no setlist snapshot to match against"
            ),
        )
        return

    # The golden timeline is the honest ground truth, so prefer it for the
    # synthesized transcript when a human has approved one.
    recording.ground_truth = effective_ground_truth(recording_dir, recording)

    try:
        result = await asyncio.to_thread(
            _run_trace,
            recording,
            msg.target_margin_ms,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("replay failed for %s", msg.recording_id)
        await _send(ws, ReplayRecordingResponse(id=msg.id, error=str(exc)))
        return

    await _send(ws, result.model_copy(update={"id": msg.id}))


def _run_trace(recording, target_margin_ms: int) -> ReplayRecordingResponse:
    # Imported lazily so a sidecar start doesn't pay for eval tooling.
    from auto_presenter.eval.trace import trace_recording

    trace = trace_recording(recording, target_margin_ms=target_margin_ms)
    return ReplayRecordingResponse(
        samples=[
            TraceSampleMsg(
                t_seconds=s.t_seconds,
                item_idx=s.item_idx,
                slide_idx=s.slide_idx,
                marginal=s.marginal,
                ess=s.ess,
                evidence=s.evidence,
                mode_position=s.mode_position,
                mode_rate=s.mode_rate,
                is_lost=s.is_lost,
                displayed_slide=s.displayed_slide,
                top_3=[
                    SlideScore(item_idx=i, slide_idx=sl, score=sc)
                    for i, sl, sc in s.top_3
                ],
                words=s.words,
            )
            for s in trace.samples
        ],
        decisions=[
            TraceDecisionMsg(
                t_seconds=d.t_seconds, item_idx=d.item_idx, slide_idx=d.slide_idx
            )
            for d in trace.decisions
        ],
        notes=trace.notes,
    )


def _golden_kind(recording_dir) -> str:
    golden = load_golden(recording_dir)
    if golden is None:
        return "shown"
    return "approved" if golden.approved else "estimated"


async def _handle_inspect_lookback(ws: ServerConnection, msg: InspectLookbackRequest) -> None:
    """The learning look-back over a stored recording, against its golden.

    On a worker thread: the first "asr" run transcribes the whole recording
    (cached after), and even a cached run is a few hundred ms per song."""
    from auto_presenter.learning.inspect import available_sources, inspect_lookback, words_for

    if _state.recordings_root is None:
        await _send(ws, InspectLookbackResponse(id=msg.id, error="recordings root not set"))
        return
    recording_dir = _state.recordings_root / msg.recording_id
    recording = await asyncio.to_thread(load_recording_metadata, recording_dir)
    if recording is None or not recording.setlist_content:
        await _send(
            ws,
            InspectLookbackResponse(id=msg.id, error=f"no setlist recording {msg.recording_id}"),
        )
        return
    golden = effective_ground_truth(recording_dir, recording)

    def run():
        words, used = words_for(recording, recording_dir, golden, msg.source)
        return used, inspect_lookback(recording, golden, words)

    try:
        used, items = await asyncio.to_thread(run)
    except Exception as exc:
        log.exception("look-back inspection failed for %s", msg.recording_id)
        await _send(ws, InspectLookbackResponse(id=msg.id, error=str(exc)))
        return
    await _send(
        ws,
        InspectLookbackResponse(
            id=msg.id,
            source=used,
            sources_available=available_sources(recording_dir),
            golden_kind=_golden_kind(recording_dir),
            items=items,
        ),
    )


async def _handle_save_golden(ws: ServerConnection, msg: SaveGoldenRequest) -> None:
    if _state.recordings_root is None:
        await _send(ws, SaveGoldenResponse(id=msg.id, error="recordings root not set"))
        return
    recording_dir = _state.recordings_root / msg.recording_id
    if not recording_dir.exists():
        await _send(
            ws,
            SaveGoldenResponse(id=msg.id, error=f"unknown recording: {msg.recording_id}"),
        )
        return
    try:
        await asyncio.to_thread(
            save_golden,
            recording_dir,
            msg.ground_truth,
            recording_id=msg.recording_id,
            notes=msg.notes,
            approved=msg.approved,
        )
    except Exception as exc:
        await _send(ws, SaveGoldenResponse(id=msg.id, error=str(exc)))
        return
    await _send(ws, SaveGoldenResponse(id=msg.id, saved=True))
