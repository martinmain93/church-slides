"""Small ONNX sentence embedders (bge-small, e5-small), CPU only, no torch."""
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from huggingface_hub import snapshot_download
from tokenizers import Tokenizer

MODELS = {
    # pooling, query prefix, passage prefix
    "bge": ("Xenova/bge-small-en-v1.5", "cls", "Represent this sentence for searching relevant passages: ", ""),
    "e5": ("Xenova/e5-small-v2", "mean", "query: ", "passage: "),
    "e5q": ("Xenova/e5-small-v2", "mean", "query: ", "passage: "),
}


class Embedder:
    def __init__(self, name):
        repo, self.pool, self.qp, self.pp = MODELS[name]
        f = "onnx/model_quantized.onnx" if name.endswith("q") else "onnx/model.onnx"
        d = Path(snapshot_download(repo, allow_patterns=[f, "tokenizer.json", "config.json"]))
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tok.enable_truncation(256)
        self.tok.enable_padding()
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(str(d / f), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.size_mb = (d / f).stat().st_size / 1e6

    def _run(self, texts, bs=32):
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        res = self._run_ordered([texts[i] for i in order], bs)
        out = np.empty_like(res)
        out[order] = res
        return out

    def _run_ordered(self, texts, bs):
        out = []
        for i in range(0, len(texts), bs):
            enc = self.tok.encode_batch(texts[i : i + bs])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            h = self.sess.run(None, feed)[0]
            if self.pool == "cls":
                v = h[:, 0]
            else:
                m = mask[..., None].astype(np.float32)
                v = (h * m).sum(1) / m.sum(1)
            out.append(v / np.linalg.norm(v, axis=1, keepdims=True))
        return np.vstack(out).astype(np.float32)

    def passages(self, texts):
        return self._run([self.pp + t for t in texts])

    def query(self, text):
        return self._run([self.qp + text])[0]


def build_index(emb, songs):
    t0 = time.time()
    flat, owner = [], []
    for si, s in enumerate(songs):
        for sl in s["slides"]:
            flat.append(sl)
            owner.append(si)
    V = emb.passages(flat)
    # whole-song vectors (first 256 tokens of the deduped lyrics) for the slide-vs-song comparison
    W = emb.passages([" / ".join(s["slides"]) for s in songs])
    return dict(V=V, owner=np.array(owner), W=W, secs=time.time() - t0, n=len(flat))
