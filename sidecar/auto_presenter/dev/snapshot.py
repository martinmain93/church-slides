"""Read-only view of the live session for agent tests."""

from __future__ import annotations

from typing import Any

from auto_presenter.dev import inject
from auto_presenter.server_state import _matcher_belief, _state


def build_snapshot() -> dict[str, Any]:
    session = _state.audio_session
    matcher = _state.matcher
    director = _state.director
    belief = _matcher_belief(matcher) if matcher is not None else None
    top = None
    if belief is not None:
        hyp = getattr(belief, "top_hypothesis", None)
        if hyp is not None:
            top = {
                "item_idx": int(hyp[0]),
                "slide_idx": int(hyp[1]),
                "marginal": float(hyp[2]) if len(hyp) > 2 else None,
            }
    displayed = None
    if _state.last_slide is not None:
        displayed = {
            "item_idx": _state.last_slide[0],
            "slide_idx": _state.last_slide[1],
            "source": _state.last_slide_source,
        }
    capture = getattr(session, "_capture", None) if session is not None else None
    from auto_presenter.audio.capture import FileCapture

    source = None
    if capture is not None:
        source = "file" if isinstance(capture, FileCapture) else "mic"
    return {
        "session": None if session is None else session.state,
        "error": None if session is None else session.last_error,
        "audio_source": source,
        "recording_id": inject.current["recording_id"],
        "wav_path": inject.current["wav_path"],
        "displayed": displayed,
        "belief": top,
        "ai_mode": None if director is None else director.mode,
        "ai_state": None if director is None else director.state,
        "blanking": _state.blanking,
        "has_matcher": matcher is not None,
    }
