# Web and mobile apps: how the code is split

Status: **agreed 2026-11-18. Groundwork built 2026-11-18:** record formats
(`docs/server-protocol/records.md` and samples), the TypeScript conversion
(`src/core/records.ts`), the shared library types (`src/core/library.ts`),
the data-source interface (`src/library/`) with the desktop version, and the
lint rule for the layers. The desktop's views don't use the interface yet;
they move over as the web app needs their pieces. **Web app v1 built
2026-11-24** (see "As built"); live at `app.churchslides.app` since 2026-11-26.

Church Slides runs today as a desktop app. A web app at
`app.churchslides.app` comes next, so a team can plan services from a phone
and find them ready on the slides computer on Sunday. Native iOS, iPad and
Android apps come later. This is how the code is shared between them.

## What a person gets on each

| | Desktop | Web (`app.churchslides.app`) | Mobile (later) |
| --- | --- | --- | --- |
| Library: browse, search, edit songs | Yes | Yes (words, order, credits; chords shown, transposed to the service's key; editing them stays on the computer for now) | Yes |
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

## As built (web app v1, 2026-11-24)

- **Where it lives:** pages in `src/web/` (phone-first, a bottom tab bar:
  Services, Songs, You), the entry in `web/` (`pnpm web:dev` on port 1430,
  `pnpm web:build` into `web/dist`). Routes are in the address's fragment
  (`#/services/…`), so it's one static page. Add `?server=http://localhost:8787`
  to use a local server (`pnpm dev:test-sign-in` in the server repo).
- **Signing in** is the band page's code, now shared
  (`src/lib/account/browserSignIn.ts`): a public OAuth client identified by
  `web/public/oauth/web-app-client.json`. Each browser has its own sign-in,
  so the slides computer, band pages and any number of browsers stay signed
  in together.
- **The library** (`src/library/web/`) is the `LibrarySource` over a record
  store in IndexedDB: each record as the server last sent it, plus the
  changes waiting to go up, kept as the fields they set and the version
  they were made on (so words edited on an older version become a revision
  on the server, and someone else's edit to another field isn't
  overwritten). The sync loop is the desktop's: push about a second after a
  change, pull on the change-notice socket, when the page comes back into
  view or online, and every minute. A notice shows only when offline, signed
  out, or a change was refused.
- **Chords** are shown read-only above the words (the band page's
  `ChordLyrics` and `alignSlides`), transposed when opened from a service
  with its own key. Editing words keeps the song's `chart`, scripture links
  and lead-sheet data untouched: only changed fields go up.
- **Roles:** owners, admins and operators edit songs and services; set
  leaders plan services and read songs; members read.
- **Services** have a date (`service_date`, `YYYY-MM-DD`, also on the
  desktop: migration 014, a date field beside the name). A new one is for
  the coming Sunday, named after it and made unique with "(2)"; its name
  field is focused so it can be retyped at once. A name still made from the
  date follows the date when it changes. The list shows Upcoming (soonest
  first) and Past. Add songs, a reading by reference (the slides computer
  fills in the verses) or a text slide, and edit readings and text slides
  afterwards; set a song's key (only for songs with chords); drag items to
  reorder (or the handle and arrow keys); copy for the next service; delete.
- **Songs** show in the order they're sung (their Order, with repeats);
  the editor checks the Order against the sections, warns before leaving
  with unsaved changes, sends only the fields changed since it opened, and
  says which services use a song before it can be deleted.
- **Review fixes (2026-11-25)**, after UX, security and code reviews: a
  delete during a create's push no longer comes back; a server reset sends
  whole records; renames send only the name (canonical comparison, unknown
  item fields kept); a 403 refuses the waiting changes instead of looping;
  tabs refresh the sign-in one at a time (Web Locks); sign-out closes and
  deletes the library; the library is tied to the person who signed in;
  `iss` is checked; localhost servers only in development builds; the
  socket address must be the server's; charts are cleaned before showing;
  a page that fails shows a message instead of a blank app; hosting headers
  (CSP, no framing) in `web/public/_headers`. The text-item helpers are
  shared with the desktop (`src/lib/serviceItemContent.ts`).
- **`LibrarySource` gained `getChart(songId)`**, implemented for the desktop
  too.
- **Checked** against a local server in the browser at phone size: sign-in,
  download, chords, a service planned with a key change (chords moved G→A),
  a words edit (the server kept the chart), and another device's change
  showing up without a reload. The desktop's own sync code pulls a
  web-planned service intact, date included (`e2e.rs`,
  `a_service_planned_on_the_web`). Not yet tried: real Google sign-in,
  deploying, the CSP on signed-in pages in a production build.

### Deploying

Live at `app.churchslides.app` since 2026-11-26: a Cloudflare Worker with
static assets (`web/wrangler.jsonc`), beside the account server at
`api.churchslides.app`, which accepts the web app's client metadata
document (`web/public/oauth/web-app-client.json`). `web/public/_headers`
sets the security policy and caching. To deploy, from `main`:

```sh
pnpm web:build && pnpm web:deploy
```

### Not yet

- The team page (members, invites) and the chord editor.
- Several tabs share one library without coordinating (each keeps its own
  copy in memory); one tab syncing for all is the fix.
