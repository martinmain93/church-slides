"""The score follower's session wiring: references from the library, the
scorer on the transcription thread, chords and read-outs on the capture
thread, decisions onto the event loop.

Engages automatically for every setlist song that has a lead sheet
(`song_references`, kind `lead_sheet`) when the session transcribes with MLX
— the span scorer needs it — and the hidden `score_follower` tunable is on
(the kill switch). Every other song is followed by its library slides alone,
unless the hidden `score_follower_without_sheet` tunable is off. Anything else
runs exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from auto_presenter.decision.follower_engine import FollowerEngine
from auto_presenter.error_reports import said
from auto_presenter.follower.live import FlowEvidence, LiveFollowers, build_live_followers
from auto_presenter.matcher.types import MatcherDecision
from auto_presenter.protocol import SetlistContent
from auto_presenter.server_state import _state, deliver_decision, transcription_needed

log = logging.getLogger(__name__)

#: Pipeline health: unhealthy reports in a row before the scorer steps down,
#: and healthy reports in a row before it steps back up (reports every 2 s).
STEP_DOWN_AFTER = 2
STEP_UP_AFTER = 15


def load_lead_sheets(db_path: Path | str | None, song_ids: list[str]) -> dict[str, dict]:
    """song id -> stored lead-sheet score, read-only. Missing table or
    database: none (an older library, or a browser session)."""
    if not db_path or not song_ids or not Path(db_path).exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        log.warning("lead sheets: could not open %s: %s", db_path, exc)
        return {}
    out: dict[str, dict] = {}
    try:
        con.execute("PRAGMA query_only = ON")
        marks = ",".join("?" * len(song_ids))
        rows = con.execute(
            "SELECT song_id, data_json FROM song_references "
            f"WHERE kind = 'lead_sheet' AND song_id IN ({marks})",
            song_ids,
        ).fetchall()
        for song_id, data in rows:
            try:
                out[song_id] = json.loads(data)
            except json.JSONDecodeError:
                log.warning("lead sheet for %s is not JSON; ignored", song_id)
    except sqlite3.OperationalError as exc:
        log.info("lead sheets: none available (%s)", exc)
    finally:
        con.close()
    return out


def load_learned(content: SetlistContent) -> dict:
    """item -> what past services taught about its song (`learning.profile`);
    songs with nothing usable left out. Never fails the session."""
    from auto_presenter.learning.profile import aggregate
    from auto_presenter.learning.store import Store

    store = Store.beside(_state.library_db_path)
    if store is None:
        return {}
    out = {}
    for item in content.items:
        if item.spoken:
            continue  # a reading isn't a song with performances to learn from
        try:
            slides = [list(s) for s in item.slides]
            song = aggregate(store.load(item.song_id), slides)
        except Exception:
            log.exception("item %d: learned performances unreadable; ignored", item.item_idx)
            continue
        if song.used:
            out[item.item_idx] = song
            log.info(
                "item %d: %d past performances (weight %.1f), %d orders, %d timed lines",
                item.item_idx,
                song.used,
                song.n_eff,
                len(song.sequences),
                len(song.timing),
            )
    return out


#: One ONNX scorer per model directory: its sessions load once and serve the
#: follower and the openings watch, session after session.
_ONNX_SCORERS: dict[str, object] = {}


def onnx_scorer_dir() -> Path | None:
    """Where this computer's ONNX span-scorer model is, or would be once
    downloaded (`follower.scorer_models`)."""
    from auto_presenter.follower.scorer_models import scorer_dir

    return scorer_dir()


def machine_verdict_path() -> Path | None:
    """Where this computer's machine-check verdict is kept, beside this
    computer's own library: it's about the computer, whichever library is open."""
    db = _state.home_db_path
    return Path(db).parent / "machine-check.json" if db else None


def saved_verdict():
    """This computer's machine-check verdict (`asr.machine_check`), if one
    is saved and still current."""
    from auto_presenter.asr import machine_check

    path = machine_verdict_path()
    return machine_check.load(path) if path is not None else None


def cpu_path_enabled() -> bool:
    """Live following's CPU path runs (short-context transcription, the ONNX
    span scorer, the score follower): forced by the `score_follower_cpu`
    tunable (development, evals), or earned by this computer's machine
    check, with the scorer model on disk."""
    tunables = _state.tunables
    if tunables is not None and tunables.score_follower_cpu:
        return True
    from auto_presenter.follower import scorer_models

    v = saved_verdict()
    return v is not None and v.verdict == "full" and v.backend == "whisper" and (
        scorer_models.is_downloaded()
    )


def assist_tier() -> bool:
    """This computer's machine check found it too slow for live following
    but fast enough for Assist (`machine_check.ASSIST_LOAD_LIMIT`): Assist
    only, speech every `ASSIST_TRANSCRIBE_EVERY_S`, no span scorer and no
    score follower, and no "next slide is due" pulse (`server_assist`)."""
    tunables = _state.tunables
    if tunables is not None and tunables.assist_tier:
        return True
    if cpu_path_enabled():
        return False
    v = saved_verdict()
    return v is not None and v.verdict == "assist" and v.backend == "whisper"


def _whisper_of(backend: object) -> object:
    """The Whisper behind a session's speech engine. Qwen3-ASR streaming
    (`asr.qwen_backend`) transcribes, but the follower's scorer, the openings
    listener and the next-line check are built on Whisper small.en, which it
    carries as `whisper`. Every other backend is its own."""
    whisper = getattr(backend, "whisper", None)
    return whisper if whisper is not None else backend


def span_scorer_for(backend: object):
    """The session's span scorer: MLX on Apple silicon; ONNX Runtime elsewhere,
    when the CPU path is on (`cpu_path_enabled`) and its model is here; else None."""
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend
    from auto_presenter.follower.scorer import MLXSpanScorer

    backend = _whisper_of(backend)
    if isinstance(backend, MLXWhisperBackend):
        return MLXSpanScorer(backend._repo)
    if not cpu_path_enabled():
        return None
    from auto_presenter.follower.onnx_scorer import OnnxSpanScorer, available

    d = onnx_scorer_dir()
    if d is None or not available(d):
        return None
    if str(d) not in _ONNX_SCORERS:
        threads = int(os.environ.get("AUTO_PRESENTER_ONNX_THREADS", "0"))  # 0: default_threads()
        _ONNX_SCORERS[str(d)] = OnnxSpanScorer(d, threads=threads)
    return _ONNX_SCORERS[str(d)]


def build_session_followers(content: SetlistContent, backend: object) -> LiveFollowers | None:
    """Followers for this setlist, or None when the follower does not engage."""
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend
    from auto_presenter.eval.metrics import identical_slide_groups

    tunables = _state.tunables
    if tunables is not None and not tunables.score_follower:
        log.info("score follower off (tunable)")
        return None
    on_gpu = isinstance(_whisper_of(backend), MLXWhisperBackend)
    if not on_gpu and not cpu_path_enabled():
        log.info("score follower needs the MLX backend; matcher only this session")
        return None
    without_sheet = tunables is None or tunables.score_follower_without_sheet
    from auto_presenter.follower import reference as ref_mod

    if ref_mod.LOOPS_ENV is None:
        # one session at a time: the references are built here, below
        ref_mod.LOOPS = tunables is None or tunables.counted_loops
    sheets = load_lead_sheets(_state.library_db_path, [i.song_id for i in content.items])
    if not sheets and not without_sheet:
        return None
    learned = None
    if tunables is None or tunables.learned_use:
        learned = load_learned(content)
    identical = identical_slide_groups(content.items)
    # A passage read aloud is left to the matcher: the follower's reference
    # is built for singing (a word a beat), and the matcher is what was
    # measured on readings (docs/done/SERVICE_ITEMS.md phase 2).
    items = [
        (i.item_idx, [list(s) for s in i.slides], list(i.slide_sections), sheets.get(i.song_id))
        for i in content.items
        if not i.spoken
    ]
    followers = build_live_followers(
        items,
        identical=identical,
        # Apple silicon: MLX; elsewhere ONNX Runtime when its models are here,
        # or words and chords only
        scorer=(
            None if os.environ.get("AUTO_PRESENTER_NO_SPAN_SCORER") else span_scorer_for(backend)
        ),
        # chords come from the session's own analyser (`follower_frame_observer`)
        chord_analyser=None,
        without_sheet=without_sheet,
        learned=learned,
    )
    if not followers.songs:
        return None
    log.info(
        "score follower engaged for %d of %d songs%s",
        len(followers.songs),
        len(content.items),
        "" if on_gpu
        else f" (span scorer {followers.scorer.repo})" if followers.scorer is not None
        else " (words and chords, no span scorer)",
    )
    if tunables is not None and tunables.in_service_learning:
        start_service_learning(followers)
    if tunables is None or tunables.next_line_check:
        start_next_line_check(followers, backend)
    return followers


def start_next_line_check(followers: LiveFollowers, backend: object) -> None:
    """The next-line check (`follower.next_line`): Apple silicon with the
    small.en model, which its phone model was trained on, and the phone model
    on disk. Anywhere else the follower runs without it."""
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend
    from auto_presenter.follower import next_line

    backend = _whisper_of(backend)
    if (
        not isinstance(backend, MLXWhisperBackend)
        or backend._model_name != "mlx-whisper-small.en"
    ):
        log.info("next-line check needs MLX small.en; off this session")
        return
    head = next_line.load_head()
    if head is None:
        log.warning(
            "next-line model missing (%s); check off this session", next_line.model_dir()
        )
        return
    try:
        encode = next_line.mlx_encoder(backend._repo)
    except Exception:
        log.exception("next-line check could not start; off this session")
        return
    followers.band_playing = lambda: _state.band.playing
    followers.start_next_line(encode, head)
    log.info("next-line check on for %d songs", len(followers.songs))


def start_service_learning(followers: LiveFollowers) -> None:
    """Learn the library-followed songs' music during the service
    (`follower.learn`), on a thread that lives while these followers do."""
    import time

    from auto_presenter.follower.learn import ServiceLearner

    try:  # dev-only (packaging/dev_only.py): a release build doesn't carry it
        from auto_presenter.music.beat_this import beats_of, python_path, script_path
    except ImportError:
        log.info("in-service learning needs Beat This (not in this build); off this session")
        return

    songs = {
        item: sf.slides
        for item, sf in followers.songs.items()
        if sf.ref.source.startswith("library") and sf.slides
    }
    if not songs:
        return
    if python_path() is None or not script_path().exists():
        log.info("in-service learning needs Beat This (not installed); off this session")
        return
    learner = ServiceLearner(songs, aligner=followers.align_words, beat_tracker=beats_of)
    installed = {"seen": False, "at": time.monotonic()}

    def alive() -> bool:
        # built before the session installs it: wait for that, then live
        # while it stays installed
        if _state.followers is followers:
            installed["seen"] = True
            return True
        return not installed["seen"] and time.monotonic() - installed["at"] < 60.0

    followers.start_learning(learner, alive)
    log.info("in-service learning on for %d songs", len(songs))


def follower_frame_observer(loop: asyncio.AbstractEventLoop):
    """Called per captured frame (capture thread): the session's music.

    One analyser per AI session (chords and beats) feeds both the score
    follower's chords and the key/tempo change watch (`music.change_watch`),
    whose changes reach the matcher's song-change gate on the event loop.
    Nothing runs for a session without a setlist (no matcher), and the watch
    is off when the `music_change_watch` tunable is.
    """
    from auto_presenter.music.change_watch import KeyTempoWatch
    from auto_presenter.server_music import _new_analyser

    tunables = _state.tunables
    watch_on = tunables is None or tunables.music_change_watch
    if _state.matcher is None and _state.followers is None:
        return lambda _frame: None
    analyser = _new_analyser(True, True) if (watch_on or _state.followers is not None) else None
    watch = KeyTempoWatch() if watch_on else None

    def observe(frame) -> None:
        nonlocal analyser
        if not transcription_needed():
            # AI off: the chord analysis, the follower's read-outs and the
            # next-line check (Whisper's encoder four times a second) only
            # serve it, so none of them runs. Screen changes still reach the
            # engine (`on_override`), so it resumes from the slide on screen.
            return
        update = None
        if analyser is not None:
            started = time.perf_counter()
            try:
                update = analyser.feed(frame)
            except Exception:
                log.exception("session music analysis failed; carrying on without it")
                analyser = None
            _state.chord_cost.add((time.perf_counter() - started) * 1000, frame.duration_seconds)
        if update is not None:
            _state.band.observe(update.t_end, update.chord_probs)
        if watch is not None and update is not None:
            for change in watch.update(update.t_end, update.chord_probs, update.bpm):
                log.info(
                    "music change: %s %s -> %s at %.1f s",
                    change.kind,
                    change.before,
                    change.after,
                    change.t_seconds,
                )
                loop.call_soon_threadsafe(_note_music_change, change.t_seconds)
        followers = _state.followers
        if followers is None:
            return
        started = time.perf_counter()
        try:
            decision = followers.on_frame(frame, update)
        except Exception:
            log.exception("score follower read-out failed; display left alone")
            return
        finally:
            _state.follower_cost.add((time.perf_counter() - started) * 1000, frame.duration_seconds)
        if decision is not None:
            asyncio.run_coroutine_threadsafe(apply_follower_decision(decision), loop)
        flow = followers.take_flow()
        if flow is not None:
            asyncio.run_coroutine_threadsafe(observe_follower_flow(flow), loop)

    return observe


def following_current_song() -> bool:
    """The song on screen is followed (a lead sheet, or the library slides
    alone): continuous mode's position comes from the follower
    (`observe_follower_flow`), not from the word tracker's posterior.

    Library-only followers were held back from scrolling for a day (3cfca0b):
    replayed on the 8 live traces (research/spikes/music_only/flow_replay.py)
    their column had the sung line in view 56.7% of the time against the
    word tracker's 61.9%. Three causes, all fixed: the column followed the
    belief's peak rather than the slide the follower shows (cut to the first
    line and back through a closing vamp, `live.FLOW_ON_SHOWN_SLIDE`); the
    order prior sent the last slide back to the first (`reference.
    LAST_REPEAT`); and the peak of a wide belief runs ahead of the band, which
    hides the sung line twice as fast as running behind (`live.
    FLOW_QUANTILE`). Now: library 67.1% (76.4% at 10 lines), word tracker
    61.9% (70.2%), lead sheet 76.0% (80.7%); jumps 64 / 64 / 62."""
    followers = _state.followers
    return followers is not None and followers.active is not None


async def observe_follower_flow(flow: FlowEvidence) -> None:
    """Continuous mode inside a followed song: the score follower's belief
    becomes the column's track, through the same `FlowFollower` the word
    tracker feeds (deadbands, no scrolling back on a whim, a cut across a
    song change, a stop at the slide's end while nobody sings)."""
    from auto_presenter.decision.flow import FlowFollower
    from auto_presenter.protocol import FlowPositionMessage
    from auto_presenter.server_state import _broadcast, _indexed_setlist

    matcher, director = _state.matcher, _state.director
    followers = _state.followers
    if matcher is None or followers is None or followers.current != flow.item_idx:
        return  # the song changed while this was on its way
    setlist = _indexed_setlist(matcher)
    if setlist is None:
        return
    follower = _state.flow_follower
    if follower is None or follower.setlist is not setlist:
        follower = _state.flow_follower = FlowFollower(setlist=setlist)
    if getattr(director, "state", None) == "following_human":
        follower.track = None  # the operator has the screen (see server._observe_flow)
        return
    item = setlist.item(flow.item_idx)
    if item is None:
        return
    # the follower's word count per slide -> the display's (tokenised alike
    # in almost every slide; proportional where they differ)
    alpha: dict[tuple[int, int, int, int], float] = {}
    for (si, j), mass in flow.mass.items():
        if si >= len(item.slides):
            continue
        n_shown, n_mine = len(item.slides[si].words), flow.words.get(si, 0)
        if n_shown == 0 or n_mine == 0:
            continue
        w = min(n_shown - 1, round(j * n_shown / n_mine))
        key = (flow.item_idx, si, w, 0)
        alpha[key] = alpha.get(key, 0.0) + mass
    track = follower.update(alpha, t_observed=flow.t, t_now=flow.t, rate_wps=flow.rate_wps)
    if track is None:
        return
    await _broadcast(
        FlowPositionMessage(
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


def _note_music_change(t_seconds: float) -> None:
    """On the loop: tell the matcher (its song-change gate) the music changed."""
    note = getattr(_state.matcher, "note_music_change", None)
    if note is not None:
        note(t_seconds)


async def apply_follower_decision(decision: MatcherDecision) -> None:
    """A follower slide change reaches the screen the way any AI decision does."""
    matcher = _state.matcher
    director = _state.director
    if not isinstance(matcher, FollowerEngine) or director is None:
        return
    held = _state.blank_when_idle and _state.blank_on_talking and _state.singing.off_book
    # While the room sounds like talk, a move needs the target slide's words,
    # from the follower as from the matcher (`decision.plan_gate`).
    gate = matcher.scheduler.plan_gate
    if gate is not None and matcher._screen is not None and not gate.allows(
        matcher.scheduler.setlist, matcher._screen,
        (decision.item_idx, decision.slide_idx), decision.t_seconds,
        matcher.scheduler.talking_score, _state.band.playing,
    ):
        held = True
    # Same rule as a transcript decision: while nobody is singing these words,
    # hold the display (see `server._make_transcript_handler`). Held or stale,
    # the follower is told what is really up so it re-issues the change later.
    if held or not matcher.accept_follower_decision(decision):
        screen = matcher._screen
        if _state.followers is not None and screen is not None:
            _state.followers.note_shown(screen[0], screen[1])
        return
    deliver_decision(decision)


def observe_health(healthy: bool) -> None:
    """Step the scorer down when the pipeline falls behind, back up when it
    has been healthy for a while."""
    followers = _state.followers
    if followers is None:
        return
    if healthy:
        _state.follower_unhealthy = 0
        _state.follower_healthy += 1
        if followers.load_level > 0 and _state.follower_healthy >= STEP_UP_AFTER:
            followers.set_load(followers.load_level - 1)
            _state.follower_healthy = 0
    else:
        _state.follower_healthy = 0
        _state.follower_unhealthy += 1
        if followers.load_level < 2 and _state.follower_unhealthy >= STEP_DOWN_AFTER:
            followers.set_load(followers.load_level + 1)
            _state.follower_unhealthy = 0


# ---------------------------------------------------------------- song openings

#: With no band playing (`music.band`), a song change needs the same song
#: heard on two of the watch's passes within this many audio seconds. A band
#: starting a song keeps singing its line, so the next pass (2 s on) hears it
#: again — on the goldens every held real start was re-heard 2-3 s later —
#: while a sermon quotes a lyric once and moves on ("...to every creature",
#: "his banner over you": each heard once, 2026-11-05). This replaced a
#: talking-score bar: that score reads "the words don't match the song on
#: screen", so it is high exactly when a band starts another song.
OPENINGS_CONFIRM_S = 6.0


def build_session_openings(content: SetlistContent, backend: object):
    """The setlist's opening-line watch (`follower.openings`), or None.

    MLX only (the span scorer), at least two songs, and the hidden
    `song_openings_watch` tunable on. Scores every span once on silence and
    noise for its prior: two scorer passes, at session start."""
    from auto_presenter.follower.openings import OpeningsWatch

    tunables = _state.tunables
    if tunables is not None and not tunables.song_openings_watch:
        return None
    scorer = span_scorer_for(backend)
    if scorer is None:
        return None
    try:
        watch = OpeningsWatch.build(
            # not readings: the operator puts one up; hearing its first words
            # in a sermon must not change the screen
            [(i.item_idx, [list(s) for s in i.slides]) for i in content.items if not i.spoken],
            scorer,
        )
        if watch is None:
            return None
        if tunables is not None:
            watch.mid_song_nudge = tunables.openings_mid_song_nudge
        watch.warm_up()
    except Exception:
        log.exception("opening-line watch failed to start; song changes by the matcher only")
        return None
    if not watch.prior:
        return None
    log.info("listening for %d songs' opening lines", len(watch.songs))
    return watch


#: After an AI song change, the opening-line watch keeps scoring the new song
#: (as the current song) every pass. If its mean gain over passes
#: `SONG_PROBATION_SKIP_S`..`SONG_PROBATION_S` after the change stays under the
#: tunable `song_probation_min_gain` (1.2), the song was not really being sung:
#: blank until it is clearly heard (`SONG_HEARD_GAIN`) or the screen changes
#: song. The first pass is skipped: it holds the very line that made the change.
#: Measured 2026-11-06: after the 7 real song changes on the multi-song goldens
#: the mean was 1.77-5.0; after false changes on talk 0.17 and 0.75.
SONG_PROBATION_S = 16.0
SONG_PROBATION_SKIP_S = 2.5
#: Fewer passes than this (the pipeline was behind) give no verdict.
SONG_PROBATION_MIN_PASSES = 3
SONG_HEARD_GAIN = 2.5


def note_ai_move(item_idx: int, t_seconds: float) -> bool:
    """An AI move reached the screen. A move into another song starts that
    song's probation; returns True when that lifted a blank (report it)."""
    prev, _state.last_ai_item = _state.last_ai_item, item_idx
    if prev is None or prev == item_idx:
        return False
    tunables = _state.tunables
    if tunables is not None and tunables.song_probation_min_gain <= 0:
        _state.song_probation = None
    else:
        _state.song_probation = (item_idx, t_seconds, [])
    lifted, _state.song_unconfirmed = _state.song_unconfirmed, False
    return lifted


def note_operator_move(item_idx: int) -> bool:
    """A person chose the screen: never blank their song. True when a blank lifted."""
    _state.last_ai_item = item_idx
    _state.song_probation = None
    lifted, _state.song_unconfirmed = _state.song_unconfirmed, False
    return lifted


def probation_step(current: int | None, gain: float, t: float) -> bool | None:
    """One opening-watch pass: True to blank, False to lift a blank, None for no change."""
    if _state.song_unconfirmed:
        if gain >= SONG_HEARD_GAIN:
            _state.song_unconfirmed = False
            return False
        return None
    p = _state.song_probation
    if p is None:
        return None
    item, t0, gains = p
    if current != item:
        _state.song_probation = None
        return None
    if SONG_PROBATION_SKIP_S < t - t0 <= SONG_PROBATION_S:
        gains.append(gain)
    if t - t0 <= SONG_PROBATION_S:
        return None
    _state.song_probation = None
    tunables = _state.tunables
    bar = tunables.song_probation_min_gain if tunables is not None else 1.2
    if len(gains) < SONG_PROBATION_MIN_PASSES or sum(gains) / len(gains) >= bar:
        return None
    log.info(
        "song %d not heard since the change (mean gain %.2f over %d passes); blanking",
        item, sum(gains) / len(gains), len(gains),
    )
    _state.song_unconfirmed = True
    return True


def _screen_item() -> int | None:
    matcher = _state.matcher
    if isinstance(matcher, FollowerEngine) and matcher._screen is not None:
        return matcher._screen[0]
    return getattr(matcher, "current_item_idx", None)


def session_side_pass(loop: asyncio.AbstractEventLoop):
    """The scorer's slot on the ASR thread (before each Whisper pass): the
    score follower's spans, then every few seconds the other songs' openings."""
    import numpy as np

    from auto_presenter.audio.types import SAMPLE_RATE
    from auto_presenter.follower.scorer import WINDOW_S

    def side(buffer, t_end: float) -> None:
        followers = _state.followers
        watch = _state.openings
        current = _screen_item()
        want = watch.wanted(current) if watch is not None and watch.due(t_end) else None
        scores = followers.side_pass(buffer, t_end, extra=want) if followers is not None else None
        if not want:
            return
        if scores is None and followers is not None and followers.load_level > 0:
            return  # the pipeline is behind: listen only when the follower scores
        if scores is None and (not buffer or t_end - buffer[0].t_start < WINDOW_S - 0.5):
            return
        try:
            if scores is not None:
                heard = watch.hear(scores, t_end, current)
            else:
                audio = np.concatenate([f.samples for f in buffer]).astype(np.float32)[
                    -int(WINDOW_S * SAMPLE_RATE) :
                ]
                heard = watch.listen(audio, t_end, current)
        except Exception:
            log.exception("opening-line watch failed; off for this session")
            _state.openings = None
            return
        if followers is not None and watch.last is not None:
            t, cur, cur_best, best_of = watch.last
            followers._log(
                e="openings",
                t=t,
                item=cur,
                cur=round(cur_best, 2),
                best={str(k): [round(g, 2), sp] for k, (g, sp) in best_of.items()},
                talking=round(float(_state.singing.score), 2),
            )
        if watch.last is not None:
            t_pass, cur, cur_best, _ = watch.last
            if probation_step(cur, cur_best, t_pass) is not None:
                from auto_presenter.server import refresh_blanking

                asyncio.run_coroutine_threadsafe(refresh_blanking(), loop)
        if heard is not None:
            asyncio.run_coroutine_threadsafe(apply_song_heard(heard), loop)

    return side


async def apply_song_heard(heard) -> None:
    """Another setlist song's line was heard: show it, and follow from there."""
    matcher = _state.matcher
    director = _state.director
    if matcher is None or director is None or director.mode == "off":
        return
    if _screen_item() == heard.item_idx:
        return
    tunables = _state.tunables
    confirm = tunables is None or tunables.openings_confirm_without_band
    if confirm and not _state.band.playing:
        pending = _state.openings_pending
        again = (
            pending is not None
            and pending[0] == heard.item_idx
            and 0.0 < heard.t_seconds - pending[1] <= OPENINGS_CONFIRM_S
        )
        if not again:
            _state.openings_pending = (heard.item_idx, heard.t_seconds)
            log.info(
                "song %d heard («%s», gain %.1f) with no band playing; waiting to hear it again",
                heard.item_idx,
                said(heard.span),
                heard.gain,
            )
            return
    _state.openings_pending = None
    log.info(
        # «…»: the line matched to what was sung; error reports strip it.
        "song change: heard «%s» (gain %.1f, current song %.1f) -> item %d slide %d",
        said(heard.span),
        heard.gain,
        heard.current_best_gain,
        heard.item_idx,
        heard.slide_idx,
    )
    matcher.commit_external(heard.item_idx, heard.slide_idx)
    if _state.followers is not None:
        # heard from the band's words: they are already inside that slide
        _state.followers.enter(heard.item_idx, heard.slide_idx, mid_slide=True)
    deliver_decision(
        MatcherDecision(
            item_idx=heard.item_idx,
            slide_idx=heard.slide_idx,
            t_seconds=heard.t_seconds,
            confidence=1.0,
        )
    )
