/**
 * FreeShow: `.show` files (JSON `[id, show]`) and `projects.json`, as
 * `other_apps.rs` reads them. A slide's `group` ("Verse", "Chorus") names a
 * section, its `children` are the section's further slides, and the active
 * layout is the order it's sung in. Chords sit on each line, by position.
 */

import { isChordToken } from "@/lib/chords/chord";
import { CHART_VERSION, type ChartLine, type ChartSection } from "@/lib/chords/chart";
import type { ImportFileResult, ImportedChartSong } from "@/lib/chords/import/types";
import type { OtherAppImport } from "@/lib/otherApps/types";

interface FsLine {
  text?: { value?: string }[];
  chords?: { pos?: number; key?: string }[];
}

interface FsSlide {
  group?: string | null;
  globalGroup?: string | null;
  children?: string[];
  items?: { lines?: FsLine[] }[];
}

interface FsShow {
  name?: string;
  settings?: { activeLayout?: string };
  meta?: { title?: string; author?: string; copyright?: string; CCLI?: string; key?: string };
  slides?: Record<string, FsSlide>;
  layouts?: Record<string, { slides?: { id: string; disabled?: boolean }[] }>;
}

interface FsProject {
  name?: string;
  created?: number;
  modified?: number;
  used?: number;
  shows?: { id: string; type?: string }[];
}

export interface FreeShowData {
  shows: [string, FsShow][];
  projects: { projects?: Record<string, FsProject> } | null;
}

/** The ids FreeShow gives its built-in examples. */
const EXAMPLE_ID = "default";

function lineOf(line: FsLine): ChartLine {
  const text = (line.text ?? []).map((t) => t.value ?? "").join("");
  const chords = (line.chords ?? [])
    .filter((c) => c.key && isChordToken(c.key))
    .map((c) => ({ pos: Math.max(0, Math.min(c.pos ?? 0, text.length)), chord: c.key! }))
    .sort((a, b) => a.pos - b.pos);
  return { text: text.trim(), chords };
}

function stanzaOf(slide: FsSlide | undefined): ChartLine[] {
  return (slide?.items ?? []).flatMap((it) => (it.lines ?? []).map(lineOf)).filter((l) => l.text !== "");
}

function toSong(show: FsShow): ImportedChartSong {
  const slides = show.slides ?? {};
  const layouts = show.layouts ?? {};
  const layout = layouts[show.settings?.activeLayout ?? ""] ?? Object.values(layouts)[0];
  const sequence = (layout?.slides ?? []).filter((s) => !s.disabled && slides[s.id]).map((s) => s.id);

  // Sections in the order they're first sung; a group used for several
  // different slides ("Verse" ×4) gets numbered so each one stays distinct.
  const firstSeen = [...new Set(sequence)];
  const groupCount = new Map<string, number>();
  for (const id of firstSeen) {
    const g = slides[id]!.group?.trim() || "Verse";
    groupCount.set(g, (groupCount.get(g) ?? 0) + 1);
  }
  const seenOfGroup = new Map<string, number>();
  const labelOf = new Map<string, string>();
  const sections: ChartSection[] = [];
  for (const id of firstSeen) {
    const slide = slides[id]!;
    const group = slide.group?.trim() || "Verse";
    const n = (seenOfGroup.get(group) ?? 0) + 1;
    seenOfGroup.set(group, n);
    const label = (groupCount.get(group) ?? 0) > 1 && !/\d$/.test(group) ? `${group} ${n}` : group;
    labelOf.set(id, label);
    const stanzas = [slide, ...(slide.children ?? []).map((c) => slides[c])].map(stanzaOf).filter((s) => s.length > 0);
    if (stanzas.length > 0) sections.push({ label, stanzas });
  }
  const order = sequence.map((id) => labelOf.get(id)!);
  const meta = show.meta ?? {};
  return {
    title: meta.title?.trim() || show.name?.trim() || "Untitled",
    artist: meta.author?.trim() || null,
    ccli: meta.CCLI?.trim() || null,
    copyright: meta.copyright?.trim() || null,
    chart: { version: CHART_VERSION, key: meta.key?.trim() || null, tempo: null, time: null, sections },
    // Only an order that repeats or reorders says anything beyond the written one.
    order: order.length !== firstSeen.length ? order : null,
    format: "freeshow",
    warnings: [],
  };
}

export function fromFreeShow(data: FreeShowData): OtherAppImport {
  const results: ImportFileResult[] = [];
  const keyOf = new Map<string, string>();
  const usedNames = new Set<string>();
  for (const [id, show] of data.shows) {
    if (id === EXAMPLE_ID) continue;
    const song = toSong(show);
    // The preview lists songs by this name; two shows can share a title.
    let name = song.title;
    for (let n = 2; usedNames.has(name); n++) name = `${song.title} (${n})`;
    usedNames.add(name);
    if (song.chart.sections.length === 0) {
      results.push({ fileName: name, songs: [], error: "No slide text in this show" });
      continue;
    }
    keyOf.set(id, name);
    results.push({ fileName: name, songs: [song], error: null });
  }
  const setlists = Object.entries(data.projects?.projects ?? {})
    .filter(([id]) => id !== EXAMPLE_ID)
    .map(([, p]) => ({
      name: p.name?.trim() || "FreeShow project",
      date: Math.max(p.used ?? 0, p.modified ?? 0, p.created ?? 0) || null,
      songs: (p.shows ?? [])
        .filter((s) => !s.type || s.type === "show")
        .map((s) => keyOf.get(s.id))
        .filter((k): k is string => k !== undefined),
    }))
    .filter((s) => s.songs.length > 0);
  return { results, setlists };
}
