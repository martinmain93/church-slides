# Organization libraries and sync

Status: **agreed 2026-11-18** (decisions at the end). Steps A–C built 2026-11-18;
step D is next. This is server milestone M4.

## What a volunteer sees

- **Not signed in:** the app is exactly what it is today. One library on this
  computer.
- **Signed in, in one organization:** the library screen shows the
  organization's library. Songs and services that anyone on the team adds or
  edits show up on every computer in the team. Changes made offline go up
  once the computer is back online.
- **A second library only appears when one exists.** If this computer also
  has songs of its own, or the person is in two organizations, a small
  library name at the top of the library screen becomes a switcher. Nobody
  who has only one library ever sees it.
- **Services belong to a library.** A service built in the organization's
  library uses that library's songs, and the team sees it. Nothing moves
  between libraries (server decision 3).
- **Nothing to set up for sync.** There's no Sync button and no sync
  settings. A small status in the library ("Up to date" / "3 changes waiting
  to go up") shows only when something's waiting or wrong.
- **No lyric is lost.** If two people change a song's words while one is
  offline, both get kept: the later one shows, and the other is one click away
  under the song's history.

What a person can change follows their role: owners, admins and operators edit
everything; set leaders edit services and read songs; members read.

## The one decision that shapes everything: a library is a file

Today all of the app's content lives in one `library.db`. Instead of adding a
"which library" column to every table and every query, **each library is its
own database file**:

```
<app data>/library.db                    this computer's own library and its settings (as today)
<app data>/orgs/<org id>/library.db      one per organization, synced
```

The app has one library open at a time, and switching libraries opens the
other file.

**Why:**

- **Decision 3 holds by construction.** A service can't reference another
  library's song because that song isn't in the file. No query can leak
  across libraries, because there is nothing to filter.
- **It mirrors the server,** where each organization already has its own
  database (its Durable Object).
- **Almost no code changes.** Every existing query, search, the AI engine's
  song index and the import tools keep working unchanged, pointed at a
  different file. The engine already reads the library from a path the app
  gives it, and its learned-song data already sits beside the library file,
  so it moves with the library.
- **Leaving an organization is clean:** delete its folder.

**Settings stay where they are**, in this computer's own file, whichever
library is open. The app keeps a second connection to that file while an
organization's library is open, and records which library is open there.
The engine and the AI-assistant process read that record, so they follow a
switch (and an engine restart) without being told. There's no settings
migration. The cost is that nothing can show two libraries at once, and
decision 3 says nothing should.

## Getting an existing library into the organization

Most churches already have their songs on the slides computer, so the first
question a new organization's owner will ask is "how do I get my songs in?"

**Proposed:** when an owner creates an organization on a computer that already
has songs, offer once: **"Use this computer's library for {organization}?"**
This **moves** the library, it doesn't copy it. Afterwards the computer's songs
are the organization's, and the computer's own library starts empty. Charts,
scripture links, services and history all come along.

Other team members' computers never get this offer. They download the
organization's library, and their own songs stay theirs.

This is a judgment call against decision 3. The worry behind that decision was
lyrics leaking between libraries owned by different people. The slides
computer's library is, in practice, the church's library already, so moving it
doesn't break that rule; copying it would. If the answer is no, an owner can
still bring songs in the usual ways: importing files, a ProPresenter export, or
SongSelect.

## What syncs

| Syncs | Stays on the computer |
| --- | --- |
| Songs: words, slide order, credits, CCLI | Recordings (audio) |
| Charts and keys | Settings: screens, microphone, AI options |
| Scripture links on songs | Imported Bibles (licensed per computer) |
| Services and their items (song, key, reading, text slide, look) | The semantic search index (rebuilt locally) |
| Service history: what was sung when (feeds "last presented" and later CCLI reporting) | |
| Lead-sheet references (small, derived from PDFs) | |
| **The display look** (theme, font, size, weight, and the scripture look) | |
| **Learned song timing** (`learning.db` performances) | |

**The display look becomes the library's.** Today it's kept per computer, in
the browser storage of the app's window. It moves into the library file as
one small record, so every operator in an organization projects the same look,
and this computer's own library keeps its own. Item looks on services already
live in the library and sync with them. Which screen the look appears on is a
setting and stays per computer.

**Learned timing syncs as history does:** each performance the engine records
is a row with its own id, and once it's final it never changes. So the sync
client sends new final rows up and writes other computers' rows into the
local `learning.db`, which already sits beside the library file. There are
no conflicts to resolve. Rows are tied to lyric text, so a song edited
elsewhere just stops using rows that no longer match, as it does today. The
engine already caps how many it reads per song (200, with older ones
decaying), and the server keeps the same cap per song.

## How sync works

This fills in the "Sync" section of the server plan. The unit of sync is a
**record**: a song (with its chart, scripture links and references) or a
service (with its items). History rows are append-only.

1. **Every local change is written to the library file at once, and marked to
   go up in the same transaction.** As built, triggers in an organization's
   file mark the record in `sync_dirty`, so writes from the app, the AI
   assistant's process and imports are all caught without any of them knowing
   about sync. `sync_shadow` keeps each record as the server last had it; what
   goes up is the difference. The app never waits on the network.
2. **Push:** when online, the app sends the outbox in order. The organization's
   Durable Object applies each change, gives it the next number in the
   organization's change log, and answers with that number. Sent rows leave
   the outbox.
3. **Pull:** the app asks for every change after the last number it has seen,
   and applies them. A new computer starts from zero, which downloads the
   library.
4. **Poke:** the app keeps one small socket to the organization open while it
   runs. On any change the server sends "there's change number N", and the
   app pulls. There's no polling and no Sync button. (The live set's host
   socket only exists during a service, so this socket is separate.)
5. **Conflicts are per field, and the last change wins**, so a key change on
   one laptop and a words change on another both survive. The exception is
   **song words:** if a change to the words was based on an older version than
   the server holds, the server keeps the version it's replacing as a
   **revision**. The song's history can bring it back.
6. **Deletes are tombstones** in the change log, so a computer that was
   offline for a month can't bring a deleted song back. A song used in a
   service can't be deleted (as today). The server enforces that too, across
   computers.
7. **Roles are checked per change** by the server, the same way as every other
   route. A set leader's song edit is refused, and the app shows it as
   "couldn't save: you can't edit songs in {organization}".

The protocol (`/v1/orgs/{orgId}/sync/push`, `…/sync/pull?after=N`, the poke
message, the record shapes) goes into `docs/server-protocol/` first. Decision 4
says a different server must be buildable from the spec.

### What the app needs

- Libraries opened by path; a switcher. Settings stay in the home file. (Step A: done.)
- In each organization's library file: `outbox`, `sync_state` (the last change
  number), and `server_version` on each synced record. Services and items gain
  `updated_at` (they don't have it today), and deletes write tombstones.
- A Rust sync client: the access token comes from the account code that
  already exists; plus the push/pull loop, the poke socket, and the status
  pill.
- The MCP tools and the importer work on the open library. When there are two
  libraries, the MCP tools take an optional library name.

### What the server needs

- In each OrgDO: tables for songs, charts, services, items, history, the
  change log, revisions and tombstones (the self-migrating schema already
  exists).
- Push, pull and poke routes behind the role matrix, with new permissions:
  `edit_songs` and `edit_services`.
- Size limits per record and per push. Charts are small; lead-sheet PDFs
  aren't synced, only what was read from them.

## Order of work

| Step | Builds | Done when |
| --- | --- | --- |
| A | Libraries are files; the switcher (no server). **Built 2026-11-18** | Today's app behaves the same; a second empty library can be made and switched to in a dev build; every app test passes |
| B | Protocol spec + server push/pull/poke + revisions | Server tests: two simulated devices, offline edits on both, no loss, words revision kept, role matrix refuses set-leader song edits |
| C | App sync client + organization library download. **Built 2026-11-18** (`src-tauri/src/library_sync`; tested against a local server) | Two laptops (or two app instances), offline edits on both, reconnect: no loss, conflict revision kept, this computer's own library never leaves it |
| D | "Use this computer's library" move, the look in the library, learned timing sync, history view for revisions, status pill | An owner with a real library creates an organization, and a second computer gets all of it |

Steps A and B don't depend on each other.

## As built (step C)

- **Where a service goes live is the open library,** not a setting: the
  "Services go live at" choice is gone. With this computer's own library
  open, services still go to the first organization whose role runs the live
  set, until step D moves that library in.
- **Organizations show in the library switcher once signed in,** before their
  library is downloaded. Opening one makes the file, and sync fills it.
- **Change notices** are a websocket the app window holds; the Rust loop also
  pulls every minute and sends changes about 3 seconds after they're made.
- **The status** sits under the switcher and only says something when changes
  are waiting while offline, the sign-in has lapsed, or the server refused a
  change (the change is undone here; the reason stays shown).
- Not in step C: the display look and learned timing (record types `look`
  and `performance`, step D), and a way to see or bring back revisions.

## Decisions (2026-11-18)

1. **Libraries are separate files**, not a column on every table.
2. **An owner can move an existing library into a new organization**, once,
   as a move rather than a copy.
3. **Service history, the display look and learned song timing all sync** in
   the first version, along with songs, charts, scripture links and services.
