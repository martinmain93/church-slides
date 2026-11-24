"""Make step_bench.py's inputs on an Apple-silicon Mac (needs mlx_qwen3_asr).

From 6 s of public-domain audio (float32 mono 16 kHz raw): the Qwen3-ASR mel
spectrograms of the whole 6 s and of its last 2 s, and the prompt's token ids
around the audio tokens.

    cd sidecar && uv run --no-sync python ../research/spikes/qwen_cpu/make_inputs.py clip.f32
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent


def main() -> None:
    from mlx_qwen3_asr.audio import compute_features
    from mlx_qwen3_asr.tokenizer import _TokenizerHolder

    from auto_presenter.asr.mlx_backend import local_snapshot
    from auto_presenter.asr.qwen_backend import QWEN_REPO, QWEN_REVISION

    audio = np.fromfile(sys.argv[1], dtype=np.float32)
    assert len(audio) == 6 * 16000, len(audio)
    for name, part in (("mel_6s", audio), ("mel_2s", audio[-2 * 16000 :])):
        mel, _ = compute_features(part)
        np.save(HERE / f"{name}.npy", np.array(mel, dtype=np.float32))
    tok = _TokenizerHolder.get(local_snapshot(QWEN_REPO, QWEN_REVISION))
    ids = list(tok.build_prompt_tokens(n_audio_tokens=7, language="English", context=""))
    pad = max(set(ids), key=ids.count)
    i = ids.index(pad)
    assert ids[i : i + 7] == [pad] * 7
    (HERE / "prompt.json").write_text(
        json.dumps({"before": ids[:i], "audio_pad": pad, "after": ids[i + 7 :]})
    )


if __name__ == "__main__":
    main()
