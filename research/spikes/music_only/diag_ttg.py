"""Does knowing the pulse predict *when* the next slide comes?

    uv run --project sidecar python research/spikes/music_only/diag_ttg.py

At each read-out inside a sung slide, the follower's belief gives a time to
the end of the slide it thinks is up: every (cell, tempo) state's cells left
in its segment over its tempo, and the belief's median of that. Compare with
the truth: the next slide's first sung word minus now. Split the error into
its two causes: where the follower thinks it is (position at "now", in beats,
against the GT position) and how fast it thinks the song goes (tempo against
the beat tracker's).

Run without and with beat tempo, at lookahead 0 (the belief at "now")."""
import json
import numpy as np
import cv, fusion, gt, joint, segref, follow as fl, run_joint as rj, diag_anticipation as da
from auto_presenter.eval.replay import load_recording

da.setup()
events = json.loads(fusion.EVENTS.read_text())
for tempo in ("", "beats"):
    rj.TEMPO = tempo
    print(f"== tempo evidence: {tempo or 'none'}")
    for key in ("wabn", "rl", "komh", "isa"):
        song = cv.Song(key); rec = load_recording(cv.S / key)
        cues = gt.build(song.s, song.slides, cv.S / key, float(song.F["duration"]))
        fw = [x for x in da.first_word_times(song, cues) if x is not None]
        firsts = np.array([x[1] for x in fw])
        _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
        ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                                      sequences=[rj.roadmap_sequence(song)])
        T = len(song.ev["mix"][0])
        srcs = rj.sources_for(song, ref, events[rec.id], da.ARM)
        if tempo:
            srcs.append(rj.tempo_source(song, ref, T))
        walls, pos, rate, marg = joint.follow(ref, srcs, T)
        ttg, pnow = joint.follow.ttg, joint.follow.pos_now
        # truth: next first word after now, while a slide is being sung
        sl_of = np.asarray(ref.slide_of)
        rows = []
        for k, w in enumerate(walls):
            i = min(len(song.tl["slide"]) - 1, int(w * 10))
            if song.tl["slide"][i] < 0 or not np.isfinite(song.tl["pos"][i]):
                continue
            nxt = firsts[firsts > w]
            if not len(nxt) or nxt[0] - w > 12:
                continue
            right_slide = sl_of[pnow[k]] == song.tl["slide"][i]
            rows.append((nxt[0] - w, ttg[k], right_slide))
        r = np.array(rows)
        err = r[:, 1] - r[:, 0]
        ok = r[:, 2] > 0
        q = lambda x: f"{np.median(x):+.2f} (IQR {np.percentile(x,25):+.2f}..{np.percentile(x,75):+.2f})" if len(x) else "-"
        near = ok & (r[:, 0] <= 3)
        print(f"  {key:5s} on the right slide {ok.mean():4.0%} | predicted - true time to next slide (s), right slide: {q(err[ok])};"
              f" with <=3 s to go: {q(err[near])}; |err|<0.5 s {np.mean(np.abs(err[near]) < 0.5):4.0%}")
