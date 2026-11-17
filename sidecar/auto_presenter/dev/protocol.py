"""WebSocket messages that exist only under `--dev-tools`.

Kept out of `auto_presenter.protocol` so they never reach codegen or a
production client.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class _Base(BaseModel):
    id: int | None = None


class InjectAudioRequest(_Base):
    kind: Literal["inject_audio"] = "inject_audio"
    #: Stored recording directory name under the sidecar recordings root.
    recording_id: str | None = None
    #: Explicit WAV path. Used when the file is not a captured recording.
    wav_path: str | None = None
    #: Pace frames to wall clock (the live-app path). Tests pass False.
    realtime: bool = True
    #: A `start_audio_session` request to start from, instead of the one the
    #: app last sent. Lets a harness run a session with no microphone at all
    #: (a CI machine has none): the file is the capture from the first frame.
    session: dict[str, Any] | None = None


class InjectAudioResponse(_Base):
    kind: Literal["inject_audio_response"] = "inject_audio_response"
    recording_id: str | None = None
    wav_path: str
    error: str | None = None


class SessionSnapshotRequest(_Base):
    kind: Literal["session_snapshot"] = "session_snapshot"


class SessionSnapshotResponse(_Base):
    kind: Literal["session_snapshot_response"] = "session_snapshot_response"
    snapshot: dict[str, Any]


class WaitForSlideRequest(_Base):
    kind: Literal["wait_for_slide"] = "wait_for_slide"
    item_idx: int
    slide_idx: int
    timeout_seconds: float = 60.0


class WaitForSlideResponse(_Base):
    kind: Literal["wait_for_slide_response"] = "wait_for_slide_response"
    reached: bool
    item_idx: int | None = None
    slide_idx: int | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None


DevClientMessage = Annotated[
    InjectAudioRequest | SessionSnapshotRequest | WaitForSlideRequest,
    Field(discriminator="kind"),
]

adapter: TypeAdapter[DevClientMessage] = TypeAdapter(DevClientMessage)
