"""Each span's score on silence and on pink noise: Whisper's language-model
prior for it (as `follower.openings.OpeningsWatch.warm_up`), per window/span.

    uv run --project sidecar python research/spikes/music_only/scorer_prior.py WINDOW SPAN [names]

Writes build/scorer_lat/prior_{name}_w{W}_n{N}.json: {item: [max(silence, noise) per span,
in sorted(set(spans_for(slides, N).values())) order]}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr  # noqa: E402
import scorer_cache as scc  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.follower.openings import _pink_noise  # noqa: E402
from auto_presenter.follower.scorer import MLXSpanScorer, spans_for  # noqa: E402


def prior_path(name, window, span):
    return scc.OUT / f"prior_{name}_w{window:g}_n{span}.json"


def make(name, window, span):
    path = prior_path(name, window, span)
    if path.exists():
        return path
    rec = load_recording(mr.CASES[name][0])
    sc = MLXSpanScorer(scc.scorer(), audio_ctx=int(round(window * 50)))
    n = int(window * scc.SR)
    out = {}
    for x in rec.setlist_content:
        spans = sorted(set(spans_for(x.slides, span).values()))
        if not spans:
            continue
        sil = sc.score(np.zeros(n, np.float32), spans)
        noi = sc.score(_pink_noise(n), spans)
        out[str(x.item_idx)] = [round(float(max(a, b)), 4) for a, b in zip(sil.scores, noi.scores, strict=True)]
    scc.OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return path


def load(name, window, span):
    return {int(k): v for k, v in json.loads(prior_path(name, window, span).read_text()).items()}


if __name__ == "__main__":
    window, span = float(sys.argv[1]), int(sys.argv[2])
    for nm in sys.argv[3:] or list(mr.CASES):
        print(make(nm, window, span), flush=True)
