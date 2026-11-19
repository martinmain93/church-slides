# Instructions for AI coding agents

Church Slides is built mostly with AI coding agents. This file is what they
read first, and it's useful for people too. `CLAUDE.md` imports it, and
Codex, Cursor and others read `AGENTS.md` directly.

## Read before you change anything

- **Any feature or UX work:** [docs/PRODUCT_PRINCIPLES.md](docs/PRODUCT_PRINCIPLES.md).
  Simplicity over flexibility is the product's core principle, from the
  website to every screen. Judge every design and every review by it.
- **What gets merged:** [CONTRIBUTING.md](CONTRIBUTING.md). Six principles,
  and a user story approved before any change a user can see.
- **How it fits together:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Engine changes:** the matching write-up in [docs/research/](docs/research/)
  and [docs/done/](docs/done/). Many obvious ideas have already been
  measured and rejected; the docs say why.

## Commands

```sh
scripts/setup_dev.sh            # set up; add --models for the bundled models
pnpm tauri:dev                  # run the app (the engine gets dev tools)
pnpm test                       # interface tests
(cd sidecar && uv run pytest -q)
(cd src-tauri && cargo test --lib)
pnpm lint
pnpm codegen                    # after changing sidecar/auto_presenter/protocol.py
```

## Rules

- **Never run a bare `uv sync` in `sidecar/`.** It removes the MLX and eval
  extras that `setup_dev.sh` installed. Use `uv run`, or
  `uv sync --inexact` and then rerun the setup script.
- **No copyrighted lyrics anywhere in the repo**: tests, fixtures, examples,
  docs, commit messages. Use public-domain hymns or lines you invent. Don't
  write out a real worship song's lyrics from memory.
- **The service never stops.** Nothing slow on the UI thread or the audio
  path. Anything online fails quietly. Fall back to the last slide or a
  blank, never to an error dialog in front of the room.
- **Measure engine changes. Plausible isn't enough.** Iterate on recorded
  traces, then run the real-time eval once the change is built
  (`research/eval/run_realtime_eval.py`), and put before and after numbers
  in the pull request. Offline replays of cached transcripts flatter the
  engine by about 2x, so don't quote them as the result.
- **Check that it reaches the screen.** A passing unit test or a correct
  engine broadcast doesn't prove the interface applied it. Check the
  running app.
- **Dependencies must be MIT-compatible** (MIT, BSD, Apache, ISC). No GPL or
  AGPL. Run `scripts/third_party_notices.py` after adding one.
- **No new telemetry** without a maintainer's approval. Crash reports and
  stats exist only in official builds (see `src/lib/usage.ts` and
  `src-tauri/src/telemetry.rs`).
- **Keep the privacy page and terms true.** A change that sends, stores,
  keeps or shows people's data differently updates `site/privacy.html` in
  the same change; one that changes what people may do updates
  `site/terms.html`. Major releases review both first (`docs/RELEASING.md`).
- **Don't reset files to undo one edit.** Never use `git checkout -- <file>` or
  `git restore` to undo one experimental change in a file that also has
  other uncommitted work.

## Seeing it work in the app

`.mcp.json` registers two MCP servers for agents:

- **auto-presenter-dev** plays a recording into the live engine
  (`inject_audio`), waits for a slide (`wait_for_slide`) and reads the
  session state (`session_snapshot`). It works against `pnpm tauri:dev`.
  Release builds compile these tools out.
- **tauri** drives and screenshots the app's windows.

Use them to check a change in the real app before saying it's done.

## Writing

- Text a user sees is short and plain: one-line hints, detail in an (i) tip,
  no jargon. Add a line under "Unreleased" in `CHANGELOG.md` for anything a
  user would notice.
- Keep the docs current. A design for open work goes in `docs/roadmap/`.
  When it ships, move it to `docs/done/` and update `docs/FEATURES.md`. A
  spike or rejected idea goes in `docs/research/` with its numbers.
