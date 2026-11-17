"""HTDemucs in the demucs-mlx venv, one window at a time.

The sidecar never imports MLX. This process loads the model once and scores
each 7.8 s window the realtime module sends. Overlap-add stays on the
sidecar side, so a later caller can reuse the same session.

    <demucs-mlx-venv>/bin/python scripts/run_demucs_mlx.py --serve

The venv lives at ~/Library/Application Support/com.churchslides.app/models/demucs-mlx-venv.
Create it with `python3.11 -m venv` there and `pip install 'demucs-mlx[convert]'`.
The first session converts the official htdemucs weights into ~/.cache/demucs-mlx.
After that cache exists, inference does not need the convert extra.

Protocol, after one JSON hello line on stdout:

    uint32 segment
    uint32 valid
    float32[channels, segment]

`valid == 0` closes the process. The reply is

    uint32 valid
    float32[sources, valid]

mono, in the source order announced in the hello. Stdout carries nothing else.
"""

from __future__ import annotations

import contextlib
import json
import struct
import sys

import numpy as np


def _read_exact(n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sys.stdin.buffer.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def _hello(payload: dict) -> None:
    sys.stdout.buffer.write((json.dumps(payload) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def serve() -> None:
    try:
        from demucs_mlx import Separator
        from demucs_mlx.apply_mlx import _forward
        from demucs_mlx.mlx_utils import center_trim
        import mlx.core as mx
    except Exception as exc:  # noqa: BLE001 — reported to the sidecar, then we stop
        _hello({"ready": False, "error": f"demucs-mlx is not installed ({exc})"})
        return

    # Conversion prints a progress log. Stdout is the protocol, so that
    # log goes to stderr. A later run loads the cache and prints nothing.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            separator = Separator(model="htdemucs", shifts=0, overlap=0.25, progress=False)
            model = separator.model
            sr = int(separator.samplerate)
            channels = int(separator.audio_channels)
            segment_s = float(getattr(model, "segment", None) or separator.segment or 7.8)
            segment = int(sr * segment_s)
            sources = [str(name) for name in model.sources]
    except Exception as exc:  # noqa: BLE001
        _hello({"ready": False, "error": str(exc)})
        return

    _hello(
        {
            "ready": True,
            "samplerate": sr,
            "channels": channels,
            "segment": segment,
            "sources": sources,
        }
    )

    while True:
        header = _read_exact(8)
        if header is None:
            return
        n, valid = struct.unpack("<II", header)
        if n == 0 or valid == 0:
            return
        raw = _read_exact(4 * channels * n)
        if raw is None:
            return
        audio = np.frombuffer(raw, dtype="<f4").reshape(channels, n)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                wav = mx.array(np.ascontiguousarray(audio, dtype=np.float32))[None, ...]
                out = _forward(model, wav)
                out = center_trim(out, int(valid))
                mx.eval(out)
                stems = np.asarray(out[0]).mean(axis=1).astype("<f4")
        except Exception as exc:  # noqa: BLE001
            print(str(exc), file=sys.stderr, flush=True)
            return
        if stems.shape != (len(sources), valid):
            print(f"unexpected stem shape {stems.shape}", file=sys.stderr, flush=True)
            return
        sys.stdout.buffer.write(struct.pack("<I", valid))
        sys.stdout.buffer.write(np.ascontiguousarray(stems, dtype="<f4").tobytes())
        sys.stdout.buffer.flush()


def main() -> None:
    if "--serve" not in sys.argv:
        raise SystemExit("usage: run_demucs_mlx.py --serve")
    serve()


if __name__ == "__main__":
    main()
