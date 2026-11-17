"""Compare offline-regenerated scored rows with a live trace's (same win_end)."""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag_live_trace as d
import multi_replay as mr
import scorer_cache as scc

name = sys.argv[1]
window = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
live = [r for r in d.load_trace(mr.CASES[name][1]) if r["e"] == "scored"]
off = [json.loads(x) for x in scc.cache_path(name, window, 2, 6, True).read_text().splitlines()]
assert len(live) == len(off), (len(live), len(off))
ds, dt, best_same, corr, comp, lat = [], [], 0, [], [], []
for a, b in zip(live, off):
    assert abs(a["win_end"] - b["win_end"]) < 1e-6 and len(a["scores"]) == len(b["scores"])
    x, y = np.array(a["scores"]), np.array(b["scores"])
    ds.append(np.abs(x - y).max()); corr.append(np.corrcoef(x, y)[0, 1])
    best_same += int(np.argmax(x) == np.argmax(y))
    dt.append(b["t"] - a["t"]); comp.append(b["compute"]); lat.append(a["arr"] - a["win_end"])
p = lambda v: np.percentile(v, [10, 50, 90]).round(3)
print(f"{name}: {len(live)} steps; max|dscore| p10/50/90 {p(ds)}; corr {p(corr)}; argmax same {best_same/len(live):.2%}")
print(f"  t diff (offline-live) p10/50/90 {p(dt)}; |dt|>0.1: {np.mean(np.abs(dt) > 0.1):.2%}")
print(f"  compute offline {p(comp)} vs live arr-win_end {p(lat)}")
