"""Application configuration and on-disk paths.

Production note: the Tauri shell passes the actual database path to the
sidecar via `--db-path`, computed from Tauri's `app_data_dir()` API. The
helpers here are only used as a fallback when the sidecar runs without
`--db-path` (e.g. `uv run pytest`, ad-hoc dev invocation).

For that fallback to land on the same folder Tauri uses, we mirror the
bundle identifier (`com.churchslides.app`) — that's what
`@tauri-apps/api/path::appDataDir` returns under the hood on macOS,
Windows, and Linux.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Must match `identifier` in `src-tauri/tauri.conf.json`.
APP_BUNDLE_ID = "com.churchslides.app"


def app_data_dir() -> Path:
    """Per-user app data directory used by the Tauri shell."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
    path = base / APP_BUNDLE_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return app_data_dir() / "library.db"
