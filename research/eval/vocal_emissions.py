"""Character emissions of the sung vocal, for re-timing bootstrapped cues.

Runs in its own throwaway environment, because torch and Demucs are not (and
should not be) sidecar dependencies. `bootstrap_golden.py` calls it like this:

    uv run --no-project --python 3.11 \
        --with torch==2.5.1 --with torchaudio==2.5.1 --with demucs==4.0.1 \
        --with numpy==2.4.6 \
        python research/eval/vocal_emissions.py <audio.wav> <out.npz>

1. Demucs (htdemucs) separates the vocal from the band. Give it the original
   file when there is one (any format ffmpeg reads): separating the 16 kHz
   mono copy cost cue accuracy on 3 of 4 songs tried (Leader: 0.56 -> 0.44
   cue F1 within 0.3 s), though it still beat Whisper's timing by far.
2. The English wav2vec2 CTC model (WAV2VEC2_ASR_BASE_960H, Apache-2.0) turns
   the vocal into per-frame character log-probabilities, 50 frames/s.

Writes `emissions` (frames x labels, float32), `labels` and `fps` to <out.npz>.
The forced alignment itself lives in `bootstrap_golden.py` (plain numpy), so
the bootstrapper never needs torch.

Why this model, on the vocal stem: on the 20 hand-timed JamendoLyrics songs it
put cues a median 0.06 s from the hand timing against Whisper's 0.37 s. The
multilingual MMS aligner barely reacts to singing and drifted 15-20 s; the
same model on the full mix drifted whole sections on some songs.
"""

from __future__ import annotations

import argparse
import subprocess

import numpy as np
import torch
import torchaudio

SR = 16_000
# wav2vec2's attention is quadratic in length, so long audio goes through in
# overlapping windows; each keeps only its centre so edges never count.
WIN_S, HOP_S = 30.0, 25.0


def load_stereo(path: str, rate: int) -> torch.Tensor:
    """Decode any ffmpeg-readable file to (2, samples) float32 at `rate`.

    ffmpeg is the same decoder `bootstrap_golden.transcode` used to write the
    recording's audio.wav, so the two share a timeline."""
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-vn",
         "-f", "f32le", "-ac", "2", "-ar", str(rate), "-"],
        check=True, capture_output=True,
    ).stdout
    return torch.from_numpy(np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy())


def separate_vocals(path: str) -> torch.Tensor:
    """The vocal stem at 16 kHz mono (same steps as `demucs.separate`)."""
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    model = get_model("htdemucs")
    model.eval()
    wav = load_stereo(path, model.samplerate)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.inference_mode():
        sources = apply_model(
            model, ((wav - mean) / std)[None], device="cpu",
            split=True, overlap=0.25, progress=False,
        )[0]
    vocals = sources[model.sources.index("vocals")] * std + mean
    return torchaudio.functional.resample(vocals.mean(0), model.samplerate, SR)


def emissions(wav: torch.Tensor) -> tuple[np.ndarray, list[str], float]:
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    model = bundle.get_model()
    model.eval()
    labels = [c.lower() for c in bundle.get_labels()]  # '-' blank, '|' word gap
    n = wav.shape[0]
    win, hop = int(WIN_S * SR), int(HOP_S * SR)
    crop = (win - hop) // 2
    pieces, start = [], 0
    with torch.inference_mode():
        while True:
            seg = wav[start : start + win]
            em, _ = model(seg.unsqueeze(0))
            em = torch.log_softmax(em, dim=-1)[0].numpy()
            fps = em.shape[0] / (seg.shape[0] / SR)
            last = start + win >= n
            lo = 0 if start == 0 else round(crop / SR * fps)
            hi = em.shape[0] if last else round((crop + hop) / SR * fps)
            pieces.append(em[lo:hi])
            if last:
                break
            start += hop
    em = np.concatenate(pieces).astype(np.float32)
    return em, labels, em.shape[0] / (n / SR)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("audio")
    ap.add_argument("out")
    a = ap.parse_args()
    vocals = separate_vocals(a.audio)
    em, labels, fps = emissions(vocals)
    np.savez(a.out, emissions=em, labels=np.array(labels), fps=np.float64(fps))
    print(f"{a.out}: {em.shape[0]} frames at {fps:.2f}/s")


if __name__ == "__main__":
    main()
