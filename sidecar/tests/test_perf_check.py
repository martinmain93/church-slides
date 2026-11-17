"""The performance gate itself — a regression detector that never fires is
worse than none, because it looks like coverage."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "perf_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("perf_check", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


pc = _load()

BASE = {
    "matcher_ms_mean": 7.0,
    "matcher_ms_p95": 10.8,
    "peak_live_states": 500,
    "rss_mb": 100.0,
}


def test_identical_numbers_are_not_a_regression() -> None:
    assert pc.compare(dict(BASE), BASE) == []


def test_small_drift_is_tolerated() -> None:
    """Wall-clock timings on a shared laptop are noisy; a gate that fires on
    noise gets ignored, which is the same as not having one."""
    noisy = dict(BASE, matcher_ms_mean=8.4, rss_mb=120.0)
    assert pc.compare(noisy, BASE) == []


def test_a_doubling_is_caught() -> None:
    problems = pc.compare(dict(BASE, matcher_ms_mean=14.5), BASE)
    assert len(problems) == 1
    assert "matcher_ms_mean" in problems[0]


def test_live_state_growth_has_almost_no_slack() -> None:
    """The particle cloud this replaced was a fixed budget that once grew
    500 -> 56,264 by compounding injection; the forward filter's live-state
    count is bounded by the setlist and by pruning instead, but a broken
    prune threshold is exactly the kind of defect this should still catch,
    so the tolerance stays just as tight."""
    assert pc.compare(dict(BASE, peak_live_states=650), BASE)
    assert pc.compare(dict(BASE, peak_live_states=505), BASE) == []


def test_improvements_are_never_regressions() -> None:
    better = dict(BASE, matcher_ms_mean=3.0, rss_mb=40.0, peak_live_states=250)
    assert pc.compare(better, BASE) == []


def test_missing_or_zero_baseline_values_are_skipped() -> None:
    """A new metric shouldn't fail the gate before it has a baseline."""
    assert pc.compare({"matcher_ms_mean": 99.0}, {"matcher_ms_mean": 0}) == []
    assert pc.compare({"brand_new_metric": 5.0}, BASE) == []
