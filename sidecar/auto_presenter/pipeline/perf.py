"""Cheap, always-on performance instrumentation.

Two audiences, one source of numbers:

  - **Live**, so an operator (and we) can see the sidecar's CPU and memory and
    where its time is going while a service runs. A render loop in the UI
    starved this pipeline for an entire session and the only visible symptom
    was the AI "not working" — cost has to be observable or it gets diagnosed
    by guesswork.
  - **In test runs**, so a change that doubles the cost of an ASR pass or the
    matcher update is caught by a number rather than by someone noticing the
    fans.

Deliberately dependency-free: `time.process_time()` and `resource.getrusage`
(or, on Windows, which has no `resource`, `GetProcessMemoryInfo` via `ctypes`)
are in the standard library, and a desktop app shouldn't grow a dependency to
report its own CPU usage.

The cost of measuring is a `monotonic()` pair per stage — nanoseconds against
the hundreds of milliseconds being measured.
"""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass


def _rss_mb() -> float:
    """Peak resident set size in MB.

    `ru_maxrss` is bytes on macOS and kilobytes on Linux — a genuine platform
    difference that silently reports a 1000x error if ignored. Windows has no
    `resource` module at all — importing it at module scope stopped the whole
    sidecar from starting there — so it reads the peak working set instead.
    """
    if sys.platform == "win32":
        return _peak_working_set_bytes() / (1024 * 1024)
    import resource

    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def _peak_working_set_bytes() -> int:
    import ctypes
    from ctypes import wintypes

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    # The K32-prefixed export lives in kernel32 from Windows 7 on, so this
    # needs no psapi.dll.
    get_info = kernel32.K32GetProcessMemoryInfo
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    if not get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return 0
    return int(counters.PeakWorkingSetSize)


@dataclass
class StageStats:
    """Accumulated timings for one named stage."""

    calls: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0

    @property
    def mean_ms(self) -> float:
        return (self.total_seconds / self.calls * 1000.0) if self.calls else 0.0

    @property
    def max_ms(self) -> float:
        return self.max_seconds * 1000.0


@dataclass
class PerfSnapshot:
    """A window of performance, since the previous snapshot."""

    #: Share of one core used by this process over the window, 0..N*100.
    cpu_percent: float
    #: Peak RSS for the process, in MB.
    rss_mb: float
    #: Per-stage timings accumulated during the window.
    stages: dict[str, StageStats]
    window_seconds: float

    def stage_mean_ms(self, name: str) -> float:
        s = self.stages.get(name)
        return s.mean_ms if s else 0.0


class PerfMonitor:
    """Times named stages and samples process CPU / memory.

    Thread-safe: stages are timed on the audio worker thread while snapshots
    are taken from the asyncio loop.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stages: dict[str, StageStats] = {}
        self._last_wall = time.monotonic()
        self._last_cpu = time.process_time()

    @contextmanager
    def stage(self, name: str):
        """Time a block and attribute it to `name`."""
        started = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - started
            with self._lock:
                st = self._stages.get(name)
                if st is None:
                    st = self._stages[name] = StageStats()
                st.calls += 1
                st.total_seconds += elapsed
                if elapsed > st.max_seconds:
                    st.max_seconds = elapsed

    def snapshot(self, *, reset: bool = True) -> PerfSnapshot:
        """Summarize the window since the last snapshot."""
        now_wall = time.monotonic()
        now_cpu = time.process_time()
        with self._lock:
            window = max(1e-9, now_wall - self._last_wall)
            cpu = (now_cpu - self._last_cpu) / window * 100.0
            stages = {k: StageStats(v.calls, v.total_seconds, v.max_seconds)
                      for k, v in self._stages.items()}
            if reset:
                self._last_wall = now_wall
                self._last_cpu = now_cpu
                self._stages = {}
        return PerfSnapshot(
            cpu_percent=round(cpu, 1),
            rss_mb=round(_rss_mb(), 1),
            stages=stages,
            window_seconds=window,
        )


#: Stage names, centralized so the live UI, the perf baseline and the code
#: doing the work cannot drift apart.
STAGE_ASR = "asr"
STAGE_MATCHER = "matcher"
STAGE_BELIEF_BROADCAST = "belief_broadcast"
