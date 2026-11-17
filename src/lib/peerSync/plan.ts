/**
 * How a setlist received from another operator lands in this library, before
 * anything is written. Uses the file importer's duplicate rules
 * (`normalizeTitle`, CCLI number first), so a song arriving by transfer and
 * the same song arriving in a file are recognised the same way.
 *
 * - Not in this library → added.
 * - In it with the same lyrics and slide order → this library's song is used
 *   (missing chords filled in), no question asked.
 * - In it with *different* lyrics → the operator chooses: keep mine, use
 *   theirs, or keep both. There is deliberately no default.
 */

import { normalizeTitle } from "@/lib/chords/import/plan";
import type { BundleSong, SetlistBundle, SongDecision, SongRow } from "@/lib/tauri";

export type SyncRow =
  | { song: BundleSong; kind: "new" }
  | { song: BundleSong; kind: "same"; match: SongRow }
  | { song: BundleSong; kind: "differs"; match: SongRow };

export type ConflictChoice = "mine" | "theirs" | "both";

/** Lyrics as a song means them: line endings, trailing space and blank-line runs don't count. */
export function normalizeBody(body: string): string {
  return body
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((l) => l.replace(/\s+/g, " ").trim())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function normalizeOrder(order: string | null | undefined): string {
  return (order ?? "").replace(/\s+/g, "").toLowerCase();
}

export function sameContent(a: { body_text: string; slide_order?: string | null }, b: { body_text: string; slide_order?: string | null }) {
  return normalizeBody(a.body_text) === normalizeBody(b.body_text) && normalizeOrder(a.slide_order) === normalizeOrder(b.slide_order);
}

export function planSetlistSync(bundle: SetlistBundle, library: SongRow[]): SyncRow[] {
  const byCcli = new Map<string, SongRow[]>();
  const byTitle = new Map<string, SongRow[]>();
  const push = (m: Map<string, SongRow[]>, k: string, s: SongRow) => m.set(k, [...(m.get(k) ?? []), s]);
  for (const s of library) {
    if (s.ccli?.trim()) push(byCcli, s.ccli.trim(), s);
    push(byTitle, normalizeTitle(s.title), s);
  }
  return bundle.songs.map((song) => {
    const candidates = (song.ccli?.trim() && byCcli.get(song.ccli.trim())) || byTitle.get(normalizeTitle(song.title)) || [];
    if (candidates.length === 0) return { song, kind: "new" };
    // A library can hold two copies of a title; an identical one wins.
    const same = candidates.find((c) => sameContent(c, song));
    if (same) return { song, kind: "same", match: same };
    return { song, kind: "differs", match: candidates[0]! };
  });
}

/** Decisions for `applySetlistBundle`, or null while a differing song is undecided. */
export function decisionsFor(rows: SyncRow[], choices: Record<string, ConflictChoice | undefined>): Record<string, SongDecision> | null {
  const out: Record<string, SongDecision> = {};
  for (const r of rows) {
    if (r.kind === "new") out[r.song.ref_id] = { kind: "new" };
    else if (r.kind === "same") out[r.song.ref_id] = { kind: "use_existing", song_id: r.match.id };
    else {
      const c = choices[r.song.ref_id];
      if (!c) return null;
      out[r.song.ref_id] =
        c === "mine"
          ? { kind: "use_existing", song_id: r.match.id }
          : c === "theirs"
            ? { kind: "replace_existing", song_id: r.match.id }
            : { kind: "keep_both" };
    }
  }
  return out;
}
