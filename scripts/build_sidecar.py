"""Freeze the Python sidecar and stage it for the Tauri bundle.

    uv run --project sidecar python scripts/build_sidecar.py

Builds `sidecar/packaging/sidecar.spec` with PyInstaller, then copies the
one-folder result to `src-tauri/resources/sidecar/`, which `tauri.conf.json`
bundles as a resource. The release shell spawns
`<resources>/sidecar/auto-presenter-sidecar[.exe]` from there.

Then it verifies the frozen binary, because the source tree passing its tests
says nothing about what PyInstaller left out:

  1. `--self-check` — every lazily-imported dependency imports, its native
     library loads, its data files exist, and it transcribes
     `sidecar/packaging/smoke.wav` ("Amazing grace, how sweet the sound") with
     tiny.en, fetched into the Hugging Face cache first if it isn't there.
  2. A real start — the binary binds a port and prints the `listening` line the
     shell waits for, then exits when its `--parent-pid` goes away.

It bundles the chord model (BTC, MIT) and the library search model
(e5-small-v2, MIT), which are too big for git: see `stage_models`. The chord
model must already be installed on the build machine
(`scripts/setup_chord_model.py`); the search model is fetched by
`scripts/setup_search_model.py` at a pinned revision and hash. No FFmpeg ships: PyAV's copy is GPL, so the engine
carries `sidecar/packaging/stubs/av`, and `check_no_gpl_ffmpeg` fails the build
if a GPL FFmpeg library turns up anyway. And it records the licence of every
distribution it froze in, for the app's THIRD-PARTY-NOTICES.txt (`record_licences`).

It builds in its own venv (`sidecar/.venv-build-<arch>`) from uv's managed,
portable CPython — never the dev venv, and never a locally compiled Python. A
pyenv/Homebrew interpreter links the machine's own OpenSSL/readline/xz, built
for whatever macOS it's running, and PyInstaller bundles them: the first frozen
build this produced needed macOS 26 to launch. On macOS the build fails if any
bundled binary needs a newer OS than `MACOS_FLOOR`.

PyInstaller can't cross-compile: run this on each OS you ship. The one
exception is Intel on an Apple-silicon Mac, which runs under Rosetta:

    uv run --project sidecar python scripts/build_sidecar.py --arch x86_64
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / "sidecar"
PACKAGING = SIDECAR / "packaging"
WORK = SIDECAR / "build" / "pyinstaller"
DIST = SIDECAR / "dist"
STAGE = ROOT / "src-tauri" / "resources" / "sidecar"
SMOKE_AUDIO = PACKAGING / "smoke.wav"
EXE_NAME = "auto-presenter-sidecar" + (".exe" if sys.platform == "win32" else "")
#: Where the bundled models are gathered for sidecar.spec (SIDECAR_MODELS_DIR).
MODELS_STAGE = SIDECAR / "build" / "models"
MODEL_LICENSES = PACKAGING / "model_licenses"

#: Oldest macOS the AI engine supports. The lock's macOS constraints (see
#: sidecar/pyproject.toml) exist to keep every wheel at or below this.
MACOS_FLOOR = "11.0"
PYTHON = "3.12"

#: The Apple-silicon engine also carries MLX (Whisper on the GPU), which needs
#: macOS 14. It's an optional layer: on macOS 11-13 `import mlx` fails, the
#: engine reports MLX unavailable and runs faster-whisper on the CPU, exactly
#: as an engine without it does. The layer is everything the `mlx` extra adds
#: to the base engine (MLX, mlx-whisper, and numba/llvmlite/scipy for its word
#: timings, which need macOS 12): nothing else imports them, so all of it is
#: built for MLX_FLOOR and only it may exceed MACOS_FLOOR.
MLX_FLOOR = "14.0"
#: Where the layer's files land in the bundle (import names, not dist names).
MLX_LAYER_DIRS = {"mlx", "mlx_whisper", "scipy", "tiktoken"}
#: Left out of the build env entirely. The `mlx` extra pulls torch through
#: mlx-whisper's metadata, but only its unused torch_whisper.py imports it; and
#: numba (with LLVM, 123 MB) is replaced by sidecar/packaging/stubs/numba.
MLX_SKIP = ("torch", "numba", "llvmlite")


def with_mlx(arch: str) -> bool:
    return sys.platform == "darwin" and arch == "aarch64"


def host_arch() -> str:
    machine = platform.machine().lower()
    return {"arm64": "aarch64", "amd64": "x86_64"}.get(machine, machine)


def build_env(arch: str) -> Path:
    """Create/refresh the build venv for `arch`; return its interpreter.

    Installs the *locked* versions, but not with `uv sync`: that picks wheels
    by the running interpreter's tags, so on a new Mac it takes numpy's
    macOS-14-only wheel over the macOS-11 one sitting beside it in the same
    release. `uv pip install --python-platform` honours
    MACOSX_DEPLOYMENT_TARGET, so every wheel is chosen for `MACOS_FLOOR`.
    """
    env_dir = SIDECAR / f".venv-build-{arch}"
    request = f"cpython-{PYTHON}-macos-{arch}-none" if sys.platform == "darwin" else PYTHON
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    subprocess.run(
        ["uv", "venv", str(env_dir), "--python", request, "--python-preference", "only-managed"],
        cwd=SIDECAR,
        env=env,
        check=True,
    )
    python = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    reqs = SIDECAR / "build" / f"requirements-{arch}.txt"
    reqs.parent.mkdir(parents=True, exist_ok=True)
    exported = subprocess.run(
        ["uv", "export", "--frozen", "--group", "dev", "--no-hashes", "--no-emit-project"],
        cwd=SIDECAR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    reqs.write_text(exported)

    install = ["uv", "pip", "install", "--python", str(python), "-r", str(reqs)]
    if sys.platform == "darwin":
        env["MACOSX_DEPLOYMENT_TARGET"] = MACOS_FLOOR
        install += ["--python-platform", f"{arch}-apple-darwin"]
    subprocess.run(install, cwd=SIDECAR, env=env, check=True)
    if with_mlx(arch):
        _install_mlx_layer(python, arch, env, reqs.parent)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--no-deps", "--reinstall-package",
         "auto-presenter-sidecar", "."],
        cwd=SIDECAR,
        env=env,
        check=True,
    )
    return python


def _requirements(env: dict, *extra: str) -> dict[str, str]:
    """Locked requirement lines by package name."""
    exported = subprocess.run(
        ["uv", "export", "--frozen", "--group", "dev", "--no-hashes", "--no-emit-project", *extra],
        cwd=SIDECAR, env=env, capture_output=True, text=True, check=True,
    ).stdout
    lines = [ln for ln in exported.splitlines() if ln and not ln.lstrip().startswith(("#", "-"))]
    return {ln.split("==")[0].strip().lower(): ln for ln in lines}


def _install_mlx_layer(python: Path, arch: str, env: dict, out: Path) -> None:
    """What the `mlx` extra adds to the base env, for MLX_FLOOR, without torch.
    Locked versions, installed without resolving again; base packages untouched."""
    base = _requirements(env)
    skip = [a for p in MLX_SKIP for a in ("--no-emit-package", p)]
    layer = {k: v for k, v in _requirements(env, "--extra", "mlx", *skip).items() if k not in base}
    path = out / f"requirements-{arch}-mlx.txt"
    path.write_text("\n".join(layer.values()) + "\n")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--no-deps", "-r", str(path),
         "--python-platform", f"{arch}-apple-darwin"],
        cwd=SIDECAR, env={**env, "MACOSX_DEPLOYMENT_TARGET": MLX_FLOOR}, check=True,
    )


def stage_models(next_line: bool) -> Path:
    """Gather the chord model, with its licence, for the spec.

    The source is where the engine looks in development: the
    `AUTO_PRESENTER_CHORD_MODEL` override, else the app data folder. A missing chord model fails the build — shipping
    without it silently drops users to template chords, which is what every
    release before this did.
    """
    sys.path.insert(0, str(SIDECAR))
    from auto_presenter.music.btc import default_model_dir

    if MODELS_STAGE.exists():
        shutil.rmtree(MODELS_STAGE)
    chords = MODELS_STAGE / "chords"
    chords.mkdir(parents=True)

    btc_src = default_model_dir()
    for name in ("btc_chords.onnx", "btc_chords.json"):
        if not (btc_src / name).is_file():
            raise SystemExit(
                f"chord model missing: {btc_src / name}\n"
                "install it with `uv run --project sidecar python scripts/setup_chord_model.py`, "
                "or point AUTO_PRESENTER_CHORD_MODEL at a folder holding btc_chords.onnx/.json"
            )
        shutil.copy2(btc_src / name, chords / name)
    meta = json.loads((chords / "btc_chords.json").read_text())
    too_small = (chords / "btc_chords.onnx").stat().st_size < 1e6
    if "BTC" not in str(meta.get("source", "")) or too_small:
        raise SystemExit(f"{btc_src} isn't the BTC export (source={meta.get('source')})")
    shutil.copy2(MODEL_LICENSES / "BTC-ISMIR19-LICENSE.txt", chords / "LICENSE.txt")

    # The library search model (e5-small-v2, MIT): pinned and hash-checked,
    # fetched from Hugging Face if this machine doesn't have it cached.
    sys.path.insert(0, str(ROOT / "scripts"))
    from setup_search_model import install as install_search_model

    install_search_model(MODELS_STAGE / "search")

    # The next-line check's phone model (trained on public music only), for
    # MLX builds, the only ones that run the check: where the engine looks in
    # development, installed by scripts/setup_next_line_model.py. Missing,
    # the check would be off for every user of the build, so it fails the
    # build like the chord model.
    if next_line:
        from auto_presenter.follower.next_line import MODEL_FILE, model_dir

        nl_src = model_dir()
        if not (nl_src / MODEL_FILE).is_file():
            raise SystemExit(
                f"next-line model missing: {nl_src / MODEL_FILE}\n"
                "run: uv run --project sidecar python scripts/setup_next_line_model.py"
            )
        (MODELS_STAGE / "next_line").mkdir()
        for name in (MODEL_FILE, "next_line_head.json"):
            if (nl_src / name).is_file():
                shutil.copy2(nl_src / name, MODELS_STAGE / "next_line" / name)

    size_mb = sum(p.stat().st_size for p in MODELS_STAGE.rglob("*") if p.is_file()) / 1e6
    print(f"models staged: chords from {btc_src}, library search, next-line ({size_mb:.1f} MB)")
    return MODELS_STAGE


#: Markers FFmpeg compiles into each library: its configure line and its
#: `*_license()` string. PyAV's wheels say "license: GPL version 3 or later".
_GPL_FFMPEG_MARKERS = (b"--enable-gpl", b"license: GPL", b"license: nonfree")
_FFMPEG_NAMES = ("avcodec", "avformat", "avutil", "avdevice", "avfilter", "swresample",
                 "swscale", "postproc", "x264", "x265")


def check_no_gpl_ffmpeg(root: Path) -> None:
    """No GPL FFmpeg — by name, or by the licence FFmpeg compiles in — may ship."""
    bad = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        name = path.name.lower()
        if any(name.startswith((n, "lib" + n)) for n in _FFMPEG_NAMES):
            bad.append((path, "FFmpeg library"))
            continue
        if path.suffix.lower() in (".so", ".dylib", ".dll", ".pyd"):
            data = path.read_bytes()
            hit = next((m for m in _GPL_FFMPEG_MARKERS if m in data), None)
            if hit:
                bad.append((path, hit.decode()))
    if bad:
        for path, why in bad[:15]:
            print(f"  {why}: {path.relative_to(root)}")
        raise SystemExit(f"{len(bad)} GPL FFmpeg files in the engine (PyAV must not ship)")
    print("licence ok: no FFmpeg/GPL libav* in the engine")


def build(python: Path, arch: str) -> Path:
    env = dict(os.environ)
    env["SIDECAR_MODELS_DIR"] = str(stage_models(next_line=with_mlx(arch)))  # read by sidecar.spec
    # stubs/av always (sidecar.spec), stubs/numba for the MLX build: PyInstaller
    # imports packages in subprocesses to list their submodules.
    env["PYTHONPATH"] = str(PACKAGING / "stubs")
    if with_mlx(arch):
        env["SIDECAR_WITH_MLX"] = "1"  # read by sidecar.spec
    subprocess.run(
        [
            str(python),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--workpath",
            str(WORK),
            "--distpath",
            str(DIST),
            str(PACKAGING / "sidecar.spec"),
        ],
        cwd=PACKAGING,
        env=env,
        check=True,
    )
    return DIST / "sidecar"


def stage(built: Path) -> Path:
    """Copy the engine where Tauri bundles it from, the way Tauri will.

    Tauri copies resources with symlinks followed, so the shipped engine has
    none: staging the same way means the self-check and the performance run
    below test what users get. Staged with its symlinks, MLX passed here and
    failed in every installed app (0.0.5): see `_colocate_mlx_metallib`.
    """
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(built, STAGE, symlinks=False)
    _colocate_mlx_metallib(STAGE)
    return STAGE / EXE_NAME


def _colocate_mlx_metallib(root: Path) -> None:
    """Put MLX's Metal shaders beside the libmlx that is actually loaded.

    `mlx/core.so` finds `@rpath/libmlx.dylib` at `@loader_path/..`, i.e. the
    top of `_internal`, where PyInstaller leaves a symlink to
    `mlx/lib/libmlx.dylib`. MLX looks for `mlx.metallib` next to the libmlx
    it was loaded from: through the symlink that is `mlx/lib`, where the
    shaders are; once copied as a plain file it is `_internal`, where they
    are not, and MLX fails to load ("Failed to load the default metallib").
    Moving the shaders (136 MB) rather than copying them keeps one copy.
    """
    internal = root / "_internal"
    shaders = internal / "mlx" / "lib" / "mlx.metallib"
    if (internal / "libmlx.dylib").is_file() and shaders.is_file():
        shutil.move(shaders, internal / "mlx.metallib")


def _macho_minos(path: Path) -> str | None:
    out = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True).stdout
    lines = out.splitlines()
    for i, line in enumerate(lines):
        cmd = line.strip()
        if cmd in ("cmd LC_BUILD_VERSION", "cmd LC_VERSION_MIN_MACOSX"):
            for follow in lines[i + 1 : i + 6]:
                parts = follow.split()
                if parts and parts[0] in ("minos", "version"):
                    return parts[1]
    return None


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def check_macos_floor(root: Path) -> None:
    """Every Mach-O file in the bundle must load on `MACOS_FLOOR`, except the
    optional MLX layer, which must load on `MLX_FLOOR`."""
    too_new = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if not (path.suffix in (".so", ".dylib") or os.access(path, os.X_OK)):
            continue
        minos = _macho_minos(path)
        rel = path.relative_to(root)
        in_layer = MLX_LAYER_DIRS & set(rel.parts) or rel.name.startswith("libmlx")
        floor = MLX_FLOOR if in_layer else MACOS_FLOOR
        if minos and _version_key(minos) > _version_key(floor):
            too_new.append((minos, rel))
    if too_new:
        for minos, rel in sorted(too_new, key=lambda t: _version_key(t[0]), reverse=True)[:15]:
            print(f"  needs macOS {minos}: {rel}")
        raise SystemExit(f"{len(too_new)} bundled binaries need a newer macOS than {MACOS_FLOOR}")
    print(f"macOS floor ok: every bundled binary loads on macOS {MACOS_FLOOR} "
          f"(the optional MLX layer on {MLX_FLOOR})")


def fetch_smoke_model() -> None:
    """The transcription check runs offline, so the model has to be cached."""
    from faster_whisper.utils import download_model

    download_model("tiny.en")


def record_licences(python: Path, arch: str) -> None:
    """Record whose code is in the engine, for the app's third-party notices.

    PyInstaller's work folder says which installed files it froze; each one's
    distribution, licence and licence texts go to sidecar/build/engine-notices.json,
    which scripts/third_party_notices.py folds into THIRD-PARTY-NOTICES.txt when
    the app is built. A licence that needs a decision (GPL, non-commercial, ...)
    and isn't in scripts/notices/reviewed.json fails the build here, not after
    the app build.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import third_party_notices

    packages = third_party_notices.write_engine_notices(python, WORK / "sidecar", arch)
    third_party_notices.report_review(packages)
    print(f"licences recorded: {len(packages)} engine components "
          f"({third_party_notices.ENGINE_RECORD.relative_to(ROOT)})")


ENTITLEMENTS = ROOT / "src-tauri" / "Entitlements.plist"


def sign_macos(root: Path, identity: str) -> None:
    """Sign every Mach-O in the staged engine for notarisation.

    Inside-out — libraries first, the executable last — with the hardened
    runtime and the same entitlements as the app. `identity` is a Developer ID
    Application identity, or `-` for an ad-hoc signature, which enforces the
    same hardened-runtime rules and so proves the entitlements locally.
    """
    machos = [
        p
        for p in root.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and (p.suffix in (".so", ".dylib") or os.access(p, os.X_OK))
        and _macho_minos(p) is not None
    ]
    exe = root / EXE_NAME
    ordered = sorted((p for p in machos if p != exe), key=lambda p: -len(p.parts)) + [exe]
    base = ["codesign", "--force", "--options", "runtime", "--sign", identity]
    if identity != "-":
        base.append("--timestamp")
    for path in ordered:
        cmd = list(base)
        if path == exe:
            cmd += ["--entitlements", str(ENTITLEMENTS)]
        subprocess.run([*cmd, str(path)], check=True, capture_output=True)
    subprocess.run(["codesign", "--verify", "--strict", str(exe)], check=True)
    print(f"signed {len(ordered)} binaries ({'ad hoc' if identity == '-' else identity})")


def self_check(exe: Path) -> None:
    fetch_smoke_model()
    out = subprocess.run(
        [str(exe), "--self-check", "--self-check-audio", str(SMOKE_AUDIO)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    # Native libraries can print to stdout before the report (Intel MKL warns
    # about SSE4.2-only CPUs under Rosetta), so parse from the first `{` line.
    lines = out.stdout.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("{")), None)
    report = json.loads("\n".join(lines[start:])) if start is not None else None
    if out.returncode != 0 or not report or not report.get("ok"):
        sys.stderr.write(out.stdout + out.stderr)
        raise SystemExit("frozen sidecar failed --self-check")
    print("self-check ok:", ", ".join(f"{k} {v['detail']}" for k, v in report["checks"].items()))


def start_and_stop(exe: Path) -> None:
    """Start it the way the shell does and make sure it announces a port and
    then exits on its own once its parent is gone."""
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    started = time.monotonic()
    proc = subprocess.Popen(
        [str(exe), "--port", "0", "--parent-pid", str(parent.pid)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        event = json.loads(line)
        if event.get("event") != "listening" or not event.get("port"):
            raise SystemExit(f"unexpected first line from sidecar: {line!r}")
        print(f"started: port {event['port']} in {time.monotonic() - started:.1f}s")
        parent.kill()
        parent.wait()
        proc.wait(timeout=15)
        print("exited after its parent went away")
    finally:
        if proc.poll() is None:
            proc.kill()
        if parent.poll() is None:
            parent.kill()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true", help="verify the staged binary only")
    parser.add_argument(
        "--arch",
        default=host_arch(),
        choices=["aarch64", "x86_64"],
        help="CPU to build for (x86_64 on an Apple-silicon Mac runs under Rosetta)",
    )
    parser.add_argument(
        "--sign",
        metavar="IDENTITY",
        help=(
            "macOS: codesign the engine with this identity (hardened runtime), "
            "e.g. 'Developer ID Application: …', or '-' for ad hoc"
        ),
    )
    args = parser.parse_args()
    if args.arch != host_arch() and not (sys.platform == "darwin" and args.arch == "x86_64"):
        raise SystemExit(f"can't build {args.arch} on this {host_arch()} machine")

    if args.skip_build:
        exe = STAGE / EXE_NAME
    else:
        python = build_env(args.arch)
        exe = stage(build(python, args.arch))
        record_licences(python, args.arch)
    size_mb = sum(p.stat().st_size for p in STAGE.rglob("*") if p.is_file()) / 1e6
    print(f"staged {exe} ({size_mb:.0f} MB, {args.arch})")
    check_no_gpl_ffmpeg(STAGE)
    if sys.platform == "darwin":
        check_macos_floor(STAGE)
        if args.sign:
            sign_macos(STAGE, args.sign)
    self_check(exe)
    start_and_stop(exe)


if __name__ == "__main__":
    main()
