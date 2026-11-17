# Phase 9 — Optional: real-time vocal separation (Demucs) — SHELVED 2026-09-03

## Status (2026-09-03)

Measured, not built. An MIT-licensed ONNX export of exactly this model
now exists (`StemSplitio/htdemucs-ft-vocals-onnx`), so the PyTorch
blocker this doc assumed is gone. Ran it on two goldens: WER dropped
24.7% -> 18.1%, clearing the bar below. But `htdemucs_ft` needs 7.8 s of
audio context per chunk — it is not causal, chunking is architectural,
not a speed problem — which roughly triples end-to-end latency. Shelved
on that alone. Causal alternatives exist in the literature at ~92 ms
(Band-SCNet, Interspeech 2025) but none has released weights. Full
write-up: `Plan/MUSIC_LITERATURE.md`.

> **Provenance of the WER figures.** They came from a throwaway script run
> against a locally downloaded ONNX export; neither the script nor the model
> is in this repo, so **these two numbers cannot be reproduced from a
> checkout**. They are recorded as the reason the phase was shelved, not as a
> measurement anything else should be tuned against. The conclusion does not
> rest on them: the 7.8 s context requirement is architectural and rules the
> model out regardless of how much WER it recovers. Anyone revisiting this
> should re-measure rather than trust the pair above — and commit the script
> that time.

## Goal

Improve ASR quality on full-mix audio sources (when soundboard vocal-aux-send isn't available). Insert a vocal-isolation stage between audio capture and ASR. **Optional** — only enter this phase if Phase 8 evaluation shows ASR is the bottleneck on full-mix recordings.

## Prerequisites

- Phase 8 complete (production-ready system on clean inputs)
- Eval data showing measurable WER gap between vocal-aux-send recordings and full-mix recordings
- Decision: this phase is justified by data, not speculation

## Scope

**In:**
- Demucs htdemucs streaming wrapper
- Real-time-capable separation (target: < 500 ms added latency, < 1.0 RTF)
- Config flag to toggle separation; auto-detect when input source is already isolated vocal
- GPU detection (MPS, CUDA, CPU fallback with smaller model variant)
- Adapter pattern: `AudioProcessor` between capture and ASR
- Settings UI: enable/disable separation, model size

**Out:**
- Custom-trained separation models
- Multi-source separation (instruments breakdown beyond vocals)
- Any matcher logic changes

## Implementation steps

### 1. Audio processor abstraction

`sidecar/auto_presenter/audio/processor.py`:
```python
class AudioProcessor(Protocol):
    def process(frame: AudioFrame) -> AudioFrame: ...

class IdentityProcessor: ...                # default, no-op
class VocalSeparator(AudioProcessor): ...   # Demucs
```

`sidecar/auto_presenter/audio/capture.py`: extend pipeline to optionally wrap input through a processor before yielding to ASR.

### 2. Demucs wrapper

`sidecar/auto_presenter/audio/separation.py`:
- Use `demucs` package; load `htdemucs_ft` model (best quality, 4-stem); use only the `vocals` stem
- For lower-end hardware: `htdemucs` (smaller, slightly lower quality) or `mdx_extra_q` (much smaller)
- Streaming approach:
  - Maintain rolling 4-second buffer
  - Process new audio with overlap; cross-fade output to avoid edge artifacts
  - Latency budget: < 500 ms

### 3. Device detection + fallback

Same module:
- Auto-detect `torch.cuda.is_available()` → CUDA
- Else `torch.backends.mps.is_available()` → MPS (Apple Silicon)
- Else CPU with `htdemucs_lite` or `mdx_extra_q`
- Log chosen device + model at startup
- If model load fails: emit warning, fall back to `IdentityProcessor`

### 4. Config + auto-bypass

`sidecar/auto_presenter/config.py` (extend):
- `audio.input_source: "vocal_aux" | "full_mix" | "auto"` (already from Phase 4 wizard)
- `audio.use_vocal_separation: bool | "auto"`
- When `input_source == "vocal_aux"`: bypass separation regardless of flag
- When `input_source == "auto"` and `use_vocal_separation == "auto"`: brief diagnostic on session start (10 s) — if input is dominated by vocal frequencies (heuristic on frequency-band energy), assume vocal_aux

### 5. Settings UI

`src/components/settings/AudioSettings.tsx` (extend Phase 4):
- Toggle: "Enhance vocals (uses GPU)" with explanation
- Auto-disabled when input source is vocal_aux
- Model variant picker for advanced users (default: auto)

### 6. Re-run audio wizard prompt

If user changes input source to "microphone" after Phase 9 ships, the wizard now offers vocal separation as an option — show progress while it downloads the model.

## Files to create

| Path | Purpose |
|---|---|
| `sidecar/auto_presenter/audio/processor.py` | Protocol, IdentityProcessor |
| `sidecar/auto_presenter/audio/separation.py` | Demucs wrapper, device detection |
| `sidecar/auto_presenter/config.py` (extend) | Separation config |
| `src/components/settings/AudioSettings.tsx` (extend) | Toggle |
| `tests/sidecar/audio/test_separation.py` | Output shape + content correctness |
| `tests/sidecar/audio/test_separation_latency.py` | RTF + added-latency benchmarks |
| `tests/integration/test_full_mix.py` | WER improvement on full-mix recording |

## Success criteria

- [ ] **WER improvement**: ≥ 5 percentage points on full-mix eval recordings vs no-separation baseline
- [ ] **Latency overhead**: < 500 ms added end-to-end
- [ ] **RTF**: < 0.5 on target GPU; < 1.0 on CPU with smaller model
- [ ] **Falls back gracefully** when Demucs fails to load — pipeline still runs without separation
- [ ] **Auto-bypass works** when input is already isolated vocal source
- [ ] **Wrong-slide rate** on full-mix recordings approaches that of vocal-aux-send recordings (within 1 percentage point)
- [ ] **No regression** on vocal-aux-send recordings (separation correctly bypassed)

## Tests required before phase complete

**Unit:**
- `test_separation.py`: known full-mix clip → output is mono vocal track; output shape matches input
- `test_separation.py`: known vocal-only clip through separator → output ≈ input (separation is roughly idempotent on isolated vocal)

**Performance:**
- `test_separation_latency.py`: 60-second mix → end-to-end pipeline RTF < 1.0 on target hardware
- `test_separation_latency.py`: latency overhead measured at < 500 ms across 100 frames

**Integration:**
- `test_full_mix.py`: replay a full-mix recording through pipeline w/ and w/o separation; WER drops by target margin
- A/B vs Phase 8 on every recording: full-mix improves; vocal-aux-send unchanged

**Manual:**
- Listen to separator output on a real worship recording — does it sound clean? Any musical artifacts that confuse ASR?

## Risks

- **Demucs is heavy.** Mitigation: smaller-model fallback for CPU-only laptops; document hardware requirements; potentially require GPU for full-mix support.
- **Phase artifacts hurt ASR.** Mitigation: A/B compare with and without on every full-mix recording; if ASR worsens, prefer no separation. Some artifacts (especially in harmonized vocals) can confuse ASR more than the original mix.
- **Crossfade boundary artifacts.** Mitigation: tune overlap window; use Demucs' streaming API directly when available rather than rolling our own.
- **Real-time-factor variance** on different hardware. Mitigation: dynamic model selection at startup based on benchmark; surface in config.
- **Justification drift.** Mitigation: skip this phase entirely if Phase 8 metrics already meet your bar — most churches *can* provide vocal-aux-send if asked. Document venue-setup expectations clearly.

## Effort estimate

5–7 days (skip if not justified by Phase 8 eval data).
