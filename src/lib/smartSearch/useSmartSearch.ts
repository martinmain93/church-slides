/**
 * The library search box's results, kept current as the query changes.
 *
 * Debounced (each search may ask the engine), and while the engine builds a
 * library's first index — seconds, once per install — the keyword results
 * show and the search re-asks until meaning results arrive.
 */

import { useEffect, useState } from "react";

import { requestSidecar } from "@/lib/sidecarClient";
import { smartSearch, type SearchDeps, type SmartResult } from "@/lib/smartSearch/search";
import { listChartMeta, listRecentServices, listSongs, searchSongs } from "@/lib/tauri";
import type { ChartMeta } from "@/lib/smartSearch/musicalFit";

const DEBOUNCE_MS = 150;
const INDEXING_RETRY_MS = 2000;
/** An engine that hasn't answered by now is starting or missing: show the
 *  keyword half rather than wait. */
const ENGINE_TIMEOUT_MS = 4000;

const semanticSearch: SearchDeps["semanticSearch"] = async (texts, limit) => {
  const reply = await Promise.race([
    requestSidecar({ kind: "semantic_search", texts, limit }, { timeoutMs: ENGINE_TIMEOUT_MS }),
    new Promise<never>((_, reject) => setTimeout(() => reject(new Error("engine not ready")), ENGINE_TIMEOUT_MS)),
  ]);
  return { status: reply.status, hits: reply.hits ?? [] };
};

export function useSmartSearch(
  query: string,
  reloadKey: number,
): { result: SmartResult | null; error: string | null } {
  const [result, setResult] = useState<SmartResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Key, tempo and time signature per song: read once, and again when the
  // library changes (a chart edited, songs imported).
  const [chartMeta, setChartMeta] = useState<Record<string, ChartMeta> | null>(null);
  useEffect(() => {
    let cancelled = false;
    listChartMeta()
      .then((m) => !cancelled && setChartMeta(m))
      .catch(() => !cancelled && setChartMeta({}));
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  useEffect(() => {
    if (query.trim() === "") {
      setResult(null);
      setError(null);
      return;
    }
    if (chartMeta === null) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const deps: SearchDeps = { searchSongs, listSongs, chartMeta, listRecentServices, semanticSearch };
    const run = () => {
      smartSearch(query, deps)
        .then((r) => {
          if (cancelled) return;
          setResult(r);
          setError(null);
          if (r.meaning === "indexing") timer = setTimeout(run, INDEXING_RETRY_MS);
        })
        .catch((err) => {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : String(err));
          setResult(null);
        });
    };
    timer = setTimeout(run, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, chartMeta, reloadKey]);

  return { result, error };
}
