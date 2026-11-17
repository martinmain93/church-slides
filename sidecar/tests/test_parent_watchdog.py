"""The sidecar must not outlive the app that spawned it.

Left alone it does: 46 orphaned processes accumulated over 12 days, three of
them holding a loaded Whisper model and burning ~3.5 cores between them — which
competes with the live pipeline for the CPU that keeps ASR passes inside their
budget, so the leak degrades the running app and not merely the machine.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

from auto_presenter.main import _pid_alive, watch_parent


def test_a_live_process_reads_as_alive():
    assert _pid_alive(os.getpid()) is True


def test_a_reaped_process_reads_as_dead():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert _pid_alive(proc.pid) is False


def test_a_process_we_do_not_own_still_counts_as_alive():
    """`os.kill(pid, 0)` raises PermissionError for another user's process.
    Treating that as "gone" would make the sidecar quit at random."""
    # pid 1 (launchd/init) is always present and never ours to signal; on
    # Windows that's pid 4, the System process, which denies us access.
    assert _pid_alive(4 if sys.platform == "win32" else 1) is True


@pytest.mark.asyncio
async def test_the_watchdog_returns_once_the_parent_exits():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        watcher = asyncio.ensure_future(watch_parent(proc.pid, poll_seconds=0.02))
        await asyncio.sleep(0.1)
        assert not watcher.done(), "returned while the parent was still running"

        proc.terminate()
        proc.wait()
        await asyncio.wait_for(watcher, timeout=2.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.asyncio
async def test_the_watchdog_waits_while_the_parent_lives():
    watcher = asyncio.ensure_future(watch_parent(os.getpid(), poll_seconds=0.02))
    await asyncio.sleep(0.15)
    assert not watcher.done()
    watcher.cancel()


@pytest.mark.asyncio
async def test_the_server_actually_stops_when_the_parent_goes_away():
    """End to end through `_run`: the unit tests above prove the watchdog
    returns, this proves the return reaches the server and closes it."""
    from auto_presenter.main import _run

    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        run = asyncio.ensure_future(
            _run(host="127.0.0.1", port=0, db_path=None, parent_pid=parent.pid)
        )
        await asyncio.sleep(0.3)
        assert not run.done(), "exited while the parent was alive"

        parent.terminate()
        parent.wait()
        await asyncio.wait_for(run, timeout=10.0)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        if not run.done():
            run.cancel()


@pytest.mark.asyncio
async def test_without_a_parent_pid_the_server_keeps_running():
    """Running the sidecar by hand (tests, CLI use) must not self-terminate."""
    from auto_presenter.main import _run

    run = asyncio.ensure_future(
        _run(host="127.0.0.1", port=0, db_path=None, parent_pid=None)
    )
    await asyncio.sleep(0.3)
    assert not run.done()
    # `_run` swallows CancelledError so a shutdown closes the server cleanly
    # rather than propagating, so cancelling completes the task normally.
    run.cancel()
    await asyncio.wait_for(asyncio.gather(run, return_exceptions=True), timeout=5.0)


def test_windows_check_never_signals_the_parent(monkeypatch):
    """On Windows `os.kill(pid, 0)` is TerminateProcess: the liveness check
    would kill the app it is watching. The Windows branch must not reach it."""
    import auto_presenter.main as main_mod

    def forbidden(*_args):
        raise AssertionError("os.kill must not be called on Windows")

    monkeypatch.setattr(main_mod.sys, "platform", "win32")
    monkeypatch.setattr(main_mod.os, "kill", forbidden)
    monkeypatch.setattr(main_mod, "_pid_alive_windows", lambda pid: pid == 42)
    assert main_mod._pid_alive(42) is True
    assert main_mod._pid_alive(43) is False
