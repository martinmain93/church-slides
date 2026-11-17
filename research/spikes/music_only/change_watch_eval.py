"""The shipped key/tempo change watch, run as live over the goldens.

    uv run --project sidecar python research/spikes/music_only/change_watch_eval.py

Each recording's audio goes frame by frame through the session analyser
(`server_music._new_analyser(True, True)`: BTC chords, causal beat tracker)
and `music.change_watch.KeyTempoWatch`, exactly as `follower_frame_observer`
does. Reported per recording: changes near a real song change (-20..+45 s)
and changes elsewhere (false alarms: what could lower the song-change bar
mid-song).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr  # noqa: E402
from auto_presenter.audio.capture import FileCapture  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.music.change_watch import KeyTempoWatch  # noqa: E402
from auto_presenter.server_music import _new_analyser  # noqa: E402


def main() -> int:
    names = sys.argv[1:] or list(mr.CASES)
    total_min, false_n = 0.0, 0
    for name in names:
        rdir = Path(mr.CASES[name][0])
        rec = load_recording(rdir)
        gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
        song_changes = [g.t_seconds for i, g in enumerate(gt) if i and g.item_idx != gt[i - 1].item_idx]
        analyser = _new_analyser(True, True)
        watch = KeyTempoWatch()
        changes, t_end = [], 0.0
        for frame in FileCapture(rdir / "audio.wav", realtime=False).stream():
            u = analyser.feed(frame)
            t_end = u.t_end
            changes += watch.update(u.t_end, u.chord_probs, u.bpm)
        near = lambda t: any(-20 <= t - s <= 45 for s in song_changes)  # noqa: E731
        false = [(round(c.t_seconds), c.kind, f"{c.before}->{c.after}") for c in changes if not near(c.t_seconds)]
        hits = [(round(s), [c.kind for c in changes if -20 <= c.t_seconds - s <= 45]) for s in song_changes]
        total_min += t_end / 60
        false_n += len(false)
        print(f"{name:7s} {t_end / 60:4.1f} min  backend {analyser.backend_name:8s} song changes {hits} | elsewhere {false}",
              flush=True)
    print(f"\nchanges away from song changes: {false_n} in {total_min:.0f} min ({false_n / total_min:.2f}/min)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
