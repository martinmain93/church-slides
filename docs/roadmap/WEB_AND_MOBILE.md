# Web and mobile apps: how the code is split

Status: **agreed 2026-11-18.** Groundwork (record formats, the TypeScript
record conversion, the library data-source interface) is being built first;
the web app follows.

Church Slides runs today as a desktop app. A web app at
`app.churchslides.app` comes next, so a team can plan services from a phone
and find them ready on the slides computer on Sunday. Native iOS, iPad and
Android apps come later. This is how the code is shared between them.

## What a person gets on each

| | Desktop | Web (`app.churchslides.app`) | Mobile (later) |
| --- | --- | --- | --- |
| Library: browse, search, edit songs | Yes | Yes (words, order, credits; chords stay on the computer) | Yes |
| Setlists: build, order, keys, readings, text slides, delete | Yes | Yes | Yes |
| Team and account | Yes | Yes | Yes |
| Present, AI following, audio, band screens on the network | Yes | No | iPad later |
| Import files and other presentation apps, recordings | Yes | No | No |
| Search by meaning | Yes | Words and titles only | Later |
| Licensed Bible text | Yes (licensed per computer) | References only; public-domain text | Like desktop |

The web app says plainly where something happens "on the slides computer",
rather than hiding it.

## Layers

```
core        TypeScript, no UI, no platform: song format, slides, chords,
            service items, record formats and conversion, validation
library     one data-source interface (songs, setlists, readings, save,
            subscribe), with two implementations:
              desktop + mobile: Rust and the local database (today's sync)
              web: a browser cache, synced with the same protocol
ui          shared React views and pieces: library list, song editor,
            setlist builder, team, account. Views ask what the platform
            can do (canPresent, canImportFiles…), never "am I in Tauri"
shells      desktop (everything), web (planning + team), band page,
            mobile later (Tauri mobile)
server      closed source: accounts, record storage, live room, AI
            gateway, remote MCP, billing
```

## Decisions

1. **Reuse core, data and pieces; build phone-first pages for the web.**
   Today's setlist builder and song editor are desktop-wide and full of
   desktop-only actions. Their pieces move into shared components that get
   data from the data source instead of calling `invoke()` directly; the web
   gets phone-first pages built from them, and the desktop gets narrow
   layouts for free.
2. **Record formats are specified, and both implementations are tested
   against the same samples.** The server stores records it doesn't
   understand, so the only thing keeping a setlist planned on a phone intact
   on the slides computer is that every client writes the same shapes. They
   live in `docs/server-protocol/records.md`, with sample records in
   `docs/server-protocol/records/` that the Rust and TypeScript conversions
   are both tested against.
3. **Mobile with Tauri mobile, not React Native.** Tauri 2 builds for iOS
   and Android, so mobile reuses the views *and* the Rust local database,
   sync, change triggers and offline use. Only the web needs a TypeScript
   sync client, and it only has to keep a browser cache.
4. **The web app is open source**, in this repository, deployed by us to
   `app.churchslides.app`. People pay for where code runs (the server), never
   for a licence check in a client. Open, it reuses the app's components
   freely and works against any server that speaks the published protocol.
   Closed: the server, billing, and our own admin tools.
5. **One repository, several builds, no monorepo tooling yet.** Folders
   `src/core`, `src/library`, `src/ui`, shells `src/` (desktop), `web/`,
   `relay/` (band page). A lint rule keeps the layers honest: `core` imports
   nothing from React or Tauri, and the web shell imports nothing from
   Tauri. The web app is static, on Cloudflare (the domain is there); the
   server allows its origin.

## Order of work

1. **Groundwork:** record formats and samples; the TypeScript conversion,
   tested against the samples like the Rust one; the data-source interface
   with the desktop going through it unchanged.
2. **Web app v1:** sign-in (the band page's), library, setlists, song
   editing, phone-first.
3. **Desktop catches up:** shared pieces replace the old ones view by view.

## Changing existing data

Only two churches have tried organizations, and neither depends on them week
to week (2026-11-18). So record formats may change now if that makes them
cleaner, even if existing organization libraries need converting or, at
worst, re-sending from the slides computer. This won't be true for long:
once a church relies on it, formats only change with a migration.
