"""PerfMonitor — stage timing and resource sampling."""

from __future__ import annotations

import threading
import time

from auto_presenter.pipeline.perf import PerfMonitor, _rss_mb


def test_stage_records_calls_and_timing():
    m = PerfMonitor()
    for _ in range(3):
        with m.stage("asr"):
            time.sleep(0.01)
    snap = m.snapshot()
    st = snap.stages["asr"]
    assert st.calls == 3
    assert st.mean_ms >= 8.0
    assert st.max_ms >= st.mean_ms * 0.9


def test_snapshot_resets_the_window_by_default():
    """Each snapshot describes the window since the last one, so a slow spell
    doesn't keep inflating every later report."""
    m = PerfMonitor()
    with m.stage("asr"):
        time.sleep(0.01)
    assert m.snapshot().stages["asr"].calls == 1
    assert m.snapshot().stages == {}


def test_snapshot_can_peek_without_resetting():
    m = PerfMonitor()
    with m.stage("matcher"):
        pass
    assert m.snapshot(reset=False).stages["matcher"].calls == 1
    assert m.snapshot().stages["matcher"].calls == 1


def test_timing_survives_an_exception_in_the_block():
    """A stage that raises still has to be accounted for — otherwise a failing
    path looks free."""
    m = PerfMonitor()
    try:
        with m.stage("asr"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert m.snapshot().stages["asr"].calls == 1


def test_cpu_percent_reflects_real_work():
    m = PerfMonitor()
    m.snapshot()  # start the window
    deadline = time.monotonic() + 0.15
    while time.monotonic() < deadline:
        sum(range(2000))
    busy = m.snapshot().cpu_percent

    m.snapshot()
    time.sleep(0.15)
    idle = m.snapshot().cpu_percent

    assert busy > idle, f"busy {busy}% should exceed idle {idle}%"


def test_rss_is_reported_in_plausible_megabytes():
    """`ru_maxrss` is bytes on macOS and kilobytes on Linux; getting that
    wrong misreports memory by 1000x in one direction or the other."""
    rss = _rss_mb()
    assert 1.0 < rss < 100_000.0, f"implausible RSS: {rss} MB"


def test_stage_timing_is_thread_safe():
    """Stages are timed on the audio worker thread while snapshots are taken
    from the asyncio loop."""
    m = PerfMonitor()

    def work():
        for _ in range(50):
            with m.stage("asr"):
                pass

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert m.snapshot().stages["asr"].calls == 200
