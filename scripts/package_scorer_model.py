"""Package an exported ONNX span-scorer model for download.

    python3 scripts/package_scorer_model.py base.en

Zips build/onnx/<model> (from scripts/export_whisper_scorer_onnx.py) with
the Whisper weights' licence (MIT, OpenAI) into
build/onnx-dist/church-slides-scorer-<model>-f<format>.zip, and prints the
SHA-256 and size that `follower.scorer_models.CPU_SCORER` pins. The engine
refuses a download whose checksum differs, so publishing a new file means
updating that entry in the same commit.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ("encoder.onnx", "scorer.onnx", "tokenizer.json", "meta.json")

WHISPER_LICENSE = """MIT License

Copyright (c) 2022 OpenAI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

README = """Church Slides span-scorer model ({model}, export format {fmt})

OpenAI's Whisper {model} weights (MIT, see LICENSE-whisper.txt), exported to
ONNX by scripts/export_whisper_scorer_onnx.py for the app's live following on
computers without Apple silicon: an encoder for a 6 s window and a decoder
that scores candidate lyric lines against it.
"""


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "base.en"
    src = ROOT / "build" / "onnx" / model
    meta = json.loads((src / "meta.json").read_text())
    fmt = meta["format"]
    out = ROOT / "build" / "onnx-dist" / f"church-slides-scorer-{model}-f{fmt}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in FILES:
            z.write(src / name, name)
        z.writestr("LICENSE-whisper.txt", WHISPER_LICENSE)
        z.writestr("README.txt", README.format(model=model, fmt=fmt))
    data = out.read_bytes()
    print(f"{out}\n  name   {model}-f{fmt}\n  sha256 {hashlib.sha256(data).hexdigest()}\n  bytes  {len(data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
