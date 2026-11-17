"""Sidecar entry point.

Parses CLI args, starts the WebSocket server, and announces its bound port on
stdout so the Tauri shell can route the React UI to it.
"""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import json
import logging
import os
import sys
from pathlib import Path

from auto_presenter.server import serve

log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(prog="auto_presenter")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind to (0 = let the OS choose)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to the SQLite database (default: in-memory placeholder)",
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help=(
            "Exit when this process goes away. The Tauri shell passes its own "
            "pid; without it a sidecar outlives the app that spawned it."
        ),
    )
    parser.add_argument(
        "--dev-tools",
        action="store_true",
        help=(
            "Enable agent-testing WebSocket verbs (inject_audio, "
            "session_snapshot, wait_for_slide). The Tauri debug shell passes "
            "this; release builds never do."
        ),
    )
    parser.add_argument(
        "--models-dir",
        default=None,
        help=(
            "Keep downloaded speech models here instead of the shared "
            "Hugging Face cache, moving any already there. The release app "
            "passes a folder beside its library (see asr/model_home.py)."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help=(
            "Import every lazily-loaded dependency, print a JSON report and "
            "exit. Used to verify a frozen build before shipping it."
        ),
    )
    parser.add_argument(
        "--self-check-audio",
        default=None,
        help=(
            "With --self-check: also transcribe this file with an already-"
            "downloaded tiny.en model, proving inference runs, not just loads."
        ),
    )
    args = parser.parse_args()

    if args.self_check:
        from auto_presenter.self_check import run_self_check

        sys.exit(run_self_check(audio=args.self_check_audio))

    # A native crash (a segfault in CTranslate2 or Metal, say) would otherwise
    # end the process without a word. The shell copies stderr into the app's
    # log file, so the faulting stack ends up somewhere it can be read.
    faulthandler.enable()

    # Logs go to stderr so stdout stays a clean event channel for the Tauri shell.
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.models_dir:
        # Before anything imports huggingface_hub, which reads its cache
        # folder once, on import.
        from auto_presenter.asr.model_home import use_models_dir
        from auto_presenter.asr.model_manager import _default_cache_dir

        use_models_dir(Path(args.models_dir), shared_cache=_default_cache_dir())

    from auto_presenter.error_reports import init as init_error_reports

    init_error_reports(args.db_path)

    asyncio.run(
        _run(
            host=args.host,
            port=args.port,
            db_path=args.db_path,
            parent_pid=args.parent_pid,
            dev_tools=args.dev_tools,
        )
    )


#: How often to check that the parent is still there. Long enough to cost
#: nothing, short enough that a relaunch does not race a departing sidecar.
PARENT_POLL_SECONDS = 2.0


def _pid_alive(pid: int) -> bool:
    """Is this process still around?

    Signal 0 performs the permission and existence checks without delivering
    anything. `PermissionError` means the pid exists but belongs to someone
    else, which still counts as alive.

    Not on Windows: there `os.kill` with any signal other than the two console
    control events calls `TerminateProcess` — the "check" would kill the app
    that launched us.
    """
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Open the process for a read-only query and ask whether it has exited.

    A handle to an exited process stays openable while anyone else holds one,
    so a successful `OpenProcess` alone does not mean alive — the exit code
    does. Access denied means it exists under another user: alive, as above.
    """
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


async def watch_parent(pid: int, poll_seconds: float = PARENT_POLL_SECONDS) -> None:
    """Return once `pid` is gone.

    The sidecar must not outlive the app that spawned it, and it cannot rely on
    being killed. In dev the Tauri shell launches `uv run python -m
    auto_presenter`, so the process Rust holds is `uv` and the Python
    interpreter is a *grandchild*: killing the handle Rust owns would leave this
    process running. `kill_on_drop` does not help either, since the handle is
    parked in a background task and the app exits without dropping it. And
    neither would survive a force-quit.

    Watching `os.getppid()` is not enough for the same reason — when the shell
    dies, `uv` is reparented to init but does not exit, so this process's
    immediate parent never changes. So the shell passes its *own* pid and we
    watch that directly.

    Measured consequence of not doing this: 46 orphaned processes accumulated
    over 12 days, three of them holding a loaded Whisper model and burning
    ~3.5 cores between them — which competes with the live pipeline for exactly
    the CPU that keeps ASR passes inside their budget.
    """
    while _pid_alive(pid):
        await asyncio.sleep(poll_seconds)


async def _run(
    *,
    host: str,
    port: int,
    db_path: str | None,
    parent_pid: int | None = None,
    dev_tools: bool = False,
) -> None:
    server = await serve(host=host, port=port, db_path=db_path, dev_tools=dev_tools)

    bound_port = _resolve_port(server)
    # stdout is the structured event channel that the Tauri shell parses.
    # First line MUST be the listening announcement.
    print(json.dumps({"event": "listening", "port": bound_port}), flush=True)
    if dev_tools:
        from auto_presenter.dev.lockfile import write_lockfile

        write_lockfile(bound_port)

    serving = asyncio.ensure_future(server.serve_forever())
    waiters = [serving]
    if parent_pid is not None:
        waiters.append(asyncio.ensure_future(watch_parent(parent_pid)))

    try:
        done, pending = await asyncio.wait(
            waiters, return_when=asyncio.FIRST_COMPLETED
        )
        if serving not in done:
            log.info("parent process %s is gone — shutting down", parent_pid)
        for task in pending:
            task.cancel()
        for task in done:
            if task is not serving:
                task.result()  # surface a watchdog crash rather than hiding it
    except asyncio.CancelledError:
        pass
    finally:
        if dev_tools:
            from auto_presenter.dev.lockfile import remove_lockfile

            remove_lockfile()
        for task in waiters:
            task.cancel()
        server.close()
        await server.wait_closed()


def _resolve_port(server) -> int:
    sockets = getattr(server, "sockets", None)
    if not sockets:
        raise RuntimeError("server has no bound sockets")
    return sockets[0].getsockname()[1]
