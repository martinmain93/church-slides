/**
 * OpenLP service files (`.osz`): a ZIP whose `service_data.osj` is a JSON
 * list — a header, then one entry per service item. A song item carries its
 * whole song as OpenLyrics XML (`header.xml_version`), so it reads like a
 * dropped OpenLyrics file; other items (Bible passages, images, custom
 * slides) are left out.
 */

import type { ImportFileResult } from "@/lib/chords/import/types";
import { parseOpenLyrics } from "@/lib/chords/import/xml";
import type { ImportedSetlist } from "@/lib/otherApps/types";
import { readZip } from "@/lib/otherApps/zip";

interface OszItem {
  serviceitem?: {
    header?: { name?: string; title?: string; xml_version?: string | null };
  };
}

export function importOsz(fileName: string, bytes: Uint8Array): { results: ImportFileResult[]; setlists: ImportedSetlist[] } {
  let items: OszItem[];
  try {
    const files = readZip(bytes);
    const osj = [...files.entries()].find(([n]) => n.toLowerCase().endsWith(".osj"))?.[1];
    if (!osj) throw new Error("no service data");
    items = JSON.parse(new TextDecoder().decode(osj)) as OszItem[];
  } catch {
    return { results: [{ fileName, songs: [], error: "Couldn't open this OpenLP service file" }], setlists: [] };
  }

  const results: ImportFileResult[] = [];
  const order: string[] = [];
  items.forEach((item, i) => {
    const h = item.serviceitem?.header;
    if (h?.name !== "songs" || !h.xml_version) return;
    const title = h.title?.trim() || `Song ${i}`;
    const key = `${fileName} › ${i}. ${title}`;
    const song = parseOpenLyrics(h.xml_version, title);
    results.push(song ? { fileName: key, songs: [song], error: null } : { fileName: key, songs: [], error: "Couldn't read this song" });
    if (song) order.push(key);
  });

  const name = (fileName.split(/[/\\]/).pop() ?? fileName).replace(/\.osz$/i, "");
  return { results, setlists: order.length > 0 ? [{ name, songs: order }] : [] };
}
