# Done

Design documents and reviews for work that has shipped. They record what
was built and why, as it was at the time. The code has moved on since, so
for how things work today start with [ARCHITECTURE.md](../ARCHITECTURE.md)
and [FEATURES.md](../FEATURES.md).

## The original build

| Document | What it covers |
| --- | --- |
| [ORIGINAL_PLAN](ORIGINAL_PLAN.md) | The first architecture and approach, before any code. Much of it changed; see the September roadmap for how |
| [PHASE_0](PHASE_0.md) | Tauri shell, Python engine, SQLite storage |
| [PHASE_1](PHASE_1.md) | Song library: editor, search, import |
| [PHASE_2](PHASE_2.md) | Setlists, presenting, the debug panel |
| [PHASE_3](PHASE_3.md) | The evaluation harness, built before any AI |
| [PHASE_4](PHASE_4.md) | Speech recognition and the audio setup wizard |
| [PHASE_5](PHASE_5.md) | A deliberately simple first matcher, for a baseline |
| [PHASE_6](PHASE_6.md) | The particle filter, later replaced by the forward filter |
| [PHASE_7](PHASE_7.md) | Repeats, jumps and lost detection |
| [PHASE_8](PHASE_8.md) | Library-wide recovery, the language-model supervisor, the installer |
| [ROADMAP_2026-09](ROADMAP_2026-09.md) | The September roadmap: how each phase actually ended up |

## Features

| Document | What it covers |
| --- | --- |
| [AI_ASSISTED_MODE](AI_ASSISTED_MODE.md) | Assist: the operator drives, the AI highlights where the band is |
| [SPOKEN_INSTRUCTIONS](SPOKEN_INSTRUCTIONS.md) | "Go back to verse one": spoken requests |
| [NEXT_LINE_CHECK](NEXT_LINE_CHECK.md) | Hearing a new line start in about a second |
| [OFFSETLIST_TRIGGER](OFFSETLIST_TRIGGER.md) | Finding a song that isn't in the setlist: why it never fired, and the vote trigger that replaced it |
| [LEARNED_REFERENCES](LEARNED_REFERENCES.md) | Following a song with no lead sheet from how the church plays it |
| [BIBLE_INTEGRATION](BIBLE_INTEGRATION.md) | Scripture: translations, licensing, references |
| [SERVICE_ITEMS](SERVICE_ITEMS.md) | Prepared scripture and text slides in a service |
| [HYMNS](HYMNS.md) | Example hymns and the Open Hymnal |

## Reviews and measurements

| Document | What it covers |
| --- | --- |
| [ENGINE_REVIEW_2026-11-10](ENGINE_REVIEW_2026-11-10.md) | What could put the wrong slide up or put a church's computer at risk. Items 1–10 fixed in 0.2.0 |
| [PERF_AUDIT_2026-09-26](PERF_AUDIT_2026-09-26.md) | CPU, memory and leaks over a long service |
| [UX_DESIGN_REVIEW](UX_DESIGN_REVIEW.md) | Interface review against the product principles |
| [BASELINE_NUMBERS](BASELINE_NUMBERS.md) | The first measured accuracy numbers (August 2026) |
| [REALTIME_EVAL](REALTIME_EVAL.md) | Output of `research/eval/run_realtime_eval.py` (September 2026) |
