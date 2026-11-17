"""Continuous-mode positioning: keep the sung words near an anchor line.

Slide mode turns the tracker's belief into a *switch* — a discrete, visible,
one-way decision whose timing the lead-time scheduler exists to get right.
Continuous mode shows each song as one column of lyrics and only has to keep
approximately the right words in view, so it reads the belief as a
**position** and hands the screen a track to follow.

The track is deliberately a formula rather than a stream of positions, because
updates arrive every 1-3 s and the screen must move smoothly in between:

    word(t) = min(limit, word0 + rate * dt + correction * min(1, dt / settle))
    dt      = t - t0

`rate` is the singing rate; `correction` is how far the screen is off, paid
back over `settle` seconds so a late update nudges the column instead of
snapping it. `limit` is the end of the slide the belief is on: with no new
evidence (an instrumental, a drought) the column stops at the gap between
slides rather than scrolling into lyrics nobody has started. A passage read
aloud has no gaps to wait at; its limit is instead a little past the
last word heard (`spoken_lead_seconds`, `spoken_coast_seconds`). A `jump` marks a
discontinuity — a different song, or an error bigger than a screen — which the
display should fade across rather than scroll through.

Positions are **global word indices** into one item's resolved slide
sequence: slide `k` starts at the summed word counts of slides `0..k-1`. The
frontend tokenizes each displayed line with the parity-checked `tokenize`, so
the same index lands on the same word on screen.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from auto_presenter.index.types import IndexedSetlist


@dataclass(frozen=True)
class FlowTrack:
    """What the screen should follow until the next update."""

    item_idx: int
    word0: float
    rate_wps: float
    correction: float
    settle_seconds: float
    limit: float
    #: Clock the track is anchored on. The engine runs on the audio timeline;
    #: the frontend re-anchors on its own clock at receipt.
    t0: float
    jump: bool
    confidence: float

    def at(self, t: float) -> float:
        dt = max(0.0, t - self.t0)
        settle = min(1.0, dt / self.settle_seconds) if self.settle_seconds > 0 else 1.0
        return min(self.limit, self.word0 + self.rate_wps * dt + self.correction * settle)


@dataclass(frozen=True)
class FlowConfig:
    #: Below this, the belief is not trusted to move the screen at all; the
    #: current track keeps running up to its `limit`. Matches
    #: `ForwardTracker.lost_below`, the calibrated "lost" line.
    min_confidence: float = 0.35
    #: Words either side of the posterior mode counted as "the same place".
    #: Wide enough to pool a line's worth of word-level uncertainty, narrow
    #: enough not to average two copies of a repeated chorus.
    mode_window: int = 8
    #: Errors smaller than this are left alone: a column that twitches on
    #: every update is harder to read than one a word off.
    deadband_words: float = 2.0
    #: Moving *back* costs the reader their place, so it needs a real error
    #: and a confident belief.
    back_words: float = 8.0
    back_confidence: float = 0.6
    #: An error this large is a different part of the song; scrolling through
    #: it would flash a screen of lyrics past the congregation.
    jump_words: float = 40.0
    settle_seconds: float = 2.0
    #: A passage read aloud (`IndexedItem.spoken`) has no instrumentals to
    #: wait out, so its column doesn't stop at each slide's end — that stop
    #: made it move a verse at a time. It runs on at the reading rate, but
    #: guesses ahead of the last word heard by at most `spoken_lead_seconds`
    #: (not the whole ~3 s recognition delay), and keeps moving without new
    #: words for at most `spoken_coast_seconds` — so a reader who stops to
    #: comment finds the screen about where they stopped, not verses on.
    spoken_lead_seconds: float = 1.0
    spoken_coast_seconds: float = 2.0
    #: Reading tracks at 0.9+ while the passage is being read; it falls as
    #: soon as the words stop matching (an aside), while the mode still
    #: creeps on. Below this a reading's track is left to coast out.
    spoken_min_confidence: float = 0.8
    #: Singing rate from words the ASR delivered over this span.
    rate_window_seconds: float = 8.0
    max_rate_wps: float = 4.0


@dataclass
class FlowFollower:
    """Turns successive forward-filter posteriors into a `FlowTrack`."""

    setlist: IndexedSetlist
    config: FlowConfig = field(default_factory=FlowConfig)
    track: FlowTrack | None = field(default=None, init=False)
    _word_ends: deque[float] = field(default_factory=deque, init=False)

    def slide_offsets(self, item_idx: int) -> list[int]:
        """Global word index at which each slide starts, plus the total."""
        item = self.setlist.item(item_idx)
        out = [0]
        for slide in item.slides if item else ():
            out.append(out[-1] + len(slide.words))
        return out

    def observe_words(self, word_ends: list[float]) -> None:
        self._word_ends.extend(word_ends)

    def rate_at(self, t: float) -> float:
        span = self.config.rate_window_seconds
        while self._word_ends and self._word_ends[0] <= t - span:
            self._word_ends.popleft()
        n = sum(1 for w in self._word_ends if w <= t)
        return min(self.config.max_rate_wps, n / span)

    def update(
        self,
        alpha: dict[tuple[int, int, int, int], float],
        *,
        t_observed: float,
        t_now: float,
        rate_wps: float | None = None,
    ) -> FlowTrack | None:
        """Fold one posterior in; return the new track, or None if unchanged.

        `t_observed` is when the last matched word was sung (the posterior's
        own time); `t_now` is when it reached us. The gap is the delivery lag,
        which the track makes up by extrapolating at the singing rate.
        `rate_wps`, when the caller knows the singing rate better than the
        recent words do (the score follower's tempo), replaces it.
        """
        estimate = self._estimate(alpha)
        if estimate is None:
            return None
        item_idx, word, confidence, slide_end = estimate
        cfg = self.config
        rate = self.rate_at(t_observed) if rate_wps is None else rate_wps
        lag = max(0.0, t_now - t_observed)
        item = self.setlist.item(item_idx)
        spoken = item is not None and item.spoken
        if spoken:
            end = float(self.slide_offsets(item_idx)[-1])
            target = min(end, word + rate * min(lag, cfg.spoken_lead_seconds))
            slide_end = min(end, target + rate * cfg.spoken_coast_seconds)
        else:
            target = min(slide_end, word + rate * lag)

        current = self.track
        if confidence < (cfg.spoken_min_confidence if spoken else cfg.min_confidence):
            return None

        def new(
            word0: float,
            correction: float = 0.0,
            *,
            jump: bool = False,
            speed: float = rate,
            limit: float = slide_end,
        ) -> FlowTrack:
            self.track = FlowTrack(
                item_idx=item_idx,
                word0=word0,
                rate_wps=speed,
                correction=correction,
                settle_seconds=cfg.settle_seconds,
                limit=limit,
                t0=t_now,
                jump=jump,
                confidence=confidence,
            )
            return self.track

        if current is None or current.item_idx != item_idx:
            return new(target, jump=True)

        shown = current.at(t_now)
        err = target - shown
        if abs(err) >= cfg.jump_words:
            return new(target, jump=True)
        if err <= -cfg.deadband_words:
            if err <= -cfg.back_words and confidence >= cfg.back_confidence:
                # Confidently well behind: ease back. The limit must not sit
                # behind the screen, or the clamp would snap it there instead.
                return new(shown, err, limit=max(slide_end, shown))
            if spoken:
                # A reading slows rather than stops, so the column doesn't
                # stutter whenever it gets a word or two ahead.
                return new(shown, speed=max(0.0, rate + err / cfg.settle_seconds),
                           limit=max(slide_end, shown))
            # The screen is ahead of the singing, but not by enough to justify
            # moving back: stop here until the singing catches up.
            if current.rate_wps == 0.0 and current.limit == shown:
                return None
            return new(shown, speed=0.0, limit=shown)
        if abs(err) < cfg.deadband_words:
            err = 0.0
        return new(shown, err, limit=max(slide_end, shown))

    def _estimate(
        self, alpha: dict[tuple[int, int, int, int], float]
    ) -> tuple[int, float, float, float] | None:
        """`(item, word, confidence, end_of_slide)` at the posterior's mode."""
        if not alpha:
            return None
        by_item: dict[int, float] = {}
        for (item_idx, _s, _w, _d), mass in alpha.items():
            by_item[item_idx] = by_item.get(item_idx, 0.0) + mass
        total = sum(by_item.values())
        if total <= 0:
            return None
        item_idx = max(by_item, key=by_item.__getitem__)
        offsets = self.slide_offsets(item_idx)

        by_word: dict[int, float] = {}
        for (i, slide_idx, word_idx, _d), mass in alpha.items():
            if i != item_idx or slide_idx >= len(offsets) - 1:
                continue
            g = offsets[slide_idx] + word_idx
            by_word[g] = by_word.get(g, 0.0) + mass

        w = self.config.mode_window
        # Mode of the smoothed posterior, then its local mean — not the global
        # mean, which would land between two copies of a repeated chorus.
        mode = max(
            by_word,
            key=lambda g: sum(by_word.get(g + k, 0.0) for k in range(-3, 4)),
        )
        near = {g: m for g, m in by_word.items() if abs(g - mode) <= w}
        near_mass = sum(near.values())
        if near_mass <= 0:
            return None
        word = sum(g * m for g, m in near.items()) / near_mass

        slide_end = float(offsets[-1])
        for k in range(len(offsets) - 1):
            if offsets[k] <= mode < offsets[k + 1]:
                slide_end = float(offsets[k + 1])
                break
        return item_idx, word, near_mass / total, slide_end
