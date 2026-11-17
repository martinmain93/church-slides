"""The learning observer's session wiring (`auto_presenter.learning`).

Every hook is a guarded one-liner from `server.py`: learning watches the
session and may never break it. On by default — it only collects (the
hidden `learned_collect` tunable is the kill switch); nothing it learns
reaches the screen until a reference built from it is handed to the follower.
"""

from __future__ import annotations

import itertools
import logging

from auto_presenter.follower.reference import LEARNED_CAP, LEARNED_PRIOR_COUNT
from auto_presenter.learning import profile
from auto_presenter.learning.session import LearningSession
from auto_presenter.learning.store import Store
from auto_presenter.learning.summary import PerformanceSummary
from auto_presenter.protocol import (
    GetLearningStateResponse,
    LearnedPassView,
    LearnedSongView,
    LearnedTransitionView,
    OperatorMoveView,
    PerformanceView,
    SetlistContent,
    SlideProb,
    StartAudioSessionRequest,
    TransitionEvidenceView,
)
from auto_presenter.server_state import _state

log = logging.getLogger(__name__)


def start_learning(msg: StartAudioSessionRequest, injected: bool) -> None:
    """A learning observer for this session, when it has a setlist."""
    _state.learning = None
    tunables = msg.tunables or _state.tunables
    if tunables is not None and not tunables.learned_collect:
        return
    if msg.setlist_content is None or not msg.setlist_content.items:
        return
    try:
        start_item = msg.start_item_idx if msg.start_item_idx is not None else 0
        session = LearningSession(
            msg.setlist_content,
            Store.beside(_state.library_db_path),
            # Injected audio is a replay of something already heard, or a test:
            # kept for the debug panel, never learned from.
            provenance="injected" if injected else "live",
            start=(start_item, msg.start_slide_idx),
        )
        session.start()
    except Exception:
        log.exception("learning observer failed to start; the session runs without it")
        return
    _state.learning = session


def stop_learning(session: LearningSession | None = None) -> None:
    """Final rows for a session's songs (blocking: call off the event loop).

    Without `session`, the current one. A given session is detached only if
    it is still the current one: a newer session's observer is left alone."""
    if session is None:
        session = _state.learning
    if session is None:
        return
    if _state.learning is session:
        _state.learning = None
    try:
        session.stop()
    except Exception:
        log.exception("learning observer failed to finish")


def _guarded(name: str, fn, *args) -> None:
    session = _state.learning
    if session is None:
        return
    try:
        fn(session, *args)
    except Exception:
        log.exception("learning %s failed; observer off for this session", name)
        _state.learning = None


def learning_frame(frame) -> None:
    session = _state.learning
    if session is not None:
        session.on_frame_time(frame.t_end)


def learning_words(words) -> None:
    _guarded("words", LearningSession.on_words, words)


def learning_slide(item_idx: int, slide_idx: int | None, source: str) -> None:
    _guarded("slide", LearningSession.on_slide_shown, item_idx, slide_idx, source)


def learning_content(content: SetlistContent) -> None:
    _guarded("content", LearningSession.update_content, content)


# ---------------------------------------------------------------- debug panel

#: Past performances listed per song (newest first).
PANEL_PERFORMANCES = 20


def learning_state() -> GetLearningStateResponse:
    """The debug panel's view (reads the store: call off the event loop)."""
    session = _state.learning
    if session is not None:
        items = session.items()
    else:
        start = _state.last_start_audio
        items = list(start.setlist_content.items) if start and start.setlist_content else []
    store = Store.beside(_state.library_db_path)
    followers = _state.followers
    songs = []
    for item in items:
        slides = [list(s) for s in item.slides]
        rows = store.load(item.song_id) if store is not None else []
        song = profile.aggregate(rows, slides)
        live, heard, ms = session.performance(item.item_idx) if session else (None, 0, 0.0)
        sf = followers.songs.get(item.item_idx) if followers is not None else None
        songs.append(
            LearnedSongView(
                item_idx=item.item_idx,
                song_id=item.song_id,
                title=getattr(item, "title", "") or "",
                follower_source=sf.ref.source if sf is not None else None,
                live=_view(live) if live is not None else None,
                live_heard_words=heard,
                lookback_ms=round(ms, 1),
                # this session's own row is the "live" one, not a past service
                performances=[_view(r) for r in rows if live is None or r.id != live.id][
                    :PANEL_PERFORMANCES
                ],
                used=song.used,
                n_eff=round(song.n_eff, 2),
                seconds_per_word=song.seconds_per_word,
                timed_lines=len(song.timing),
                order=_order(slides, song, sf.ref if sf is not None else None),
                slide_text=[" ".join(sw) for sw in slides],
            )
        )
    return GetLearningStateResponse(
        collecting=session is not None,
        provenance=session.provenance if session else None,
        audio_now=round(session.audio_now, 1) if session else 0.0,
        passes_run=session.passes_run if session else 0,
        store=str(store.path) if store is not None else None,
        songs=songs,
    )


def delete_performance(performance_id: str) -> None:
    store = Store.beside(_state.library_db_path)
    if store is not None:
        store.delete(performance_id)


def _view(s: PerformanceSummary) -> PerformanceView:
    return PerformanceView(
        id=s.id,
        started_at_ms=s.started_at_ms,
        provenance=s.provenance,
        led_by=s.led_by,
        weight=s.weight,
        agreement=s.agreement,
        mean_confidence=s.notes.get("mean_confidence"),
        heard_words=s.heard_words,
        final=s.final,
        passes=[
            LearnedPassView(slide=p.slide, t_start=p.t_start, confidence=p.confidence)
            for p in s.passes
        ],
        operator=[OperatorMoveView(t=t, slide=sl, source=src) for t, sl, src in s.operator],
    )


def _order(
    slides: list[list[str]], song: profile.LearnedSong, ref=None
) -> list[LearnedTransitionView]:
    """Per sung line: library order's next line, the services' next lines and
    the evidence for each, and the follower's resulting next-line odds.

    `ref` is the follower's own reference when it follows this song (its
    order prior is what decides); otherwise one is built from the library."""
    sung = [i for i, sw in enumerate(slides) if sw]
    counts: dict[int, dict[int, float]] = {}
    for seq, w in song.sequences:
        for a, b in itertools.pairwise(seq):
            if a != b:
                counts.setdefault(a, {})[b] = counts.setdefault(a, {}).get(b, 0.0) + w
    by_from: dict[int, list] = {}
    for ev in song.evidence:
        by_from.setdefault(ev.from_slide, []).append(ev)
    live_ref, base_ref = _refs(slides, song, ref)
    out = []
    for k, si in enumerate(sung):
        row = counts.get(si, {})
        n = sum(row.values())
        best = max(row, key=row.__getitem__) if row else None
        out.append(
            LearnedTransitionView(
                from_slide=si,
                library_next=sung[k + 1] if k + 1 < len(sung) else None,
                learned_next=best,
                count=round(row.get(best, 0.0), 2) if best is not None else 0.0,
                share=round(min(LEARNED_CAP, n / (n + LEARNED_PRIOR_COUNT)), 3) if n else 0.0,
                successors=[
                    SlideProb(slide=b, p=round(c, 3))
                    for b, c in sorted(row.items(), key=lambda x: -x[1])
                ],
                evidence=[
                    TransitionEvidenceView(
                        to_slide=ev.to_slide,
                        performance_id=ev.performance_id,
                        started_at_ms=ev.started_at_ms,
                        led_by=ev.led_by,
                        weight=ev.weight,
                        t=round(ev.t, 1),
                        conf_from=round(ev.conf_from, 3),
                        conf_to=round(ev.conf_to, 3),
                        operator_agrees=ev.operator_agrees,
                    )
                    for ev in sorted(by_from.get(si, []), key=lambda e: -e.started_at_ms)
                ],
                prior=_next_odds(live_ref, si),
                base_prior=_next_odds(base_ref, si),
            )
        )
    return out


def _refs(slides, song, ref):
    """(the reference as run, the same without learning), or Nones."""
    from dataclasses import replace

    from auto_presenter.follower.reference import SegmentReference
    from auto_presenter.follower.spec import library_spec

    try:
        if ref is not None:
            spec = ref.spec
            base = replace(spec, learned=[])
        else:
            base = library_spec(slides)
            spec = profile.learned_spec(base, song)
        return SegmentReference(spec, slides), SegmentReference(base, slides)
    except Exception:
        log.exception("learning panel: reference could not be built")
        return None, None


def _next_odds(ref, slide: int, top: int = 4) -> list[SlideProb]:
    """The order prior's row for `slide`: where the follower expects it to go."""
    if ref is None or slide not in ref.seg_slides[1:]:
        return []
    seg = ref.seg_slides.index(slide, 1)
    # by slide: a loop's further passes (counted loops) are the same slide again
    by_slide: dict[int, float] = {}
    for g, p in enumerate(ref.succ[seg]):
        if p > 0:
            sl = int(ref.seg_slides[g])
            by_slide[sl] = by_slide.get(sl, 0.0) + float(p)
    order = sorted(by_slide, key=by_slide.__getitem__, reverse=True)[:top]
    return [SlideProb(slide=sl, p=round(by_slide[sl], 3)) for sl in order if by_slide[sl] > 0.005]
