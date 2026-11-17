"""The ONNX Runtime span scorer: its numpy DTW and median filter against
mlx_whisper's (where installed), and a real scoring pass when exported models
are on this machine (scripts/export_whisper_scorer_onnx.py)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from auto_presenter.follower.onnx_scorer import OnnxSpanScorer, available, dtw, median_filter

ROOT = Path(__file__).resolve().parents[3]


def test_dtw_matches_mlx_whispers():
    timing = pytest.importorskip("mlx_whisper.timing")
    rng = np.random.default_rng(0)
    for shape in ((5, 40), (12, 300), (1, 9)):
        x = rng.normal(size=shape).astype(np.float32)
        ours = dtw(x)
        ref = timing.dtw(x.copy())
        assert np.array_equal(ours[0], ref[0]) and np.array_equal(ours[1], ref[1])


def test_dtw_walks_a_diagonal_cheapest_path():
    cost = np.ones((3, 6), np.float32)
    for i, j in ((0, 0), (0, 1), (1, 2), (1, 3), (2, 4), (2, 5)):
        cost[i, j] = 0.0
    rows, cols = dtw(cost)
    # each token's first frame: 0, 2, 4
    first = {int(r): int(c) for r, c in zip(rows[::-1], cols[::-1], strict=True)}
    assert [first[0], first[1], first[2]] == [0, 2, 4]


def test_median_filter_matches_mlx_whispers():
    timing = pytest.importorskip("mlx_whisper.timing")
    x = np.random.default_rng(1).normal(size=(4, 7, 50)).astype(np.float32)
    assert np.allclose(median_filter(x, 7), timing.median_filter(x, 7), atol=1e-6)


@pytest.fixture(scope="module")
def tiny():
    d = ROOT / "build" / "onnx" / "tiny.en"
    if not available(d):
        pytest.skip("no exported tiny.en here (scripts/export_whisper_scorer_onnx.py)")
    return OnnxSpanScorer(d)


def test_the_sung_line_scores_best_and_lands_in_the_window(tiny):
    sf = pytest.importorskip("soundfile")
    audio, _ = sf.read(ROOT / "data/fixtures/perf/service.wav", dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    window = audio[30 * 16000 : 36 * 16000]
    # what is sung there, among lines that aren't
    spans = ["the power of your blood", "amazing grace how sweet", "here i am to worship"]
    sung = tiny.score(window, ["no power of hell"] + spans)
    assert sung is not None
    assert 0.0 <= sung.place_s <= 6.0
    assert len(sung.scores) == 4 and np.isfinite(sung.scores).all()


def test_a_prefix_is_scored_by_the_span_it_begins():
    from auto_presenter.follower.onnx_scorer import _prefix_runs

    toks = [[1, 2, 3], [1, 2], [1], [4, 5], [1, 2, 3], [7]]
    runs, owner = _prefix_runs(toks)
    assert sorted(runs) == [(1, 2, 3), (4, 5), (7,)]
    assert owner[(1,)] == owner[(1, 2)] == (1, 2, 3)
    assert owner[(4, 5)] == (4, 5)


@pytest.fixture(scope="module")
def tiny_fp32():
    d = ROOT / "build" / "onnx" / "tiny.en"
    if not available(d):
        pytest.skip("no exported tiny.en here (scripts/export_whisper_scorer_onnx.py)")
    return OnnxSpanScorer(d)


def test_sharing_prefixes_changes_no_score(tiny_fp32, monkeypatch):
    """fp32: a span scored inside a longer one scores exactly as alone (the
    decoder is causal). Not true of dynamic int8, whose activation scale
    depends on the whole batch, which is why the engine uses fp32."""
    import auto_presenter.follower.onnx_scorer as osc

    sf = pytest.importorskip("soundfile")
    audio, _ = sf.read(ROOT / "data/fixtures/perf/service.wav", dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    window = audio[30 * 16000 : 36 * 16000]
    spans = ["the power", "the power of", "the power of your blood", "no power of hell", "no power"]
    shared = tiny_fp32.score(window, spans).scores

    def unshared(toks):
        unique = list(dict.fromkeys(tuple(t) for t in toks if t))
        return unique, {t: t for t in unique}

    monkeypatch.setattr(osc, "_prefix_runs", unshared)
    alone = tiny_fp32.score(window, spans).scores
    assert np.allclose(shared, alone, atol=1e-4)
