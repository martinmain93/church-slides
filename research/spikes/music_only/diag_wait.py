"""The band-waits states, up close: per read-out, band vs screen, belief in waits, vocal activity.

    python research/spikes/music_only/diag_wait.py NAME T0 T1 "ARM" [ITEM]

ARM is a music_replay arm body (e.g. "wait=0.3,vocal=stems"). Prints, every
read-out in [T0, T1]: the band's slide (golden), the slide shown, the share
of belief in wait states, the top slides of the belief at "now" (waits folded
to their slide), and the vocal activity that has arrived for that moment.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr  # noqa: E402
import music_replay as mrp  # noqa: E402
from auto_presenter.follower import online  # noqa: E402


def main() -> int:
    name, t0, t1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    _n, arm = mrp.parse("x:" + (sys.argv[4] if len(sys.argv) > 4 else ""))
    online.LYRIC_HOLD_S = arm["hold"]
    online.HOLD_MIN_SHARE = arm["hms"]
    mrp.apply(arm)
    log = []
    orig = online.OnlineFollower.readout

    def readout(self, wall):
        r = orig(self, wall)
        dump = [float(x) for x in os.environ.get("DUMP_T", "").split(",") if x]
        if any(abs(wall - d) < 0.05 for d in dump):
            # the belief at the checkpoint, re-filtered to now, by state
            a = self.alpha.copy()
            for f in range(self.ck_frame + 1, int(wall * self.fps) + 1):
                a = self._update(self.tr.step(a), f, wall)
            a = a / a.sum()
            flat = np.argsort(a.ravel())[::-1][:10]
            print(f"--- belief at {wall:.1f}:")
            for k in flat:
                ri, c = divmod(int(k), a.shape[1])
                if c >= self.ref.P:
                    g = c - self.ref.P
                    what = f"WAIT after seg{g}(sl{self.ref.seg_slides[g]})"
                else:
                    g = int(self.ref.seg_of[c])
                    what = (f"seg{g}(sl{self.ref.seg_slides[g]})+{c - int(self.ref.jump_targets[g])}"
                            f"{' sung' if self.ref.sung[c] else ' gap'}")
                print(f"     {a.ravel()[k]:.3f}  {what}  {self.ref.rates[ri] * 30:.0f}bpm")
        if t0 <= wall <= t1:
            wait = self.wait_now
            idx = self._slide_idx[: self.ref.P]
            ok = idx >= 0
            m = np.bincount(idx[ok], weights=r.marginal_now[ok], minlength=self._n_slides)
            m = m / max(m.sum(), 1e-12)
            top = np.argsort(m)[::-1][:3]
            v = self._vocal.get(int((wall - 3.0) * self.fps), {}).get("voice")
            c = int(np.argmax(r.marginal_now))
            seg = int(self.ref.seg_of[c])
            pos = f"seg{seg}(sl{self.ref.seg_slides[seg]})+{c - int(self.ref.jump_targets[seg])}/" \
                  f"{int(self.ref.end_cells[seg]) - int(self.ref.jump_targets[seg]) + 1} {r.tempo_qps * 60:.0f}bpm"
            log.append((wall, r.slide, wait, [(int(s), round(float(m[s]), 2)) for s in top],
                        None if v is None else round(v[1], 2), self._holding(wall), pos))
        return r

    online.OnlineFollower.readout = readout
    dec, rec = mrp.decisions(name, arm)
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    for t, shown, wait, top, v, hold, pos in log:
        band = max((g for g in gt if g.t_seconds <= t), key=lambda g: g.t_seconds, default=None)
        b = band.slide_idx if band else None
        print(f"{t:6.1f} band {b!s:>3} screen {shown!s:>3} {'OK ' if b == shown else '-- '} wait {wait:4.2f} "
              f"vocal(t-3) {v!s:>5} hold {int(hold)} top {top} at {pos}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
