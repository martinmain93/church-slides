"""Kaggle GPU job: the next-line phone head on Qwen3-ASR 0.6B audio features (FMA).

Input dataset fma-phone-windows: windows.json = [[stem, start_s, phone targets], ...],
the same FMA windows the shipped (Whisper small.en) head was trained on.
1. Re-fetch each song's FMA shard, cut the 2.5 s windows, encode them with Qwen3-ASR's
   audio tower (official qwen-asr package; matches the app's MLX features: cosine 1.000).
   Saves /kaggle/working/qfeat.npy (N, 33, 1024 fp16) + qfeat_rows.json.
2. Train CTC phone heads (conv + 2-layer bi-GRU, the shipped shape) on them, FMA songs
   held out by song (same split rule as fma-finetune). Arms: h512, h256.
   Logs held-out loss + phone error per epoch; saves q<arm>_best.pt + q<arm>.json.
"""
import collections, glob, json, os, subprocess, sys, time
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "qwen-asr"], check=True)
import numpy as np, torch, torch.nn as nn
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download
from transformers import WhisperFeatureExtractor
from qwen_asr.core.transformers_backend.modeling_qwen3_asr import Qwen3ASRForConditionalGeneration

WIN, OUT, dev, BUDGET = 2.5, "/kaggle/working", "cuda", 11.3 * 3600
t0 = time.time(); torch.manual_seed(0); rng = np.random.default_rng(0)
W = json.load(open(glob.glob("/kaggle/input/**/windows.json", recursive=True)[0]))
log = open(f"{OUT}/log.txt", "a")
def say(m):
    m = f"[{(time.time() - t0) / 60:.0f} min] {m}"; print(m, flush=True); log.write(m + "\n"); log.flush()

# ------------------------------------------------------------------ features
m = Qwen3ASRForConditionalGeneration.from_pretrained("Qwen/Qwen3-ASR-0.6B", torch_dtype=torch.float32)
tower = m.thinker.audio_tower.to(dev).eval(); del m
fe = WhisperFeatureExtractor.from_pretrained("Qwen/Qwen3-ASR-0.6B")
NQ = 33

@torch.no_grad()
def encode(clip):
    a = fe(clip, sampling_rate=16000, padding=True, truncation=False, return_attention_mask=True, return_tensors="pt")
    n = int(a["attention_mask"].sum())
    out = tower(a["input_features"][0, :, :n].to(dev), feature_lens=torch.tensor([n], device=dev)).last_hidden_state
    f = out.float().cpu().numpy().astype(np.float16)
    return f[:NQ] if len(f) >= NQ else np.concatenate([f, np.zeros((NQ - len(f), f.shape[1]), np.float16)])

def decode(b):
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
                       input=b, capture_output=True)
    return np.frombuffer(r.stdout, np.float32).copy() if not r.returncode else None

prev = glob.glob("/kaggle/input/**/qfeat.npy", recursive=True)
if prev:
    C = np.load(prev[0]); keep = json.load(open(prev[0].replace("qfeat.npy", "qfeat_rows.json")))
else:
    by_shard = collections.defaultdict(lambda: collections.defaultdict(list))
    for k, (stem, s, _t) in enumerate(W):
        by_shard[int(stem[1:4])][int(stem[5:])].append((k, s))
    C = np.zeros((len(W), NQ, 1024), np.float16); got = np.zeros(len(W), bool)
    FETCH_BUDGET = 8.0 * 3600  # leave >3 h for training
    for n, (shard, songs) in enumerate(sorted(by_shard.items())):
        if time.time() - t0 > FETCH_BUDGET:
            say(f"fetch budget hit after {n} shards"); break
        try:
            p = hf_hub_download("benjamin-paine/free-music-archive-full", f"data/train-{shard:05d}-of-00972.parquet",
                                repo_type="dataset", cache_dir="/tmp/hf")
        except Exception as e:
            say(f"shard {shard} failed: {e}"); continue
        tab = pq.read_table(p, columns=["audio"])
        for i, wins in songs.items():
            x = decode(tab.slice(i, 1).to_pylist()[0]["audio"]["bytes"])
            if x is None: continue
            for k, s in wins:
                clip = x[int(s * 16000): int(s * 16000) + int(WIN * 16000)]
                if len(clip) < int(WIN * 16000) // 2: continue
                C[k] = encode(clip); got[k] = True
        os.remove(os.path.realpath(p))
        if n % 10 == 0: say(f"shard {n + 1}/{len(by_shard)}, {int(got.sum())} windows")
    keep = [int(k) for k in np.flatnonzero(got)]
    np.save(f"{OUT}/qfeat.npy", C[keep]); json.dump(keep, open(f"{OUT}/qfeat_rows.json", "w"))
    C = C[keep]
say(f"features ready: {len(keep)} of {len(W)} windows")
del tower; torch.cuda.empty_cache()

# ------------------------------------------------------------------ heads
pos = {k: j for j, k in enumerate(keep)}
songs = sorted({W[k][0] for k in keep}); val_songs = set(rng.choice(songs, len(songs) // 20, replace=False))
tr = [k for k in keep if W[k][0] not in val_songs]; va = [k for k in keep if W[k][0] in val_songs]
F = torch.from_numpy(C).to(dev)
mu = F[: 4096].float().reshape(-1, F.shape[-1]).mean(0); sd = F[: 4096].float().reshape(-1, F.shape[-1]).std(0) + 1e-3

class Head(nn.Module):
    def __init__(s, d=1024, h=512, n=41, layers=2):
        super().__init__()
        s.register_buffer("mu", mu.clone()); s.register_buffer("sd", sd.clone())
        s.conv = nn.Sequential(nn.Conv1d(d, h, 5, padding=2), nn.GELU(), nn.Dropout(0.1))
        s.rnn = nn.GRU(h, h // 2, num_layers=layers, batch_first=True, bidirectional=True)
        s.out = nn.Linear(h, n)
    def forward(s, x):
        x = s.conv(((x.float() - s.mu) / s.sd).transpose(1, 2)).transpose(1, 2)
        return s.out(s.rnn(x)[0])

def augment(x):  # light SpecAugment on the features: 2 time masks <=2 frames, 2 channel masks <=64
    B, T, D = x.shape
    for _ in range(2):
        w = torch.randint(0, 3, (B,), device=dev); s0 = (torch.rand(B, device=dev) * (T - w)).long()
        t = torch.arange(T, device=dev)[None]; x = x.masked_fill(((t >= s0[:, None]) & (t < (s0 + w)[:, None]))[..., None], 0)
    for _ in range(2):
        w = torch.randint(0, 65, (B,), device=dev); s0 = (torch.rand(B, device=dev) * (D - w)).long()
        d = torch.arange(D, device=dev)[None]; x = x.masked_fill(((d >= s0[:, None]) & (d < (s0 + w)[:, None]))[:, None, :], 0)
    return x

def batch(idx):
    x = F[[pos[k] for k in idx]]; ys = [W[k][2] for k in idx]
    return x, torch.tensor([p for y in ys for p in y], dtype=torch.long), torch.tensor([len(y) for y in ys])

def per(out, tgt, tl_):
    g = out.argmax(-1).cpu().numpy(); off = 0; errs = toks = 0
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

say(f"{len(tr)} train / {len(va)} val windows ({len(val_songs)} held-out songs)")
ctc = nn.CTCLoss(blank=0, zero_infinity=True)
for arm, h, E in (("h512", 512, 40), ("h256", 256, 40)):
    m = Head(h=h).to(dev); json.dump({"h": h, "layers": 2, "d": 1024, "frames": NQ}, open(f"{OUT}/q{arm}.json", "w"))
    opt = torch.optim.AdamW(m.parameters(), 1e-3, weight_decay=1e-2); best = 9.0
    for ep in range(E):
        if time.time() - t0 > BUDGET: say("time budget hit"); break
        m.train(); order = list(tr); rng.shuffle(order); tl = []
        for b in range(0, len(order), 128):
            x, tgt, tl_ = batch(sorted(order[b:b + 128])); x = augment(x)
            lp = m(x).log_softmax(-1).transpose(0, 1)
            loss = ctc(lp.float().cpu(), tgt, torch.full((x.shape[0],), x.shape[1]), tl_)
            opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(m.parameters(), 5); opt.step(); tl.append(loss.item())
        m.eval(); errs = toks = 0; vl = []
        with torch.no_grad():
            for b in range(0, len(va), 256):
                x, tgt, tl_ = batch(va[b:b + 256]); out = m(x)
                vl.append(ctc(out.log_softmax(-1).transpose(0, 1).float().cpu(), tgt, torch.full((x.shape[0],), x.shape[1]), tl_).item())
                e, t = per(out, tgt, tl_); errs += e; toks += t
        pe = errs / max(toks, 1)
        say(f"{arm} epoch {ep} train {np.mean(tl):.3f} val {np.mean(vl):.3f} phone error {pe:.1%}")
        if pe < best:
            best = pe; torch.save(m.state_dict(), f"{OUT}/q{arm}_best.pt")
    say(f"{arm} best phone error {best:.1%}")
say("done")
