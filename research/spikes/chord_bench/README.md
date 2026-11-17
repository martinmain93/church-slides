# Model benchmarks (2026-09-08)

Spike benchmarks for `docs/research/MUSIC_MODELS_2026.md`. These need an isolated venv
with PyTorch — deliberately **not** the sidecar, which stays onnxruntime-only.

```bash
uv venv /tmp/spikevenv --python 3.11
VIRTUAL_ENV=/tmp/spikevenv uv pip install librosa torch torchaudio einops \
    rotary-embedding-torch soxr tqdm mir_eval pandas pyyaml pretty_midi
VIRTUAL_ENV=/tmp/spikevenv uv pip install "beat_this@git+https://github.com/CPJKU/beat_this"
git clone --depth 1 https://github.com/jayg996/BTC-ISMIR19.git /tmp/btc
```

BTC is a 2019 repo and does not run on current numpy/PyYAML. Patch the clone
(`np.float` -> `np.float64`, `np.int` -> `np.int64`, `yaml.load(f)` ->
`yaml.safe_load(f)`). Note that BSD `sed` on macOS does not support `\b`, so a
word-boundary substitution silently does nothing — use Python.

| script | what it measures |
|---|---|
| `bench_beat.py` | Beat This! speed (CPU vs MPS), and 16 kHz vs 44.1 kHz agreement |
| `validate2.py` | is the beat grid right? word-onset phase, cue-to-downbeat |
| `bench_btc.py` | BTC speed, over-segmentation, chart cross-match |
| `bench_beatsync.py` | all three chord front ends under one control |

`bench_beat.py` needs full-rate decodes of the source tracks in `hires/`,
produced with ffmpeg at 44.1 kHz mono from the originals in the iTunes library.
