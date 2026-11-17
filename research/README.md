# Research

Everything here is for developing the engine, not part of the app. Nothing in
this folder is bundled into a release build. Product tooling (build, release,
licence notices, the perf gate, model setup) stays in `scripts/`.

| Folder | What lives there |
|---|---|
| `eval/` | Harnesses still used to judge the live engine: the real-time gates (`run_follower_eval.py`, `run_realtime_eval.py`), golden-recording tools (`bootstrap_golden.py`, whose cue re-timing runs `vocal_emissions.py` in a throwaway uv environment, and `annotate.py`), and the fitting/calibration scripts behind shipped constants. |
| `lyric_video/` | Reads burned-in lines off a worship video into a research dataset. Not imported by the app. Output stays in `lyric_video/out/` and is not committed. |
| `spikes/` | Experiments that are open, parked, or finished but worth rerunning. `music_only/` holds the score-follower and in-service-learning work; `fast_channel/` is parked (see its README). |

Scripts find the repo root from their own location (`Path(__file__).resolve().parents[N]`)
and put `sidecar/` on `sys.path`, so run them from anywhere with the sidecar's
environment:

    uv run --project sidecar python research/eval/run_follower_eval.py --help

The write-ups behind most of this live in [docs/research/](../docs/research/)
and [docs/done/](../docs/done/). Some refer to a `dead_ends/` folder of
rejected approaches, which wasn't carried into the public repo.
