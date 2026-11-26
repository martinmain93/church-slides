"""MLX (app) vs PyTorch (Kaggle) Qwen audio features on public-song windows."""
import sys, numpy as np
from pathlib import Path
REPO = Path.home() / "Development/church-slides/.claude/worktrees/practical-cartwright-edb24b"
sys.path.insert(0, str(REPO / "research/spikes/qwen_improve"))
mode = sys.argv[1]
from common import SR, audio_of, load_clip
x = audio_of(load_clip("Avercage_-_Embers"))
clips = [x[int(s * SR): int((s + 2.5) * SR)] for s in (31.0, 47.3, 62.9, 80.1)]
out = Path(__file__).parent / f"parity_{mode}.npy"
if mode == "mlx":
    import mlx.core as mx
    from mlx_qwen3_asr.load_models import load_model
    from mlx_qwen3_asr.audio import compute_features
    from auto_presenter.asr.mlx_backend import local_snapshot
    m, _ = load_model(local_snapshot("Qwen/Qwen3-ASR-0.6B"))
    F = []
    for c in clips:
        mel, lens = compute_features(c.astype(np.float32)); f, _ = m.audio_tower(mel.astype(mx.float16), lens)
        F.append(np.asarray(f[0].astype(mx.float32)))
    np.save(out, np.stack(F))
else:
    sys.path.insert(0, str(Path(__file__).parent)); from torch_feats import QwenFeats
    q = QwenFeats(); np.save(out, np.stack([q(c) for c in clips]))
    a = np.load(Path(__file__).parent / "parity_mlx.npy"); b = np.load(out)
    print("shapes", a.shape, b.shape)
    for i in range(len(a)):
        cos = (a[i] * b[i]).sum(-1) / (np.linalg.norm(a[i], axis=-1) * np.linalg.norm(b[i], axis=-1))
        print(f"window {i}: cosine mean {cos.mean():.4f} min {cos.min():.4f}  rel err {np.linalg.norm(a[i]-b[i])/np.linalg.norm(a[i]):.3f}")
