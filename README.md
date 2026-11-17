# Church Slides

Worship slides that follow your band. Church Slides listens to the room and
moves the lyrics on as the band sings, so a volunteer, or nobody at all, can
run the screens on a Sunday.

**Free forever, and open source (MIT).** Everything that runs on the church's
own computer is in this repo and stays free: presenting, the song library and
setlists, imports from other presentation apps, display looks, scripture, the
AI that follows the band, and band and leader screens on the local network.

An optional paid service adds features that need a server, such as accounts,
sync across a team's devices, sending a setlist to the whole team, and hosted
AI for slower computers. It's built separately and never takes away anything
from this free app.

[Download](https://church-slides.netlify.app) ·
[What's new](https://church-slides.netlify.app/changelog.html) ·
[Features](docs/FEATURES.md) ·
[Roadmap](docs/roadmap/) ·
[Contributing](CONTRIBUTING.md)

## Supported computers

| | Presenting | AI that follows the band |
| --- | --- | --- |
| Mac, Apple silicon | macOS 10.15+ | macOS 11+ |
| Mac, Intel | macOS 10.15+ | macOS 11+ |
| Windows 10/11 (64-bit) | Yes | Yes (experimental installer) |
| Linux (Ubuntu 22.04+) | Builds from source | Not packaged |

The app checks the computer's speed on first run. On a computer too slow for
the AI to keep up, it offers Assist mode, which suggests the next slide for a
person to confirm.

## How it's built

- **App shell:** [Tauri 2](https://tauri.app/) in Rust (`src-tauri/`). It
  handles windows and screens, the SQLite library, and audio devices.
- **Interface:** React, TypeScript, Vite and Tailwind (`src/`).
- **AI engine:** Python (`sidecar/`). It runs speech recognition (Whisper:
  MLX on Apple silicon, CTranslate2 elsewhere) and the follower that decides
  when to change slides. The shell starts it and talks to it over a local
  WebSocket. Release builds freeze it with PyInstaller.
- **Online band relay:** a Cloudflare Worker (`relay/`), deployed into the
  church's own Cloudflare account.
- **Usage statistics:** opt-in, received by a small Worker (`stats/`).
- **Shared types:** Pydantic models in `sidecar/auto_presenter/` are the
  source of truth. `pnpm codegen` mirrors them into `src/types/generated.ts`.

## Documentation

| | |
| --- | --- |
| [Architecture](docs/ARCHITECTURE.md) | How the app, the AI engine and the band screens fit together |
| [Features](docs/FEATURES.md) | What the app does, with links to each feature's design |
| [Roadmap](docs/roadmap/) | What's next, and the open work |
| [Product principles](docs/PRODUCT_PRINCIPLES.md) | Simple first: how every design is judged |
| [Server protocol](docs/server-protocol/) | How the app talks to an optional server; anyone can implement it |
| [Research](docs/research/) | Ideas that were measured and parked or rejected, and why |
| [Done](docs/done/) | Designs and reviews for work that has shipped |

## Built with AI

Church Slides is developed mostly with AI coding agents (Claude Code). Most
of the code, tests and design documents were written by an agent, then
directed, measured and reviewed by a person. Contributions made the same
way are welcome. The bar is the same for every change, however it was
written.

To work this way:

- **Point your agent at [AGENTS.md](AGENTS.md).** Claude Code loads it
  through `CLAUDE.md`, and Codex, Cursor and most others read it directly.
  It has the commands, the rules that aren't obvious from the code, and
  which documents to read first.
- **Start from the design docs, not a blank prompt.** Before an engine
  change, give the agent the relevant write-up from
  [docs/research/](docs/research/) or [docs/done/](docs/done/). Many
  plausible ideas have already been measured and rejected.
- **Get the user story approved before the agent writes code**
  ([CONTRIBUTING.md](CONTRIBUTING.md#process)). An agent will happily build
  more than was asked for.
- **Make it prove the change.** Have it run the tests, and for engine
  changes the real-time eval, and check the result in the running app.
  `.mcp.json` gives agents tools to play a recording into the live engine
  and to drive the app's windows. Put the numbers in the pull request.
- **You own every line.** Read the diff before you open the pull request,
  and be ready to explain any of it in review. Keep pull requests small
  enough to read.
- **Watch for what agents get wrong here.** Copyrighted lyrics in test data
  (use public-domain hymns or invented lines), new settings and buttons
  where a better default would do, and claiming a fix works because the
  unit test passed.

## Development

You need Node 20+, pnpm 9+, Rust (stable), [uv](https://docs.astral.sh/uv/),
and Xcode Command Line Tools on a Mac (Visual Studio Build Tools on Windows).

```sh
scripts/setup_dev.sh      # once, and after dependency changes
pnpm tauri:dev            # run the app
```

In `sidecar/`, use `uv run` rather than a bare `uv sync`: `uv sync` removes
the optional packages the setup script installs.

Tests:

```sh
pnpm test                               # interface
(cd sidecar && uv run pytest -q)        # AI engine
(cd src-tauri && cargo test --lib)      # app shell
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before starting on a change. It
explains which changes need an approved proposal, and the benchmarks every
change has to keep.

## Licence

The code is [MIT](LICENSE). Bundled models and libraries keep their own
licences, listed in the app under Settings → Open-source licences and
generated by `scripts/third_party_notices.py`. The test recording in
`data/fixtures/perf/` has its own terms, in its README.

"Church Slides" and its logo are trademarks; see [TRADEMARKS.md](TRADEMARKS.md).
If you publish your own build, please give it a different name.
