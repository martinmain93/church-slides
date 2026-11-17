"""Live-windowed transcripts for the fusion test, with their delivery times.

    uv run --project sidecar python research/spikes/music_only/transcribe_live.py OUT.json REC_DIR [REC_DIR ...]

The app's own path: `SlidingWindowWrapper` (production defaults) over
`MLXWhisperBackend` small.en, fed each recording's audio as fast as it will
go. The wrapper paces itself on measured pass times, so the cadence is the
live one. Every final event is stored with `available_at` — the audio time
at which the wrapper would have handed it over — so decisions can be scored
on the clock the room sees, not the clock the words were sung on.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from auto_presenter.asr.mlx_backend import MLXWhisperBackend
from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.eval.replay import Replayer, load_recording


def main() -> int:
    out = Path(sys.argv[1])
    cache = json.loads(out.read_text()) if out.exists() else {}
    backend = MLXWhisperBackend("small.en")
    for arg in sys.argv[2:]:
        rec = load_recording(Path(arg))
        if rec.id in cache:
            print(f"{rec.id}: cached")
            continue
        avail: dict[int, float] = {}

        def observe(event, available_at, avail=avail):
            avail[id(event)] = available_at

        wrapper = SlidingWindowWrapper(backend, emission_observer=observe)
        t0 = time.time()
        events = []
        for e in wrapper.transcribe(Replayer(rec, realtime=False).audio_frames()):
            if e.is_final:
                events.append({
                    "t_end": e.t_end,
                    "segment_id": e.segment_id,
                    "available_at": avail.get(id(e)),
                    "words": [{"text": w.text, "t_start": w.t_start, "t_end": w.t_end} for w in e.words],
                })
        cache[rec.id] = events
        out.write_text(json.dumps(cache))
        print(f"{rec.id}: {len(events)} events in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
