/**
 * OpenLP: rows of its song database as `other_apps.rs` reads them. Lyrics are
 * OpenLP's own XML (`<verse type="v" label="1">`), with chords inline as
 * `[G]` and `[---]` marking an optional slide split; `verse_order` is the
 * arrangement ("v1 c1 v2 c1").
 */

import { CHART_VERSION, type ChartSection } from "@/lib/chords/chart";
import type { ImportedChartSong } from "@/lib/chords/import/types";
import { parseInlineChords } from "@/lib/chords/import/text";
import type { OtherAppImport } from "@/lib/otherApps/types";

interface OpenLpSong {
  id: number;
  title: string;
  lyrics: string;
  verse_order: string | null;
  ccli_number: string | null;
  copyright: string | null;
  authors: string[];
}

export interface OpenLpData {
  songs: OpenLpSong[];
}

const VERSE_TYPES: Record<string, string> = {
  v: "Verse", c: "Chorus", b: "Bridge", p: "Pre-Chorus", i: "Intro", e: "Ending", o: "Other",
};

function labelFor(type: string, label: string): string {
  const base = VERSE_TYPES[type.toLowerCase()] ?? "Verse";
  return label ? `${base} ${label}` : base;
}

/** OpenLP's display formatting tags: `{r}red{/r}`, `{st}`, `{br}`. */
const FORMATTING_TAG = /\{\/?[a-z]{1,4}\}/gi;

function toSong(row: OpenLpSong): ImportedChartSong {
  const warnings: string[] = [];
  const sections: ChartSection[] = [];
  const byKey = new Map<string, string>();
  const doc = new DOMParser().parseFromString(row.lyrics, "application/xml");
  if (doc.getElementsByTagName("parsererror").length > 0) {
    warnings.push("OpenLP's lyrics for this song couldn't be read");
  }
  for (const verse of Array.from(doc.getElementsByTagName("verse"))) {
    const type = verse.getAttribute("type") ?? "v";
    const num = verse.getAttribute("label") ?? "";
    const label = labelFor(type, num);
    byKey.set(`${type}${num}`.toLowerCase(), label);
    const text = (verse.textContent ?? "").replace(FORMATTING_TAG, "").replace(/\r/g, "");
    const stanzas = text
      .split(/\n?\[---\]\n?|\n\s*\n/)
      .map((block) => block.split("\n").map(parseInlineChords).filter((l) => l.text.trim() !== ""))
      .filter((s) => s.length > 0);
    if (stanzas.length > 0) sections.push({ label, stanzas });
  }

  let order: string[] | null = null;
  if (row.verse_order?.trim()) {
    order = [];
    for (const tok of row.verse_order.trim().split(/\s+/)) {
      const label = byKey.get(tok.toLowerCase());
      if (label) order.push(label);
      else warnings.push(`Verse order refers to "${tok}", which isn't in the song`);
    }
  }

  return {
    title: row.title.trim() || "Untitled",
    artist: row.authors.length ? row.authors.join(", ") : null,
    ccli: row.ccli_number?.trim() || null,
    copyright: row.copyright?.trim() || null,
    chart: { version: CHART_VERSION, key: null, tempo: null, time: null, sections },
    order: order && order.length > 0 ? order : null,
    format: "openlp",
    warnings,
  };
}

export function fromOpenLp(data: OpenLpData): OtherAppImport {
  const results = data.songs.map((row) => {
    const fileName = `${row.title} #${row.id}`;
    const song = toSong(row);
    return song.chart.sections.length > 0
      ? { fileName, songs: [song], error: null }
      : { fileName, songs: [], error: "No lyrics in this song" };
  });
  // OpenLP keeps services in files saved wherever the operator chose, not in
  // its library, so there are none to bring across from here.
  return { results, setlists: [] };
}
