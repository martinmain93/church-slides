# Semantic library search (spike, 2026-10-21; shipped 2026-10-22)

Can one search box answer "songs about victory in Jesus", "good songs for
easter that we haven't played in the last 3 services" and "hymns in the key of
E about the blood of Jesus" without a language model? Yes: rules for the
filters, a fixed occasion table, and a small sentence-embedding model.

What shipped: `src/lib/smartSearch/` (query parsing, hymn detection, merge)
and `sidecar/auto_presenter/library_search/` (the model and the index).

## Results (326-song real library, 27 labelled queries)

Precision@10 (share of the top ten that are right):

| System | Titles top-1 | Lyric lines top-1 | Themes P@10 | Occasions P@10 |
|---|---|---|---|---|
| Keyword search (before) | 7/8 | 5/5 | 0.16 | 0.00 |
| Rules + occasion table + e5-small, slide max | 8/8 | 4/5 | 0.41 | 0.52 |
| ... whole-song vectors | 8/8 | 5/5 | 0.45 | 0.56 |
| ... int8 e5, 50/50 song + best passage (**shipped**) | 8/8 | 5/5 | 0.46 | 0.60 |
| Without the occasion table | 8/8 | 4/5 | 0.41 | 0.10 |
| Qwen2.5 1.5B (local, MLX) reads the query | 7/8 | 1/5 | 0.40 | 0.44 |
| ... and writes lyric-like phrases to search | 7/8 | 1/5 | 0.31 | 0.42 |

- The local LLM parsed 15/25 held-out filter queries vs 16/25 for the rules,
  took ~2 s a query, and rewrote remembered lyric lines into something else.
  Qwen 0.5B parsed 4/25. Not shipped.
- The occasion table is load-bearing: occasions are rarely words in the songs.
- Passage-only scoring was not better than whole-song vectors (Good Friday
  0.10 vs 0.80); the blend keeps the best passage as "why it matched".
- int8 e5-small-v2: 34 MB, ~2 ms a query, quality equal to fp32 (133 MB).
- Fails everywhere: abstract paraphrase ("God's love chasing after us" ->
  Reckless Love).
- Found on the way: `sanitize_fts5` turned "10,000" into "10000", so "10,000
  reasons" found nothing (fixed in `src-tauri/src/db.rs`).
- Hymn detection by wording alone found 18/34 hymns; with the title list in
  `src/lib/smartSearch/hymns.ts`, 32/34.

Small sample: 14 theme/occasion queries, so 0.03 is one song.

## Rerun

Needs its own environment (MLX and the LLMs aren't sidecar dependencies):

    uv venv .venv && uv pip install onnxruntime tokenizers numpy huggingface_hub mlx-lm
    SEMSEARCH_DB=/path/to/a/copy/of/library.db .venv/bin/python eval.py

`queries.py` holds the labels (written before any system ran, pooled from
keyword probes in `probe.py`); they name songs in the library they were
written for.
