"""The follower's tempo belief: is it wrong, or just undecided?
Per song: the tempo belief's mean and spread (in rate-grid steps), how much
mass sits within 5% of the true tempo, and whether the belief is biased slow."""
import json
import numpy as np
import cv, fusion, joint, follow as fl, diag_anticipation as da, gt
from auto_presenter.eval.replay import load_recording

da.setup()
events = json.loads(fusion.EVENTS.read_text())
for key in ("wabn", "rl", "komh", "isa"):
    song = cv.Song(key); rec = load_recording(cv.S / key)
    item = cv.stage1.song_item(song.s, cv.S / key)
    cues = gt.build(song.s, song.slides, cv.S / key, float(song.F["duration"]))
    g = gt.song_rate(cues)
    bt = da.beat_tempo(key, np.array([300.0]))  # unused; tempo truth from the tracker's whole-song median
    b = np.array(json.loads((cv.S / f"beats_{key}.json").read_text())["beats"])
    tb = 1 / np.median(np.diff(b)); tb = tb / 2 ** np.round(np.log2(tb / g))
    walls, shown, frate = da.follower(song, rec, events, item)
    rm = joint.follow.rate_marg
    import segref
    rates = np.geomspace(40, 130, 16) / 60.0  # quarter notes / s, as SegmentReference builds them
    sung = np.array([song.tl["slide"][min(len(song.tl["slide"]) - 1, int(w * 10))] >= 0 for w in walls])
    rm = rm[sung] / rm[sung].sum(axis=1, keepdims=True)
    mean = rm @ rates
    near = rm[:, np.abs(rates / tb - 1) <= 0.05].sum(axis=1)
    slow = rm[:, rates < tb * 0.95].sum(axis=1); fast = rm[:, rates > tb * 1.05].sum(axis=1)
    ent = -(rm * np.log(rm + 1e-12)).sum(axis=1) / np.log(len(rates))
    print(f"{key:5s} tracker tempo {tb:.2f} q/s | belief mean {np.median(mean):.2f} | mass within 5%: median {np.median(near):.2f}"
          f" | slower {np.median(slow):.2f} faster {np.median(fast):.2f} | entropy (0 sure..1 uniform) {np.median(ent):.2f}")
