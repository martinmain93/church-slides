# Importing from other church presentation apps

Research for an optional "Bring your songs" onboarding step (2026-10-26).
Samples in `samples/` were made in the real apps from three public-domain
hymns (Amazing Grace, Be Thou My Vision, A Mighty Fortress), each with a
setlist in running order.

## Where each app keeps its data (macOS)

| App | Songs | Setlists | Notes |
|---|---|---|---|
| ProPresenter 22 (was "7") | `~/Library/Application Support/RenewedVision/ProPresenter/Workspaces/registry-v1.sqlite3` → `workspaces.root_path` → `DocumentStore/workspace-v1.sqlite3`, table `workspace_streams` rows `kind='Presentation'`, `core_path='<Library>/<Title>'` | same table, `kind='PlaylistLibrary'`, scope 0 | Every `snapshot` is a Loro CRDT document (`loro`, MIT) — decodes to JSON with the old PP7 protobuf field names. No `.pro` files on disk any more. |
| ProPresenter 7 (older) | `~/Documents/ProPresenter/Libraries/*/*.pro` (protobuf `rv.data.Presentation`) | `.proplaylist` | Schema: greyshirtguy/ProPresenter7-Proto (MIT). Untested here. |
| FreeShow 1.6 | `~/Documents/FreeShow/Shows/*.show` (folder configurable) | `~/Library/Application Support/freeshow/projects.json` | Plain JSON. |
| OpenLP 3.1 | `~/Library/Application Support/openlp/Data/songs/songs.sqlite` | `.osz` service files (wherever saved) | `.osz` is a zip with `service_data.osj` — self-contained (full lyrics + credits). |
| OpenSong | `~/Documents/OpenSong/Songs/**` | `~/Documents/OpenSong/Sets/*` | Songs already importable; sets not yet. |
| EasyWorship 6/7 | Windows: `C:\Users\Public\Documents\Softouch\EasyWorship\<profile>\v6.1\Databases\Data\Songs.db` + `SongWords.db` | schedules | SQLite + RTF. Windows only — not sampled. |
| Proclaim | Faithlife cloud | — | No export; terms forbid automated access. Unsupported — show guidance only. |

## Mapping to a Church Slides song

**ProPresenter 22** (`samples/propresenter22/amazing_grace.json`, decoded with `pp_pres.py`)
- Title: last segment of `core_path`. Don't trust `root.ccli.song_title` —
  PP's own text import set it to "Verse 1".
- Sections: `root.cue_group_placements[].meta.Kind.group.name` ("Verse 1"…),
  in order; each group's `children[].meta.uuid` are its slides (cues).
- Slide text: `root.cues[uuid].actions[0].ActionTypeData.slide.Slide.presentation.base_slide.elements[*].element.text.rtf_data`
  — RTF (stringified bytes after decode), needs RTF → plain text.
- Order: `root.arrangements` (empty in samples → written order).
- Credits: `root.ccli.{author, song_number, copyright_year, publisher}`.
- Playlist: `PlaylistLibrary` → `playlists[].children[]` with
  `Kind.__tag == 'playlist'`; items' `document_path.RelativeFilePath.local.path`
  = `Libraries/<Library>/<Title>.pro` → match `core_path` `<Library>/<Title>`;
  `arrangement_name` picks an arrangement.
- File fallback: File → Export → Presentation / Playlist / Text / Legacy Workspace.

**FreeShow**
- `.show` = `[id, show]`; `show.meta.{title, author, copyright, CCLI, key}`.
- Slides: `show.slides[id].group` ("Verse", "Chorus"…), `globalGroup`,
  text in `items[].lines[].text[].value`; `children` = continuation slides.
- Order: `show.layouts[*].slides[].id` (skip `disabled`).
- Projects: `projects.json.projects[id].{name, shows[{id, type}]}` —
  `type` absent = a show, `section` = divider (skip).
- Its own OpenSong importer left A Mighty Fortress as one slide with the
  verse digits inline — expect messy upstream data.

**OpenLP**
- `songs`: `title, lyrics (OpenLyrics-ish XML: <verse type="v" label="1">),
  verse_order ("v1 v2 c1"), ccli_number, copyright`; authors via
  `authors_songs`. Lyrics keep chords inline as `[D]word` (ChordPro) —
  keep them for band screens.
- `.osz` items: `serviceitem.header.{title, data.{authors, ccli_number,
  copyright}}`, slides in `serviceitem.data[].{verseTag, raw_slide}`.

## Import files (Song files import)
- `.pro` (ProPresenter 7 / exported from 22): protobuf `rv.data.Presentation`,
  read with a minimal wire reader (`src/lib/otherApps/proFile.ts`); field
  numbers from greyshirtguy/ProPresenter7-Proto (MIT). Told from a ChordPro
  `.pro` by its binary first bytes.
- `.proplaylist`: ZIP64, stored, `data` = `rv.data.Playlist` + one `.pro` per
  song at the root. The end-of-directory offset is wrong in real files, so
  `zip.ts` walks local headers (Python's zipfile rejects them outright).
  `data` is a wrapper `{ application_info = 1; type = 2; root = 3 (Playlist) }`;
  items carry both an absolute `file://` URL and `local.path`
  ("Libraries/Default/X.pro"), plus `arrangement` + `arrangement_name`.
  Fixture `Real export.proplaylist` is a real ProPresenter 22 export.
- `.osz` (OpenLP service): ZIP with `service_data.osj`; each song item's
  `header.xml_version` is full OpenLyrics → existing parser. One service per file.

## Ground rules (agreed in research)
- Read the church's own data files only; never decompile or patch another
  app (Renewed Vision and Faithlife terms ban reverse engineering their
  software; Faithlife also bans automated access).
- Write our own readers from the formats — don't copy OpenLP/FreeShow (GPL)
  importer code.
- Open other apps' databases read-only, on a copy (`sqlite3 .backup`), never
  in place; skip media and themes.
- Nothing leaves the machine. Ask before reading another app's folders —
  macOS also prompts for ~/Documents.
- Name apps plainly ("from ProPresenter"); no logos or implied endorsement.
