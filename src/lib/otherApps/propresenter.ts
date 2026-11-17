/**
 * ProPresenter 22: songs and playlists as `other_apps.rs` reads them out of
 * the workspace database. A presentation's groups ("Verse 1", "Chorus") are
 * the sections, each slide's text box a stanza, and an arrangement the order.
 */

import { CHART_VERSION, type ChartSection } from "@/lib/chords/chart";
import type { ImportedChartSong } from "@/lib/chords/import/types";
import { rtfToText } from "@/lib/otherApps/rtf";
import type { OtherAppImport } from "@/lib/otherApps/types";

interface PpGroup {
  id: string | null;
  name: string | null;
  /** Per slide, the RTF of each of its text boxes. */
  slides: string[][];
}

interface PpArrangement {
  id: string | null;
  name: string | null;
  groups: string[] | null;
}

export interface PpPresentation {
  /** "<Library>/<Title>". */
  path: string;
  ccli: {
    author?: string | null;
    artist_credits?: string | null;
    song_number?: number | null;
    copyright_year?: number | null;
    publisher?: string | null;
  } | null;
  selected_arrangement: string | null;
  groups: PpGroup[];
  arrangements: PpArrangement[];
}

interface PpPlaylist {
  name: string | null;
  items: { document: string; arrangement: string | null }[];
}

export interface ProPresenterData {
  presentations: PpPresentation[];
  playlists: PpPlaylist[];
}

/** "Libraries/Default/Amazing Grace.pro" → "Default/Amazing Grace" (a presentation's path). */
function documentKey(doc: string): string {
  return doc.replace(/^Libraries\//, "").replace(/\.pro$/i, "");
}

/** One presentation as a song. Shared by ProPresenter 22 (read from its
 * database) and ProPresenter 7 `.pro` files (`proFile.ts`). */
export function presentationToSong(p: PpPresentation): ImportedChartSong {
  const title = p.path.split("/").pop() ?? p.path;
  const warnings: string[] = [];
  const sections: ChartSection[] = [];
  const labelOf = new Map<string, string>();
  for (const g of p.groups) {
    const stanzas = g.slides
      .map((boxes) => boxes.map(rtfToText).filter((t) => t !== "").join("\n"))
      .filter((t) => t !== "")
      .map((t) => t.split("\n").map((text) => ({ text: text.trim(), chords: [] })));
    const label = g.name?.trim() || "Verse";
    if (g.id) labelOf.set(g.id, label);
    if (stanzas.length > 0) sections.push({ label, stanzas });
  }

  // The arrangement the song is set to, else its only one. With several and
  // none chosen, the written order is the safest default.
  const arrangement =
    p.arrangements.find((a) => a.id && a.id === p.selected_arrangement) ??
    (p.arrangements.length === 1 ? p.arrangements[0] : undefined);
  if (!arrangement && p.arrangements.length > 1) {
    warnings.push(`ProPresenter has ${p.arrangements.length} arrangements; using the written order`);
  }
  const order =
    arrangement?.groups
      ?.map((id) => labelOf.get(id))
      .filter((l): l is string => l !== undefined) ?? null;

  const ccli = p.ccli ?? {};
  const year = ccli.copyright_year ? String(ccli.copyright_year) : "";
  const copyright = [year, ccli.publisher ?? ""].join(" ").trim() || null;
  return {
    title,
    artist: ccli.author?.trim() || ccli.artist_credits?.trim() || null,
    ccli: ccli.song_number ? String(ccli.song_number) : null,
    copyright,
    chart: { version: CHART_VERSION, key: null, tempo: null, time: null, sections },
    order: order && order.length > 0 ? order : null,
    format: "propresenter",
    warnings,
  };
}

export function fromProPresenter(data: ProPresenterData): OtherAppImport {
  const results = data.presentations.map((p) => {
    const song = presentationToSong(p);
    return song.chart.sections.length > 0
      ? { fileName: p.path, songs: [song], error: null }
      : { fileName: p.path, songs: [], error: "No slide text in this presentation" };
  });
  const known = new Set(results.filter((r) => r.songs.length > 0).map((r) => r.fileName));
  const setlists = data.playlists
    .map((pl) => ({
      name: pl.name?.trim() || "ProPresenter playlist",
      songs: pl.items.map((i) => documentKey(i.document)).filter((k) => known.has(k)),
    }))
    .filter((s) => s.songs.length > 0);
  return { results, setlists };
}
