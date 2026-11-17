"""The sidecar has to start on every platform we ship to.

`resource` is Unix-only, and a module-scope import of it — reached from
`server.py` through the perf instrumentation — stopped the sidecar from
starting on Windows at all. Nothing on macOS would ever notice, so this
imports the server with `resource` made unimportable.
"""

from __future__ import annotations

import subprocess
import sys


def test_server_imports_without_unix_only_modules():
    code = (
        "import sys\n"
        "for name in ('resource', 'fcntl', 'termios', 'pty'):\n"
        "    sys.modules[name] = None  # makes `import name` raise ImportError\n"
        "import auto_presenter.server\n"
        "import auto_presenter.main\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
