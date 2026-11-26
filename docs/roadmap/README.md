# Roadmap

Where Church Slides is going, as of November 2026. Nothing here is a promise
of a date. To work on an item, open a
[proposal](../../.github/ISSUE_TEMPLATE/proposal.md) first: see
[CONTRIBUTING.md](../../CONTRIBUTING.md).

## Where it stands

Version 0.2.2 runs real services on Mac and Windows. The AI follows a band
well enough that a volunteer mostly watches rather than drives. The app is
open source and free, and stays that way. [FEATURES.md](../FEATURES.md) lists
what it does today.

## The stages

| Stage | Goal | Where the work happens |
| --- | --- | --- |
| **Local pilot** (now) | 3–5 churches of different sizes run real services, one of them with nobody as the regular operator, and their pain points get fixed | This repo |
| **Server** | A church signs in, invites its team, and shares setlists across devices. The app still runs a whole service with the server unreachable | A separate server; the app gains optional sign-in |
| **Paid tier** | Hosted features worth paying for: team sync, sending a setlist to the whole team, a hosted band relay, hosted AI for slow computers, CCLI reporting | The server. Nothing in this app is locked |
| **Public beta** | A few dozen churches we don't know, on a wide range of computers | Both |
| **Full launch** | Promote widely once a stranger's church can rely on it every week: no open show-stoppers, 99%+ of services without a crash | Both |

## Open work in this repo

### Following the band

- **Moves a person wouldn't make.** About one AI move in seven used to be of
  a kind an operator never would, usually corrected within 2–5 seconds.
  0.2.2 fixed most of leaving a slide early. Still open: jumps to a slide
  nobody sang, moves while nobody is singing, and stepping on and back
  through a line the band repeats. Much of what's left comes from the
  words arriving ~3 s late.
  [ERROR_HUNT_2026-11-12](ERROR_HUNT_2026-11-12.md)
- **Measurement blind spots.** The headline score rewards switching early,
  and nothing checks that a decision reaches the screen.
  [VISIBILITY_REVIEW_2026-11-10](VISIBILITY_REVIEW_2026-11-10.md)
- **Remaining engine review items** (11–18, low severity).
  [ENGINE_REVIEW_2026-11-10](../done/ENGINE_REVIEW_2026-11-10.md)
- **In-service learning**: learn a song with no lead sheet during the service,
  so the second time through goes better. Built, but turned off.
  [LEARNED_REFERENCES](../done/LEARNED_REFERENCES.md)
- **Faster computers aren't required.** Bring the next-line check and
  readings that follow the reader to Intel Macs and Windows.

### The leader and the band

- **Leader screen in landscape**, with the whole song on one page.
- **Music director cues**: band-only "next section" cues on the leader
  screen first, then one-tap chord patterns.
- **Notice a different key**: when the band plays in a key other than the
  chart's, offer to switch the chart (capo-aware).
- **Vocal range**: warn when a key sits outside the leader's range, and
  suggest one that fits.
- **Suggestions that learn the leader's habits**: rank "Fits next" by what
  this leader actually picks. This never touches the engine.
- **More inputs** (order TBD): a talkback channel for the music director
  that goes straight to commands; a talk button on the leader or band page;
  audio from a phone or iPad near the stage as the music input.
  [INPUTS](INPUTS.md)

### Library and setup

- **EasySlides import.**
- **Undo** for setlist and slide edits, which today are immediate. It's
  partly there in the song editor. [UNDO](UNDO.md)
- **One workspace component.** Setlist preparation and the live service
  share a layout, but are still two components, so Start service remounts
  the screen. [UX_DESIGN_REVIEW](../done/UX_DESIGN_REVIEW.md)
- **Install without warnings**: notarised Mac builds and signed Windows
  installers.
- **Linux packages.** Linux builds from source today but isn't packaged.

### Later, with the server

- A phone and iPad app for planning: a web planner on the server first,
  native apps after.
- Sign-in, church teams and sync in the app, always optional.
