"""What one per-frame stage costs, measured where it runs.

The music follower and the chord analyser run on the capture thread, once
per audio frame, beside the speech engine. On Apple silicon the speech
engine is on the GPU and they have the processor to themselves; on an Intel
Mac or a Windows PC all three share it, so what each costs decides whether
they fit. Pipeline health reports two numbers per stage, per window:

* **ms of work per second of audio**: its share of one core (100 = 10%);
* **p95 ms of one frame**: the occasional slow frame (a follower read-out,
  a key re-check), which delays every frame queued behind it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class StageCost:
    _work_ms: float = 0.0
    _audio_s: float = 0.0
    _frames: list[float] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, ms: float, audio_s: float) -> None:
        """One frame: `ms` of work for `audio_s` seconds of audio (capture thread)."""
        with self._lock:
            self._work_ms += ms
            self._audio_s += audio_s
            self._frames.append(ms)

    def take(self) -> tuple[float, float] | None:
        """(ms per audio second, p95 frame ms) since the last take, and start
        a new window; None when nothing ran (event loop)."""
        with self._lock:
            work, audio, frames = self._work_ms, self._audio_s, self._frames
            self._work_ms, self._audio_s, self._frames = 0.0, 0.0, []
        if not frames or audio <= 0:
            return None
        frames.sort()
        return work / audio, frames[min(len(frames) - 1, int(len(frames) * 0.95))]
