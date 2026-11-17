"""Roundtrip tests for every message type.

These guard the wire protocol: every model serializes to JSON and parses back
to an equal instance. New message types added in later phases should add a
case here.
"""

from __future__ import annotations

import inspect
import json
import typing

import pytest
from pydantic import BaseModel, TypeAdapter

from auto_presenter import protocol
from auto_presenter.protocol import (
    BeliefStateMessage,
    ClientMessage,
    ErrorMessage,
    Hypothesis,
    PingMessage,
    PongMessage,
    ServerMessage,
    SlideChangeMessage,
    TranscriptPartialMessage,
    Word,
)

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
_server_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


@pytest.mark.parametrize(
    ("message", "adapter"),
    [
        (PingMessage(id=1), _client_adapter),
        (PongMessage(id=1), _server_adapter),
        (ErrorMessage(id=2, message="boom"), _server_adapter),
        (SlideChangeMessage(id=None, item_idx=0, slide_idx=0), _server_adapter),
        (
            TranscriptPartialMessage(
                id=None,
                words=[Word(text="hello", t_start=0.1, t_end=0.5, confidence=0.9)],
                is_final=False,
            ),
            _server_adapter,
        ),
        (
            BeliefStateMessage(
                id=None,
                top_3=[Hypothesis(song_id="s1", slide_idx=0, marginal=0.7)],
                ess=0.6,
                is_lost=False,
            ),
            _server_adapter,
        ),
    ],
)
def test_message_roundtrip(message, adapter):
    serialized = message.model_dump_json()
    parsed = adapter.validate_python(json.loads(serialized))
    assert parsed == message


def _union_members(annotated_union: object) -> set[type]:
    """Unwrap `Annotated[Union[A, B, ...], Field(discriminator=...)]` down
    to `{A, B, ...}`."""
    (union,) = typing.get_args(annotated_union)[:1]
    return set(typing.get_args(union))


def _dispatched_message_types() -> set[type]:
    """Every real wire message defined in `protocol` — a `BaseModel`
    subclass, defined in this module (not imported from elsewhere), whose
    `kind` field is a `Literal` tag. Embedded/payload types (`Word`,
    `Hypothesis`, `SlideScore`, `InstructionModelInfo`) have no `kind` field
    and are correctly excluded — they only ever travel nested inside a real
    message — and neither is a payload whose `kind` is plain data
    (`MusicRhythmEventMsg.kind` names an onset's slot, not a message).
    """
    out = set()
    for _name, obj in vars(protocol).items():
        if (
            inspect.isclass(obj)
            and issubclass(obj, BaseModel)
            and obj.__module__ == protocol.__name__
            and "kind" in obj.model_fields
            and typing.get_origin(obj.model_fields["kind"].annotation) is typing.Literal
        ):
            out.add(obj)
    return out


def test_every_message_is_reachable_through_a_union():
    """Regression: `SetSpokenInstructionsRequest` and `SpokenInstructionMessage`
    were defined, imported, and dispatched on by `isinstance` checks in
    `server.py` — but never added to `ClientMessage`/`ServerMessage`. Since
    the server validates every incoming frame through
    `TypeAdapter(ClientMessage).validate_python(...)` *before* any dispatch
    code runs, a message missing from the union fails validation and never
    reaches its handler at all. The toggle looked wired up — server-side
    tests could construct the class directly and call the handler — but no
    real client message of that kind could ever arrive. Nothing short of
    this check catches a class that exists, is imported, and is dispatched
    on, yet is silently unreachable.
    """
    client = _union_members(ClientMessage)
    server = _union_members(ServerMessage)
    reachable = client | server
    missing = _dispatched_message_types() - reachable
    assert not missing, (
        f"defined with a `kind` field but missing from both ClientMessage "
        f"and ServerMessage: {sorted(m.__name__ for m in missing)}"
    )
