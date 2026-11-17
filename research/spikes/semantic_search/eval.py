"""Run every system over the test set and print the scorecard."""
import json
import os
import pickle
import sys
import time

import numpy as np

import corpus
import embed
import parse
import queries

SONGS = corpus.load_songs()
BY_ID = {s["id"]: i for i, s in enumerate(SONGS)}
TITLE = [s["title"] for s in SONGS]


def kw(q):
    return [BY_ID[i] for i in corpus.keyword_search(q) if i in BY_ID]


def song_scores(idx, qvecs, agg, unit):
    """Average over query vectors of each song's score."""
    tot = np.zeros(len(SONGS), dtype=np.float32)
    for qv in qvecs:
        if unit == "song":
            tot += idx["W"] @ qv
            continue
        if unit == "both":
            tot += 0.5 * (idx["W"] @ qv)
            tot += 0.5 * song_scores(idx, [qv], agg, "slide")
            continue
        sims = idx["V"] @ qv
        per = [[] for _ in SONGS]
        for s, o in zip(sims, idx["owner"]):
            per[o].append(s)
        for i, p in enumerate(per):
            p = sorted(p, reverse=True)
            k = {"max": 1, "top2": 2, "top3": 3}[agg]
            tot[i] += float(np.mean(p[:k]))
    return tot / len(qvecs)


def rrf(lists, k=60):
    sc = {}
    for L in lists:
        for r, i in enumerate(L):
            sc[i] = sc.get(i, 0) + 1 / (k + r + 1)
    return sorted(sc, key=lambda i: -sc[i])


def texts_for(p, q, use_occ=True, expansion=None):
    t = []
    if p.get("theme"):
        t.append(p["theme"])
    if p.get("occasion") and use_occ:
        t += parse.OCCASIONS[p["occasion"]][1]
    elif p.get("occasion"):
        t.append(p["occasion"])
    if expansion:
        t += expansion
    return t or [q]


def search(q, p, emb, idx, agg="max", unit="slide", merge=True, title_first=True, use_occ=True, expansion=None):
    raw_kw = kw(q)
    if title_first and raw_kw and corpus.title_tier(TITLE[raw_kw[0]], q) <= 1:
        return raw_kw
    qv = [emb.query(t) for t in texts_for(p, q, use_occ, expansion)]
    sc = song_scores(idx, qv, agg, unit)
    sem = list(np.argsort(-sc))
    if not merge:
        return sem
    leftover = p.get("theme")
    k = kw(leftover) if leftover and leftover.lower() != q.lower() else raw_kw
    return rrf([sem[:50], k[:50]])


# ---------------- metrics ----------------
def rel_ids(qd):
    return {i for i, t in enumerate(TITLE) if t in set(qd["rel"])}


def score(qd, ranked):
    R = rel_ids(qd)
    first = next((r for r, i in enumerate(ranked) if i in R), None)
    rr = 0 if first is None else 1 / (first + 1)
    hits10 = sum(1 for i in ranked[:10] if i in R)
    return dict(top1=float(first == 0), rr=rr, p10=hits10 / min(10, len(R)))


def parse_ok(p, g):
    alt = g.get("alt", {})
    res = {}
    res["hymn"] = (p["hymn"] == g["hymn"]) or (alt.get("hymn_alt") is not None and p["hymn"] == alt["hymn_alt"])
    res["key"] = (p["key"] or None) == g["key"]
    gp, pp = g["played"], p["played"]
    if gp is None:
        res["played"] = pp is None
    else:
        res["played"] = pp is not None and pp[0] == gp[0] and (gp[1] is None or pp[1] == gp[1])
    res["occasion"] = bool(p["occasion"] == g["occasion"] or (alt.get("occasion_alt") and p["occasion"] == alt["occasion_alt"]))
    res["theme"] = bool(p.get("theme")) == g["theme"]
    res["all"] = all(res.values())
    return res


def main():
    results = {}
    t0 = time.time()
    embs = {n: embed.Embedder(n) for n in ["bge", "e5", "e5q"]}
    idxs = {}
    for n, e in embs.items():
        cp = f"cache_{n}.pkl"
        idxs[n] = pickle.load(open(cp, "rb")) if os.path.exists(cp) else embed.build_index(e, SONGS)
        pickle.dump(idxs[n], open(cp, "wb"))
        print(f"[{n}] model {e.size_mb:.0f} MB, indexed {idxs[n]['n']} slides in {idxs[n]['secs']:.1f}s", flush=True)
        qt = time.perf_counter()
        for _ in range(20):
            e.query("songs about the blood of Jesus")
        print(f"[{n}] query embed {(time.perf_counter() - qt) / 20 * 1000:.1f} ms", flush=True)

    retr = [q for q in queries.Q if q["kind"] != "filter"]
    rule_p = {q["q"]: parse.rules(q["q"]) for q in queries.Q}

    llms = pickle.load(open("cache_llm.pkl", "rb")) if os.path.exists("cache_llm.pkl") else {}
    expansions = llms.pop("_exp", None)
    for name, repo in [("qwen0.5", "mlx-community/Qwen2.5-0.5B-Instruct-4bit"), ("qwen1.5", "mlx-community/Qwen2.5-1.5B-Instruct-4bit")]:
        if name in llms and expansions is not None:
            ms = [v["ms"] for v in llms[name].values()]
            print(f"[{name}] (cached) parse median {np.median(ms):.0f} ms, max {max(ms):.0f} ms")
            continue
        llm = parse.LLMParser(repo)
        llm("warm up")
        allq = [q["q"] for q in queries.Q] + [h for h, _ in queries.HOLDOUT]
        llms[name] = {q: llm(q) for q in allq}
        ms = [v["ms"] for v in llms[name].values()]
        print(f"[{name}] parse median {np.median(ms):.0f} ms, max {max(ms):.0f} ms", flush=True)
        if name == "qwen1.5":
            exp_llm = llm
    # LLM expansion variant (qwen1.5): lyric-like phrases for the theme/occasion
    from mlx_lm import generate
    if expansions is None:
      expansions = {}
      for q in retr:
        msgs = [{"role": "user", "content": "Write 4 short lines that might appear in worship song lyrics matching this search, one per line, no numbering:\n" + q["q"]}]
        pr = exp_llm.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        out = generate(exp_llm.model, exp_llm.tok, prompt=pr, max_tokens=80, verbose=False)
        expansions[q["q"]] = [l.strip(" -•\"") for l in out.splitlines() if l.strip()][:4]

    pickle.dump({**llms, "_exp": expansions}, open("cache_llm.pkl", "wb"))

    # ---------------- parse accuracy ----------------
    print("\n=== PARSE ACCURACY (fields all correct) ===")
    filt = [q for q in queries.Q if q["kind"] == "filter"]
    for name, P in [("rules", {**rule_p, **{h: parse.rules(h) for h, _ in queries.HOLDOUT}}), *llms.items()]:
        a = [parse_ok(P[q["q"]], q["gold"]) for q in filt]
        h = [parse_ok(P[hq], g) for hq, g in queries.HOLDOUT]
        fails = [hq for (hq, g), r in zip(queries.HOLDOUT, h) if not r["all"]]
        per = {f: sum(r[f] for r in h) for f in ["hymn", "key", "played", "occasion", "theme"]}
        print(f"{name:8s} tuned-set {sum(r['all'] for r in a)}/{len(a)}  held-out {sum(r['all'] for r in h)}/{len(h)}  per-field(held-out) {per}")
        for fq in fails:
            pp = {k: v for k, v in P[fq].items() if k in ('hymn', 'key', 'played', 'occasion', 'theme')}
            print(f"          miss: {fq!r} -> {pp}")
        results[f"parse_{name}"] = dict(tuned=sum(r['all'] for r in a), held=sum(r['all'] for r in h), fails=fails)

    # ---------------- retrieval ----------------
    systems = {
        "keyword (today)": lambda q, e: kw(q["q"]),
        "S1 bge rules+occ slide-max merged": lambda q, e: search(q["q"], rule_p[q["q"]], embs["bge"], idxs["bge"]),
        "S1 e5  rules+occ slide-max merged": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5"], idxs["e5"]),
        "  e5 no occasion table": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5"], idxs["e5"], use_occ=False),
        "  e5 embeddings only (no kw merge)": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5"], idxs["e5"], merge=False),
        "  e5 whole-song vectors": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5"], idxs["e5"], unit="song"),
        "  e5 song+slide-max blend": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5"], idxs["e5"], unit="both"),
        "  bge whole-song vectors": lambda q, e: search(q["q"], rule_p[q["q"]], embs["bge"], idxs["bge"], unit="song"),
        "  bge song+slide-max blend": lambda q, e: search(q["q"], rule_p[q["q"]], embs["bge"], idxs["bge"], unit="both"),
        "S3 e5q (int8) song+slide blend": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5q"], idxs["e5q"], unit="both"),
        "  e5q whole-song": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5q"], idxs["e5q"], unit="song"),
        "  e5 slide top2": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5"], idxs["e5"], agg="top2"),
        "  e5 slide top3": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5"], idxs["e5"], agg="top3"),
        "  e5 no title-first rule": lambda q, e: search(q["q"], rule_p[q["q"]], embs["e5"], idxs["e5"], title_first=False),
        "S2 e5 + qwen1.5 parse": lambda q, e: search(q["q"], llms["qwen1.5"][q["q"]], embs["e5"], idxs["e5"]),
        "S2b e5 + qwen1.5 parse + expansion": lambda q, e: search(q["q"], llms["qwen1.5"][q["q"]], embs["e5"], idxs["e5"], expansion=expansions[q["q"]]),
        "S2 bge + qwen1.5 parse": lambda q, e: search(q["q"], llms["qwen1.5"][q["q"]], embs["bge"], idxs["bge"]),
    }
    print("\n=== RETRIEVAL ===")
    print(f"{'system':38s} | title top1 | lyric top1 | theme P@10  MRR | occasion P@10  MRR")
    per_query = {}
    for name, fn in systems.items():
        rows = {k: [] for k in ["title", "lyric", "theme", "occasion"]}
        for q in retr:
            ranked = [int(i) for i in fn(q, None)]
            s = score(q, ranked)
            rows[q["kind"]].append(s)
            per_query.setdefault(q["id"], {})[name] = (round(s["p10"], 2), [TITLE[i] for i in ranked[:10]])
        m = lambda k, f: np.mean([r[f] for r in rows[k]])
        print(f"{name:38s} |   {m('title','top1'):.2f}     |   {m('lyric','top1'):.2f}     |   {m('theme','p10'):.2f}   {m('theme','rr'):.2f} |   {m('occasion','p10'):.2f}    {m('occasion','rr'):.2f}")
        results[name] = {k: {f: float(m(k, f)) for f in ["top1", "rr", "p10"]} for k in rows}

    json.dump(dict(results=results, per_query=per_query, expansions=expansions,
                   llm_parses={n: {q: {k: v for k, v in p.items() if k != 'raw'} for q, p in d.items()} for n, d in llms.items()}),
              open("results.json", "w"), indent=1, default=str)
    print(f"\ntotal {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
