"""Decision-layer types — `AIMode` + `AIStatus` snapshots.

These mirror what the WS protocol carries to the UI; keeping them as
plain dataclasses inside the decision module keeps the Director's
public surface free of pydantic / WS coupling for the unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AIMode = Literal["off", "shadow", "assist", "active"]
AIState = Literal["idle", "tracking", "following_human"]


@dataclass(frozen=True)
class AIStatus:
    """The status pill's view of the AI's current behavior."""

    mode: AIMode
    state: AIState
    last_decision_t_seconds: float | None = None
    last_override_t_seconds: float | None = None
