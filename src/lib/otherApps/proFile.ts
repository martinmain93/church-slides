/**
 * ProPresenter 7 files: a `.pro` presentation (protobuf `rv.data.Presentation`)
 * and a `.proplaylist` (a ZIP holding the playlist as `data` — protobuf
 * `rv.data.Playlist` — beside a `.pro` for each song in it).
 *
 * Field numbers come from the community schema greyshirtguy/ProPresenter7-Proto
 * (MIT). Only the fields a song needs are read; the result is the same shape
 * ProPresenter 22's library reads as (`propresenter.ts`), so both versions
 * become songs the same way.
 */

import type { ImportFileResult } from "@/lib/chords/import/types";
import { presentationToSong, type PpPresentation } from "@/lib/otherApps/propresenter";
import { ProtoMessage, uuidOf } from "@/lib/otherApps/protobuf";
import type { ImportedSetlist } from "@/lib/otherApps/types";
import { readZip } from "@/lib/otherApps/zip";

/** rv.data.Presentation */
const P = { name: 3, selectedArrangement: 10, arrangements: 11, cueGroups: 12, cues: 13, ccli: 14 };

/** A protobuf `.pro` rather than a ChordPro `.pro` (text): binary bytes up front. */
export function looksLikeProPresenterFile(bytes: Uint8Array): boolean {
  const head = bytes.subarray(0, 64);
  return head.length > 2 && head[0] === 0x0a && head.some((b) => b < 0x09);
}

export function readProPresentation(bytes: Uint8Array, fallbackTitle: string): PpPresentation {
  const pres = new ProtoMessage(bytes);

  // Cue id → per slide, the RTF of each text box.
  // Cue { uuid = 1; actions = 10 } → Action.slide = 23 → SlideType.presentation = 2
  // → PresentationSlide.base_slide = 1 → Slide.elements = 1 → Slide.Element.element = 1
  // → Graphics.Element.text = 13 → Graphics.Text.rtf_data = 5
  const cueText = new Map<string, string[]>();
  for (const cue of pres.messages(P.cues)) {
    const id = uuidOf(cue.message(1));
    if (!id) continue;
    const boxes = cue
      .messages(10)
      .flatMap((action) => action.message(23)?.message(2)?.message(1)?.messages(1) ?? [])
      .map((el) => el.message(1)?.message(13)?.string(5))
      .filter((rtf): rtf is string => !!rtf);
    cueText.set(id, boxes);
  }

  // CueGroup { group = 1 (Group { uuid = 1; name = 2 }); cue_identifiers = 2 }
  const groups = pres.messages(P.cueGroups).map((cg) => {
    const group = cg.message(1);
    return {
      id: uuidOf(group?.message(1) ?? null),
      name: group?.string(2) ?? null,
      slides: cg.messages(2).map((u) => cueText.get(uuidOf(u) ?? "") ?? []),
    };
  });

  // Arrangement { uuid = 1; name = 2; group_identifiers = 3 }
  const arrangements = pres.messages(P.arrangements).map((a) => ({
    id: uuidOf(a.message(1)),
    name: a.string(2),
    groups: a.messages(3).map((u) => uuidOf(u)).filter((id): id is string => id !== null),
  }));

  // CCLI { author = 1; artist_credits = 2; publisher = 4; copyright_year = 5; song_number = 6 }
  const ccli = pres.message(P.ccli);
  return {
    path: pres.string(P.name)?.trim() || fallbackTitle,
    ccli: ccli && {
      author: ccli.string(1),
      artist_credits: ccli.string(2),
      publisher: ccli.string(4),
      copyright_year: ccli.number(5),
      song_number: ccli.number(6),
    },
    selected_arrangement: uuidOf(pres.message(P.selectedArrangement)),
    groups,
    arrangements,
  };
}

function baseName(path: string): string {
  return path.split(/[/\\]/).pop() ?? path;
}

/** One `.pro` file → the import result for it. */
export function importProFile(fileName: string, bytes: Uint8Array): ImportFileResult {
  try {
    const song = presentationToSong(readProPresentation(bytes, baseName(fileName).replace(/\.pro$/i, "")));
    return song.chart.sections.length > 0
      ? { fileName, songs: [song], error: null }
      : { fileName, songs: [], error: "No slide text in this presentation" };
  } catch {
    return { fileName, songs: [], error: "Couldn't read this ProPresenter file" };
  }
}

/**
 * A `.proplaylist`: every presentation in it (each "file" named
 * `<playlist file> › <song>.pro`) and the playlist as a setlist.
 */
export function importProPlaylist(fileName: string, bytes: Uint8Array): { results: ImportFileResult[]; setlists: ImportedSetlist[] } {
  let files: Map<string, Uint8Array>;
  try {
    files = readZip(bytes);
  } catch {
    return { results: [{ fileName, songs: [], error: "Couldn't open this ProPresenter playlist" }], setlists: [] };
  }
  const results: ImportFileResult[] = [];
  const keyOf = new Map<string, string>();
  for (const [name, data] of files) {
    if (!/\.pro$/i.test(name)) continue;
    const key = `${fileName} › ${baseName(name)}`;
    const r = importProFile(key, data);
    results.push(r);
    if (r.songs.length > 0) keyOf.set(baseName(name).toLowerCase(), key);
  }

  const setlists: ImportedSetlist[] = [];
  const data = files.get("data");
  if (data) {
    try {
      // ProPresenter wraps the playlist: { application_info = 1; type = 2;
      // root = 3 (Playlist) }. A bare Playlist is read as-is.
      const doc = new ProtoMessage(data);
      const root = doc.message(3)?.string(2) != null ? doc.message(3)! : doc;
      collectPlaylists(root, keyOf, setlists);
    } catch {
      // The songs still import; only the running order is lost.
    }
  }
  // An unreadable `data` still leaves the songs in file order as one service.
  if (setlists.length === 0 && keyOf.size > 0) {
    setlists.push({ name: baseName(fileName).replace(/\.proplaylist$/i, ""), songs: [...keyOf.values()] });
  }
  return { results, setlists };
}

/**
 * Playlist { name = 2; playlists = 12 (PlaylistArray { playlists = 1 });
 * items = 13 (PlaylistItems { items = 1 }) }; PlaylistItem.presentation = 4
 * → document_path = 1 (URL: absolute_string = 1, relative_path = 2,
 * local = 4 → LocalRelativePath.path = 2).
 */
function collectPlaylists(pl: ProtoMessage, keyOf: Map<string, string>, out: ImportedSetlist[]) {
  const items = pl.message(13)?.messages(1) ?? [];
  if (items.length > 0) {
    const songs = items
      .map((item) => {
        const url = item.message(4)?.message(1);
        const path = url?.message(4)?.string(2) ?? url?.string(2) ?? url?.string(1) ?? "";
        return keyOf.get(decodeURIComponent(baseName(path)).toLowerCase());
      })
      .filter((k): k is string => k !== undefined);
    if (songs.length > 0) out.push({ name: pl.string(2)?.trim() || "ProPresenter playlist", songs });
  }
  for (const child of pl.message(12)?.messages(1) ?? []) collectPlaylists(child, keyOf, out);
}
