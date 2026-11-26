"""Same phone head, trained on Whisper or Qwen encoder features of the same windows.
    .venv/bin/python train.py wh|qw [epochs]"""
import json, sys, time, numpy as np, torch, torch.nn as nn
torch.manual_seed(0)
F = sys.argv[1]; E = int(sys.argv[2]) if len(sys.argv) > 2 else 25
X = np.load(f"{F}_own_x.npy").astype(np.float32); Y = json.load(open("own_y.json"))
T, M = Y["targets"], Y["meta"]
recs = sorted({m[0] for m in M}); rng = np.random.default_rng(0)
val = set(rng.choice(recs, max(2, len(recs) // 10), replace=False))
tr = [i for i, m in enumerate(M) if m[0] not in val]; va = [i for i, m in enumerate(M) if m[0] in val]
class Head(nn.Module):
    def __init__(s, d, h=256, n=41, layers=2):
        super().__init__()
        s.register_buffer("mu", torch.zeros(d)); s.register_buffer("sd", torch.ones(d))
        s.conv = nn.Sequential(nn.Conv1d(d, h, 5, padding=2), nn.GELU(), nn.Dropout(0.1))
        s.rnn = nn.GRU(h, h // 2, num_layers=layers, batch_first=True, bidirectional=True)
        s.out = nn.Linear(h, n)
    def forward(s, x):
        x = s.conv(((x - s.mu) / s.sd).transpose(1, 2)).transpose(1, 2)
        return s.out(s.rnn(x)[0])
def batch(idx):
    x = torch.from_numpy(X[idx]); ys = [T[i] for i in idx]
    return x, torch.tensor([p for y in ys for p in y], dtype=torch.long), torch.tensor([len(y) for y in ys])
def per(out, tgt, tl_):
    g = out.argmax(-1).numpy(); off = 0; errs = toks = 0
    for k, L_ in enumerate(tl_.tolist()):
        ref = tgt[off:off + L_].tolist(); off += L_
        hyp = [int(v) for j, v in enumerate(g[k]) if v != 0 and (j == 0 or v != g[k][j - 1])]
        d = list(range(len(hyp) + 1))
        for r in ref:
            nd = [d[0] + 1] + [0] * len(hyp)
            for j, h in enumerate(hyp): nd[j + 1] = min(d[j + 1] + 1, nd[j] + 1, d[j] + (r != h))
            d = nd
        errs += d[-1]; toks += len(ref)
    return errs, toks
if __name__ == "__main__":
    m = Head(X.shape[-1]); xs = torch.from_numpy(X[tr[:1024]]).reshape(-1, X.shape[-1])
    m.mu.copy_(xs.mean(0)); m.sd.copy_(xs.std(0) + 1e-3)
    print(F, "frames", X.shape[1], "dim", X.shape[2], len(tr), "train", len(va), "val", flush=True)
    opt = torch.optim.AdamW(m.parameters(), 1e-3, weight_decay=1e-2); ctc = nn.CTCLoss(blank=0, zero_infinity=True)
    best = 9.0
    for ep in range(E):
        m.train(); rng.shuffle(tr); st = time.time(); tl = []
        for b in range(0, len(tr), 64):
            idx = sorted(tr[b:b + 64]); x, tgt, tl_ = batch(idx)
            loss = ctc(m(x).log_softmax(-1).transpose(0, 1), tgt, torch.full((x.shape[0],), x.shape[1]), tl_)
            opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(m.parameters(), 5); opt.step(); tl.append(loss.item())
        m.eval(); errs = toks = 0
        with torch.no_grad():
            for b in range(0, len(va), 256):
                x, tgt, tl_ = batch(va[b:b + 256]); e, t = per(m(x), tgt, tl_); errs += e; toks += t
        pe = errs / max(toks, 1)
        print(f"epoch {ep} train {np.mean(tl):.3f} phone error {pe:.1%} ({time.time() - st:.0f}s)", flush=True)
        if pe < best: best = pe; torch.save(m.state_dict(), f"{F}_best.pt")
    print(F, "best phone error", f"{best:.1%}")
