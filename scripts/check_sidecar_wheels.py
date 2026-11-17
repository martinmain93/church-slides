"""Can the locked sidecar dependencies install on every platform we ship AI to?

    uv run --project sidecar python scripts/check_sidecar_wheels.py

The frozen sidecar's minimum OS is the highest floor among its native wheels,
and a routine `uv lock --upgrade` can silently raise it — that's how the lock
came to exclude every Intel Mac and every Mac older than macOS 14. This
dry-run-installs the lock for each target, binary wheels only, at that
target's *oldest* supported version, so a lock change that drops a platform
fails here rather than on someone's machine.

Pure-Python packages that only publish an sdist (metaphone) are skipped: they
build anywhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / "sidecar"

#: (label, uv --python-platform, MACOSX_DEPLOYMENT_TARGET or None)
TARGETS = [
    ("macOS 11+ Apple silicon", "aarch64-apple-darwin", "11.0"),
    ("macOS 11+ Intel", "x86_64-apple-darwin", "11.0"),
    ("Windows 10+ x64", "x86_64-pc-windows-msvc", None),
    ("Linux x64 (glibc 2.28+)", "x86_64-manylinux_2_28", None),
    ("Linux arm64 (glibc 2.28+)", "aarch64-manylinux_2_28", None),
]
PYTHON = "3.12"


def sdist_only_packages() -> set[str]:
    lock = tomllib.loads((SIDECAR / "uv.lock").read_text())
    return {
        p["name"]
        for p in lock["package"]
        if "sdist" in p and not p.get("wheels") and p.get("source", {}).get("registry")
    }


def export_requirements(skip: set[str]) -> str:
    out = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project"],
        cwd=SIDECAR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    keep = [
        line
        for line in out.splitlines()
        if line.strip() and not line.startswith("#") and line.split("==")[0].strip() not in skip
    ]
    return "\n".join(keep) + "\n"


def check(label: str, platform: str, macos: str | None, reqs: Path, target: Path) -> bool:
    env = dict(os.environ)
    if macos:
        env["MACOSX_DEPLOYMENT_TARGET"] = macos
    result = subprocess.run(
        [
            "uv", "pip", "install", "--dry-run", "--no-build",
            "--python-platform", platform, "--python-version", PYTHON,
            "--target", str(target), "-r", str(reqs),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    ok = result.returncode == 0
    print(f"{'ok  ' if ok else 'FAIL'}  {label}")
    if not ok:
        reason = [ln for ln in result.stderr.splitlines() if "Because" in ln or "╰─▶" in ln]
        print("      " + ("\n      ".join(reason[:3]) or result.stderr.strip()[-600:]))
    return ok


def main() -> None:
    skip = sdist_only_packages()
    with tempfile.TemporaryDirectory() as tmp:
        reqs = Path(tmp) / "requirements.txt"
        reqs.write_text(export_requirements(skip))
        results = [check(*t, reqs, Path(tmp) / "target") for t in TARGETS]
    if skip:
        print(f"(skipped pure-Python sdists: {', '.join(sorted(skip))})")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
