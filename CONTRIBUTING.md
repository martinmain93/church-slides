# Contributing to Church Slides

Church Slides puts the words a room needs on the screen and follows the band,
so a volunteer — or nobody — can run the slides on a Sunday. Thanks for
helping.

This page is short on purpose. Read the principles, follow the process, and
tick the checklist in your pull request.

## Principles

These decide what gets merged. A pull request that breaks one is closed, however
good the code is.

### 1. A service never stops

A crash, a frozen screen or a lost song during a service is the worst bug there
is. Everything else comes second.

- A full service runs with no internet. Anything online fails quietly and the
  service carries on.
- Nothing slow runs on the UI thread or blocks the audio path.
- When something goes wrong, fall back to what still works (the last slide, a
  blank screen) and never to an error dialog in front of the room.

### 2. No visual clutter in the core screens

The core screens are the operator view, the projector output, and the leader
and band screens. Volunteers use them under pressure, so they only get simpler.

- No new permanent buttons, badges or panels on a core screen. New controls go
  behind "⋯", or appear only while they're in use.
- The projector output shows the church's words in the church's look, and
  nothing else: no app UI, ever.
- A new setting needs a reason a volunteer would change it. A better default
  wins over a setting every time.
- Hints are one line. Detail goes in an (i) tip.
- Every pull request that changes a screen shows before and after screenshots.

The full reasoning is in [docs/PRODUCT_PRINCIPLES.md](docs/PRODUCT_PRINCIPLES.md).
Read it before proposing a feature. Expect "no" more often than "yes": keeping
the app simple means turning down good ideas.

### 3. Every benchmark stays the same or gets better, on every platform

Churches run this on old laptops. A feature that makes the AI late, or the fan
loud, makes the app worse for them.

- No measured number gets worse beyond its noise floor: AI accuracy and timing,
  lag, CPU, memory, startup, and UI response.
- This applies on every supported platform: Apple-silicon Mac, Intel Mac and
  Windows. CI measures the ones you can't run yourself.
- A feature that's turned off costs nothing.
- Sometimes a change honestly trades one number for another, for example better
  accuracy for a little more CPU. Put both numbers in the pull request and say
  why. A maintainer decides. Don't hide the cost.

What's measured, and how, is in [Benchmarks](#benchmarks) below.

### 4. One user story, approved before code

Every change a user can see starts as a user story. A maintainer has to approve
it before you open a pull request (see [Process](#process)). Discussing it
early saves you building something that won't be merged.

### 5. The app is free, open and local

- The app is MIT-licensed and free forever. Features that run on the church's
  computer stay in the free app.
- No licence checks, plan checks or "upgrade" prompts in the app. Paid features
  live on the server, not here.
- Audio and lyrics stay on the church's computer unless the church turned on a
  feature that says otherwise.
- No new analytics or error reporting without a maintainer's approval, and
  never anything a person said or sang.

### 6. Content we can share

- Lyrics in tests, fixtures and examples are public domain only. No
  copyrighted lyrics, not even a line.
- Recordings only if the owner gave a licence to share them, written in the
  folder's README.
- New dependencies must have a licence compatible with MIT (MIT, BSD, Apache,
  ISC and similar). No GPL or AGPL. Run `scripts/third_party_notices.py` after
  adding one.

## Process

**Bug fixes, tests, docs, and speedups that change nothing a user sees:** open a
pull request directly. Link the issue if there is one.

**Anything a user sees, or does differently:**

1. Open a **Proposal** issue with a user story (the template asks for it).
2. Wait for a maintainer to add the `approved` label. They may ask for the
   story to be cut down first; that's normal.
3. Open a pull request that links the approved issue and builds only what the
   story says. Something extra means a new story.

Pull requests for unapproved changes that users can see are closed, with a link
back to this page.

AI-written code is welcome (see [Built with AI](README.md#built-with-ai)). You must have run it, and you must be able to
explain every line in review.

Sign off each commit (`git commit -s`). That certifies you have the right to
submit it under the project's licence (the
[Developer Certificate of Origin](https://developercertificate.org/)).

## Benchmarks

| What | How | When |
| --- | --- | --- |
| Unit tests (app, engine, Rust) | `pnpm test`, `uv run pytest -q` in `sidecar/`, `cargo test` | Every pull request (CI) |
| Engine speed and cost on real audio: lag, CPU, memory, slides on time | `scripts/perf_report.py` | Every build. CI runs it on Windows |
| AI accuracy on the public recordings | `research/eval/run_realtime_eval.py` | Every engine change |
| AI accuracy on the private church recordings | Run by a maintainer | Engine changes, before merge |
| Long-run soak and UI timings | `scripts/perf_suite.py` | Before a release, or when asked |

Engine changes: iterate on recorded traces, then run the real-time eval once
the change is built. Put the before and after numbers in the pull request.

## Setup

See [README.md](README.md#development). In `sidecar/`, use `uv run` rather than a
bare `uv sync`: `uv sync` removes the optional extras the AI needs.
