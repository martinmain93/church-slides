"""Does this install — usually a frozen build — have everything it needs?

Nearly every heavy dependency is imported inside the function that uses it, so
a module left out of a PyInstaller bundle doesn't fail at startup: it fails the
first time someone starts a service. This imports each one up front, loads the
native libraries behind them, and checks the data files they read, so the
failure happens on the build machine instead.

No network, no model download, no audio stream — it must pass offline.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


def _faster_whisper() -> str:
    import faster_whisper

    vad = Path(faster_whisper.__file__).parent / "assets"
    if not any(vad.glob("silero_vad*.onnx")):
        raise FileNotFoundError(f"no silero VAD model in {vad}")
    return faster_whisper.__version__


def _ctranslate2() -> str:
    import ctranslate2

    ctranslate2.get_cuda_device_count()  # forces the native library to load
    return ctranslate2.__version__


def _onnxruntime() -> str:
    import onnxruntime

    onnxruntime.get_available_providers()
    return onnxruntime.__version__


def _sounddevice() -> str:
    import sounddevice

    # Loading the module loads PortAudio; listing devices is harmless and
    # proves the library works, even on a machine with no microphone.
    sounddevice.query_devices()
    return sounddevice.__version__


def _cmudict() -> str:
    from auto_presenter.index.phonemic import load_cmudict

    if not load_cmudict().get("amazing"):
        raise LookupError("cmudict loaded but empty")
    return "ok"


def _tokenizers() -> str:
    import tokenizers

    return tokenizers.__version__


def _certifi() -> str:
    import certifi

    if not Path(certifi.where()).is_file():
        raise FileNotFoundError(certifi.where())
    return certifi.__version__


def _leadsheet() -> str:
    """Lead-sheet import: pdfminer reads a small PDF end to end (paths,
    graphics-state line width, text), and its bundled character maps — data
    files a frozen build must carry — load."""
    from pdfminer.cmapdb import CMapDB

    from auto_presenter.leadsheet import parse_lead_sheet
    from auto_presenter.leadsheet.pdfread import read_pages

    content = b"/G1 gs 72 600 m 540 600 l S BT /F1 12 Tf 72 700 Td (Amazing grace) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> /ExtGState << /G1 << /LW 0.6 >> >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        pdf += b"%010d 00000 n \n" % off
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)

    (page,) = read_pages(bytes(pdf))
    width = page.drawings[0]["width"] or 0 if page.drawings else 0
    if [s["text"] for s in page.spans] != ["Amazing grace"] or abs(width - 0.6) > 1e-6:
        raise RuntimeError(f"pdfminer read the test page wrongly: {page.spans} {page.drawings}")
    if parse_lead_sheet(bytes(pdf)).is_lead_sheet:
        raise RuntimeError("a text-only page was taken for a lead sheet")
    CMapDB.get_cmap("UniJIS-UCS2-H")  # a bundled data file (pdfminer/cmap)
    import pdfminer

    return pdfminer.__version__


def _server() -> str:
    import auto_presenter.server  # noqa: F401

    return "ok"


def _utf8_mode() -> str:
    # Lyrics, recordings and settings are read and written as text without an
    # explicit encoding in places. On Windows the default is the ANSI code
    # page, which can't hold every lyric (or read back what UTF-8 wrote), so
    # the frozen engine runs in UTF-8 mode (sidecar.spec's "X utf8").
    if getattr(sys, "frozen", False) and not sys.flags.utf8_mode:
        raise RuntimeError("the frozen engine isn't in UTF-8 mode")
    return "on" if sys.flags.utf8_mode else "off (not frozen)"


def _mlx() -> str:
    """The Apple-silicon engine's GPU path, where it's meant to be there:
    loads MLX, runs a kernel on the GPU (the Metal shaders are bundled data),
    imports mlx_whisper and mlx_qwen3_asr (with its mel filters, bundled
    data), and reads both next-line phone models. Skipped elsewhere and on
    macOS before 14."""
    import platform

    if sys.platform != "darwin" or platform.machine() != "arm64":
        return "n/a (not Apple silicon)"
    if int(platform.mac_ver()[0].split(".")[0]) < 14:
        return "n/a (macOS before 14)"
    try:
        import mlx.core as mx
    except ImportError:
        if getattr(sys, "frozen", False):
            raise
        return "not installed"
    total = mx.sum(mx.arange(10, dtype=mx.float32) * 2, stream=mx.gpu)
    mx.eval(total)
    if float(total.item()) != 90.0:
        raise RuntimeError(f"MLX GPU arithmetic is wrong: {total.item()}")
    import mlx_qwen3_asr
    import mlx_whisper
    import numpy as np
    from mlx_qwen3_asr.audio import compute_features

    compute_features(np.zeros(16000, np.float32))  # reads assets/mel_filters.npz
    from auto_presenter.follower import next_line

    for name in (next_line.MODEL_FILE, next_line.QWEN_MODEL_FILE):
        if next_line.load_head(name) is None and getattr(sys, "frozen", False):
            raise RuntimeError(f"next-line model {name} isn't bundled")
    return (
        f"{mx.__version__} (gpu), mlx_whisper {mlx_whisper.__version__}, "
        f"mlx_qwen3_asr {mlx_qwen3_asr.__version__}"
    )


def _error_reports() -> str:
    """The SDK and every integration `error_reports.init` names, as frozen."""
    import sentry_sdk
    from sentry_sdk.integrations import (  # noqa: F401
        atexit,
        dedupe,
        excepthook,
        logging,
        threading,
    )

    return sentry_sdk.VERSION


def _test_tone(seconds: float) -> np.ndarray:
    import numpy as np

    t = np.arange(int(16000 * seconds), dtype=np.float32) / 16000
    # A C major triad, so the chord model has something musical to chew on.
    tone = np.zeros_like(t)
    for f in (261.6, 329.6, 392.0):
        tone += 0.2 * np.sin(2 * np.pi * f * t)
    return tone


def _chord_model() -> str:
    """BTC (chords) loads and runs. A frozen engine must carry its own copy;
    a source checkout reports where it looked and moves on."""
    from auto_presenter.music.btc import BtcChordBackend, bundled_model_dir

    backend = BtcChordBackend()
    if not backend.available:
        if getattr(sys, "frozen", False):
            raise FileNotFoundError(f"BTC chord model {backend.status} in {backend.model_dir}")
        return f"{backend.status} (not frozen; {backend.model_dir})"
    if getattr(sys, "frozen", False) and backend.model_dir != bundled_model_dir():
        raise RuntimeError(f"loaded BTC from {backend.model_dir}, not the bundled copy")
    probs = backend.predict(_test_tone(backend.window_seconds + 1.0))
    if probs is None or len(probs) != len(backend.labels):
        raise RuntimeError("BTC returned no prediction for a full block")
    return f"btc ({backend.model_dir})"


def _search_model() -> str:
    """Library search's sentence model loads and ranks by meaning. A frozen
    engine must carry its own copy; a source checkout reports and moves on."""
    from auto_presenter.library_search.embedder import (
        Embedder,
        bundled_model_dir,
        default_model_dir,
    )

    model_dir = default_model_dir()
    if not (model_dir / "model.onnx").is_file():
        if getattr(sys, "frozen", False):
            raise FileNotFoundError(f"search model missing in {model_dir}")
        return f"not installed (not frozen; {model_dir})"
    if getattr(sys, "frozen", False) and model_dir != bundled_model_dir():
        raise RuntimeError(f"loaded the search model from {model_dir}, not the bundled copy")
    emb = Embedder(model_dir)
    q = emb.queries(["songs for Christmas"])[0]
    p = emb.passages(
        ["Silent night, holy night, Christ the Saviour is born", "Nothing but the blood of Jesus"]
    )
    if not float(p[0] @ q) > float(p[1] @ q):
        raise RuntimeError("search model ranked a passion hymn above a carol for 'Christmas'")
    return f"e5-small-v2 ({model_dir})"


def _no_gpl_ffmpeg() -> str:
    """PyAV bundles a GPL build of FFmpeg, so a frozen engine carries
    packaging/stubs/av instead, and no FFmpeg library at all."""
    import av

    if not getattr(sys, "frozen", False):
        return "n/a (not frozen)"
    if not getattr(av, "IS_STUB", False):
        raise RuntimeError(f"the real PyAV is bundled: {getattr(av, '__file__', av)}")
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    names = ("avcodec", "avformat", "avutil", "avdevice", "avfilter", "swresample", "swscale",
             "postproc", "x264", "x265")
    found = [p.name for p in root.rglob("*") if p.is_file() and
             any(p.name.startswith((n, "lib" + n)) for n in names)]
    if found:
        raise RuntimeError(f"FFmpeg libraries bundled: {found[:5]}")
    return "stubbed"


CHECKS: dict[str, Callable[[], str]] = {
    "utf8_mode": _utf8_mode,
    "server": _server,
    "faster_whisper": _faster_whisper,
    "mlx": _mlx,
    "ctranslate2": _ctranslate2,
    "onnxruntime": _onnxruntime,
    "sounddevice": _sounddevice,
    "cmudict": _cmudict,
    "tokenizers": _tokenizers,
    "certifi": _certifi,
    "leadsheet": _leadsheet,
    "error_reports": _error_reports,
    "chord_model": _chord_model,
    "search_model": _search_model,
    "no_gpl_ffmpeg": _no_gpl_ffmpeg,
}


def _read_wav_16k(path: str) -> np.ndarray:
    """A 16 kHz 16-bit mono WAV as float32 — what the engine hands Whisper.
    Not faster-whisper's file decoder: that needs PyAV, which isn't bundled."""
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getsampwidth(), w.getnchannels()) != (16000, 2, 1):
            raise ValueError(f"{path}: expected 16 kHz 16-bit mono")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _transcribe(audio: str) -> str:
    """Run real inference. Offline only: the model must already be cached."""
    from faster_whisper import WhisperModel

    model = WhisperModel("tiny.en", device="cpu", compute_type="int8", local_files_only=True)
    segments, _ = model.transcribe(_read_wav_16k(audio), beam_size=1)
    text = " ".join(s.text.strip() for s in segments).strip()
    if not text:
        raise ValueError("transcription came back empty")
    return text


def run_self_check(audio: str | None = None) -> int:
    checks = dict(CHECKS)
    if audio:
        checks["transcribe"] = lambda: _transcribe(audio)
    results: dict[str, dict[str, object]] = {}
    for name, check in checks.items():
        try:
            results[name] = {"ok": True, "detail": check()}
        except Exception as e:  # report every failure, not just the first
            results[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    ok = all(r["ok"] for r in results.values())
    print(
        json.dumps(
            {"event": "self_check", "ok": ok, "platform": sys.platform, "checks": results},
            indent=2,
        ),
        flush=True,
    )
    return 0 if ok else 1
