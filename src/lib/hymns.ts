/**
 * Hymns from the Open Hymnal Project, searched and added on demand.
 *
 * The sidecar does the fetching (openhymnal.org serves plain HTTP with no
 * CORS headers, so the webview can't), converts each hymn's score into song
 * text, and decides its copyright status — see `sidecar/auto_presenter/hymns`.
 * Nothing from the Open Hymnal ships in the app.
 */

import { requestSidecar } from "@/lib/sidecarClient";
import { createSong } from "@/lib/tauri";
import type { HymnDetail, HymnSummary } from "@/types/generated";

export type { HymnDetail, HymnSummary };

export interface HymnSearch {
  results: HymnSummary[];
  /** When the Open Hymnal's index was fetched (ms), if it was. */
  fetchedAt: number | null;
  error: string | null;
}

export async function searchHymns(query: string, refresh = false): Promise<HymnSearch> {
  try {
    const reply = await requestSidecar(
      { kind: "search_hymns", query, refresh },
      // The first search of the week fetches the site's index (~1 MB).
      { timeoutMs: 90_000 },
    );
    return {
      results: reply.results ?? [],
      fetchedAt: reply.fetched_at ? reply.fetched_at * 1000 : null,
      error: reply.error ?? null,
    };
  } catch (err) {
    return { results: [], fetchedAt: null, error: unavailable(err) };
  }
}

export async function getHymn(
  hymnId: string,
): Promise<{ hymn: HymnDetail | null; error: string | null }> {
  try {
    const reply = await requestSidecar({ kind: "get_hymn", hymn_id: hymnId }, { timeoutMs: 60_000 });
    return { hymn: reply.hymn ?? null, error: reply.error ?? null };
  } catch (err) {
    return { hymn: null, error: unavailable(err) };
  }
}

/** Add a fetched hymn to the library. Resolves to the new song's id. */
export async function addHymnToLibrary(hymn: HymnDetail): Promise<string> {
  return createSong({
    title: hymn.title,
    body_text: hymn.body_text,
    author: hymn.author || null,
    copyright: hymn.copyright || null,
    slide_order: hymn.slide_order || null,
  });
}

function unavailable(err: unknown): string {
  const text = String(err);
  return /sidecar unavailable|not connected|ECONNREFUSED|WebSocket/i.test(text)
    ? "Hymn search needs the app's engine, which isn't running."
    : text;
}
