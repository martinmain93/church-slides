# Record formats

What the `fields` of each sync record hold (see the README, "Sync"). The
server stores fields without reading them, so this page is the contract
between clients: a setlist planned on a phone must arrive intact on the
slides computer.

Every format here has a sample in [`records/`](records/). The desktop app's
Rust conversion (`src-tauri/src/library_sync`) and the TypeScript one
(`src/core/records.ts`) are both tested against the samples, so a change to
either that breaks the other fails a test.

## Rules for every record

- **Absent means empty.** A field with no value is left out, never sent as
  `null` inside `fields`. (In a push, `null` in `set` removes a field.)
  Empty lists are left out too.
- **Times** are Unix milliseconds.
- **Some fields hold JSON as text** (`"look": "{\"theme\":…}"`). They are
  the app's own formats, stored as text in its database; clients keep them
  as text and only parse the ones they understand. A client that doesn't
  understand a field must not change it: push only the fields you changed.
- **Fields a client doesn't know** (from a newer client) are kept as they
  are and never sent back as removed.

## `song`

| Field | Type | |
| --- | --- | --- |
| `title` | string | Required |
| `body_text` | string | Required. The words, in the song format (`docs/SONG_FORMAT.md`) |
| `slide_order` | string | Section order, e.g. `"V1, C, V2, C"`. Absent: each section once |
| `author`, `ccli`, `copyright` | string | |
| `default_tempo_wps` | number | Words a second the AI expects |
| `created_at`, `updated_at` | number | Required |
| `chart` | object | Chords. `chart_json` (text: the chart), `original_key`, `source_format`, `updated_at` |
| `scriptures` | list | Bible passages linked to the song: `id`, `reference`, `translation`, `section_key`, `anchor_lyric`, `every_repeat` (0/1), `position`, `source`, `strength`, `note` |
| `references` | list | Data read from lead sheets: `id`, `kind`, `source_name`, `data_json` (text), `summary_json` (text), `created_at`, `updated_at` |

A client that edits a song's words without understanding `chart` leaves it
alone; chords are placed by words, and the desktop app realigns them.

## `setlist`

A service and its items, in order.

| Field | Type | |
| --- | --- | --- |
| `name` | string | Required |
| `created_at` | number | Required |
| `last_used_at` | number | When it was last run |
| `items` | list | The items, below, sorted by `position` |
| `refs` | list | `"song:<id>"` for every song an item uses, sorted, no repeats. The server keeps those songs from being deleted (README, "References keep records alive") |

Each item:

| Field | Type | |
| --- | --- | --- |
| `id` | string | Required |
| `kind` | string | `song`, `scripture` or `text`. Required |
| `position` | number | Required; 0, 1, 2… |
| `song_id` | string | `song` items: the library song |
| `song_key` | string | The key it's played in for this service ("Bb") |
| `slides_override` | text | Slides edited for this service only: `[{"section":{"type":"verse","number":1},"lyrics":"…"}]` |
| `content` | text | `scripture`: `{"reference","translation"?,"abbreviation"?,"verses"?,"own_text"?,…}`; `text`: `{"title"}` |
| `look` | text | This item's own display look (as `look.display`) |

A scripture item may carry only its reference (`{"reference":"John 3:16"}`):
the slides computer fills in the verse text from its own Bibles, which are
licensed per computer.

## `reading`

`title`, `body_text`, `created_at`, `updated_at`: a prepared reading.

## `service`

One service that took place (history; never deleted): `setlist_id`,
`started_at`, `songs` (the song ids sung, sorted).

## `look`

Id `default`. `display` (text: `{"theme","font","size","weight"}`),
`scripture` (text, same shape: scripture's own look; absent means the display
look), `updated_at`.

## `performance`

Timing the AI learned from one performance of a song: `song_id`,
`started_at_ms`, `provenance`, `weight`, `data_json` (text), `updated_at_ms`.
Only finished performances are sent, and they never change.
