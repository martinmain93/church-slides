# PyInstaller spec for the frozen sidecar. Build with `scripts/build_sidecar.py`,
# which also copies the result to where the Tauri bundle picks it up.
#
# One-folder, not one-file: one-file re-extracts ~300 MB of native libraries to
# a temp directory on every launch (slow, and an antivirus scan each time on
# Windows), and macOS notarisation has to see and sign every dylib, which it
# can't inside a one-file archive.
#
# Nearly every heavy import in `auto_presenter` is deferred into the function
# that needs it, so PyInstaller's static analysis sees none of them. They are
# named here instead; a module missing from this list fails at the moment the
# feature is first used, not at startup — `scripts/build_sidecar.py` runs
# `--self-check` against the frozen binary to catch that at build time.

import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

sys.path.insert(0, SPECPATH)  # noqa: F821 (PyInstaller defines SPECPATH)
from dev_only import DEV_ONLY_MODULES  # noqa: E402


def _ships(module: str) -> bool:
    return not any(module == d or module.startswith(d + ".") for d in DEV_ONLY_MODULES)


# The Apple-silicon build carries MLX (Whisper on the GPU, macOS 14+; see
# scripts/build_sidecar.py, which sets SIDECAR_WITH_MLX). Everywhere else it's
# left out, as is `sherpa_onnx`, which ships its own runtime (see pyproject).
WITH_MLX = os.environ.get("SIDECAR_WITH_MLX") == "1"
QWEN_UNUSED = {"cli", "server", "diarization", "__main__"}
EXCLUDES = [
    "cmudict",  # GPL-3 wrapper; its data ships as auto_presenter/index/data
    *([] if WITH_MLX else ["mlx", "mlx_whisper", "mlx_qwen3_asr"]),
    # mlx_qwen3_asr's command line, web server and speaker diarization: unused
    "mlx_qwen3_asr.cli",
    "mlx_qwen3_asr.server",
    "mlx_qwen3_asr.diarization",
    "mlx_whisper.torch_whisper",  # the one module that imports torch; unused
    "llvmlite",  # real numba's compiler; stubs/numba replaces numba
    "sherpa_onnx",
    "torch",
    "tkinter",
    "matplotlib",
    "IPython",
    "pytest",
    # Dev-only engine modules (packaging/dev_only.py): listed here too, so a
    # stray import can't pull one in.
    *DEV_ONLY_MODULES,
]

# Every engine module ships except the dev-only ones (packaging/dev_only.py:
# the Model Inspector's tools, the eval library, the stdio MCP server).
# auto_presenter.dev (inject_audio and friends) ships, but only answers when
# the engine is started with --dev-tools, which the app never passes in a
# release build (sidecar.rs). The build's performance run needs it: it plays
# the fixture service into this exact binary (scripts/perf_report.py).
hiddenimports = (
    [m for m in collect_submodules("auto_presenter") if _ships(m)]
    + collect_submodules("faster_whisper")
    + [
        "ctranslate2",
        "onnxruntime",
        "sounddevice",
        "_sounddevice_data",
        "tokenizers",
        "rapidfuzz",
        "metaphone",
        "certifi",
        # faster-whisper imports `av` at module level. This resolves to
        # stubs/av, not PyAV: PyAV bundles a GPL build of FFmpeg, and the
        # engine never decodes files (see the stub's docstring).
        "av",
    ]
    # Lead-sheet import (auto_presenter.leadsheet): pdfminer loads its CMap
    # and font modules by name.
    + collect_submodules("pdfminer")
    # error_reports.py imports its integrations by name; the SDK's own
    # internals are listed so a missing one fails the build, not a crash report.
    + collect_submodules("sentry_sdk", filter=lambda m: ".integrations." not in m)
    + [
        "sentry_sdk.integrations.atexit",
        "sentry_sdk.integrations.dedupe",
        "sentry_sdk.integrations.excepthook",
        "sentry_sdk.integrations.logging",
        "sentry_sdk.integrations.threading",
    ]
    + (
        [m for m in collect_submodules("mlx_whisper") if not m.endswith("torch_whisper")]
        + collect_submodules("mlx")
        + [m for m in collect_submodules("mlx_qwen3_asr") if m.rsplit(".", 1)[-1] not in QWEN_UNUSED]
        + ["numba"]  # stubs/numba
        # scipy.signal (mlx_whisper's medfilt) reaches these by name at import
        + collect_submodules("scipy._external.array_api_compat")
        if WITH_MLX
        else []
    )
)

datas = (
    collect_data_files("faster_whisper")  # silero VAD model
    # the CMU pronouncing dictionary's data (BSD-style), read by our own
    # loader: the `cmudict` package that wraps it is GPL-3
    + collect_data_files("auto_presenter.index", includes=["data/*"])
    + collect_data_files("certifi")  # CA bundle for HTTPS
    + collect_data_files("pdfminer")  # character maps for CID fonts (lead sheets)
    + collect_data_files("_sounddevice_data")  # PortAudio on macOS/Windows
    # Packages that look themselves up through `importlib.metadata` at runtime
    # need their dist-info copied too.
    + copy_metadata("faster_whisper")
    + copy_metadata("tokenizers")
    # MLX's Metal shaders (mlx.metallib), mlx_whisper's mel filters and
    # tokenizer files, and mlx_qwen3_asr's mel filters (not its Korean
    # dictionary, which only its aligner's Korean word splitting reads).
    + (
        collect_data_files("mlx")
        + collect_data_files("mlx_whisper")
        + collect_data_files("mlx_qwen3_asr", includes=["assets/mel_filters.npz"])
        if WITH_MLX
        else []
    )
)

# The chord model (BTC, MIT) and the library search model (e5-small-v2, MIT),
# with their licence texts, land in `_internal/models`, where music/btc.py and
# library_search/embedder.py look first in a frozen engine.
# scripts/build_sidecar.py gathers it (it's too big for git) and passes the
# folder in.
MODELS_DIR = os.environ.get("SIDECAR_MODELS_DIR")
if not MODELS_DIR:
    raise SystemExit(
        "SIDECAR_MODELS_DIR is not set: build with scripts/build_sidecar.py, "
        "which stages the chord model"
    )
for required in (
    "chords/btc_chords.onnx",
    "chords/btc_chords.json",
    "search/model.onnx",
    "search/tokenizer.json",
    # the next-line check's phone models: MLX builds only, the ones that run it
    *(["next_line/next_line_head.npz", "next_line/next_line_head_qwen.npz"] if WITH_MLX else []),
):
    if not (Path(MODELS_DIR) / required).is_file():
        raise SystemExit(f"missing bundled model {required} in {MODELS_DIR}")
datas += [
    (str(f), str(Path("models") / f.parent.relative_to(MODELS_DIR)))
    for f in sorted(Path(MODELS_DIR).rglob("*"))
    if f.is_file()
]

binaries = (
    collect_dynamic_libs("ctranslate2")
    + collect_dynamic_libs("onnxruntime")
    + collect_dynamic_libs("_sounddevice_data")
    + (collect_dynamic_libs("mlx") if WITH_MLX else [])
)

a = Analysis(
    ["entry.py"],
    # stubs/ first: its `av` (always) and `numba` (MLX build) stand in for the
    # real packages (see their docstrings).
    pathex=["stubs", ".."],
    # hooks/hook-av.py overrides the stock hook, which would collect PyAV.
    hookspath=["hooks"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=EXCLUDES,
    noarchive=False,
)

# Nothing from FFmpeg may ship: PyAV's copy is GPL (see stubs/av). If anything
# pulls a libav*/libsw*/x264/x265 library in, fail here rather than release it.
_FFMPEG = re.compile(r"(^|[/\\])(lib)?(avcodec|avformat|avutil|avdevice|avfilter|swresample|swscale|postproc|x264|x265)\b")
_ffmpeg = sorted({
    dest
    for dest, _src, _kind in a.binaries + a.datas
    if _FFMPEG.search(dest) or re.split(r"[/\\]", dest)[0] in ("av", "av.libs")
})
_real_av = sorted(name for name, src, _kind in a.pure if name.split(".")[0] == "av" and "stubs" not in src)
if _ffmpeg or _real_av:
    raise SystemExit(f"FFmpeg/PyAV would be bundled (GPL): {(_ffmpeg + _real_av)[:10]}")
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    # UTF-8 mode, so text files read and write the same on Windows as on a Mac
    # (see self_check._utf8_mode, which fails the build without it).
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="auto-presenter-sidecar",
    console=True,  # stdout is the port-announcement channel the shell reads
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="sidecar")
