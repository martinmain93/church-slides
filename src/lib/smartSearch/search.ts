/**
 * One search box for the library: titles, remembered lines, credits, topics,
 * occasions, musical facts and filters, without a mode switch.
 *
 *   1. Read the query (`parseQuery`): hymns, key, tempo, time signature,
 *      "by …", "new songs", "not played lately", an occasion, a song to fit
 *      with, and the topic left over. A "by …" or "fits with …" that names
 *      nothing in the library is read again as plain words ("saved by grace").
 *   2. A plain query whose top keyword hit is the title typed ("Amazing
 *      grace"), or a bare number (a CCLI number), answers exactly as the
 *      keyword search does.
 *   3. Otherwise rank by meaning (the engine's `semantic_search`), by
 *      keywords and — for "fits with …" — by key, tempo and time signature
 *      (`musicalFit`), merged by rank (reciprocal rank fusion), then filter.
 *
 * Measured on a real 326-song library: themes 0.16 -> 0.46 of the top ten
 * right, occasions 0.00 -> 0.60, titles and lyric lines unchanged
 * (research/spikes/semantic_search). Without the engine (or before its first
 * index is built) the keyword half still runs, filters included.
 */

import { isHymn, normalizeTitle } from "@/lib/smartSearch/hymns";
import { describeMeta, hasMusicalFacts, musicalFit, type ChartMeta } from "@/lib/smartSearch/musicalFit";
import {
  creditsMatch,
  hasFilters,
  keyMatches,
  normalizeTime,
  parseQuery,
  tempoMatches,
  type ParsedQuery,
  type PartKind,
} from "@/lib/smartSearch/parseQuery";
import type { ServiceRow, SongRow } from "@/lib/tauri";
import { parseChord } from "@/lib/chords/chord";

export type { ChartMeta };
export type MeaningStatus = "ready" | "indexing" | "unavailable" | "unused";

export interface SemanticHit {
  song_id: string;
  passage: string;
}

export interface SearchDeps {
  searchSongs: (query: string) => Promise<SongRow[]>;
  listSongs: () => Promise<SongRow[]>;
  /** Song id -> key, tempo and time signature, for songs with a chord chart. */
  chartMeta: Record<string, ChartMeta>;
  listRecentServices: (limit: number) => Promise<ServiceRow[]>;
  semanticSearch: (texts: string[], limit: number) => Promise<{ status: Exclude<MeaningStatus, "unused">; hits: SemanticHit[] }>;
  now?: () => number;
}

export interface SmartResult {
  parsed: ParsedQuery;
  songs: SongRow[];
  /** Song id -> why it's here: the passage it matched by meaning, or how it
   *  fits musically ("Same key (E) · 72 bpm · 4/4"). */
  reasons: Record<string, string>;
  meaning: MeaningStatus;
  /** When the service log starts, if the query filtered on it; null when
   *  nothing has been logged yet (so "not played lately" can't exclude). */
  historySince: number | null;
  /** Short facts about the answer the UI shows beside the tags ("12 songs
   *  have no tempo yet"). */
  notes: string[];
}

/** Songs found by meaning (or musical fit) alone, beyond every keyword
 *  match: the rest of the library is ranked too, but past this it's noise. */
export const MEANING_RESULTS = 25;
const RRF_K = 60;

export async function smartSearch(query: string, deps: SearchDeps): Promise<SmartResult> {
  const now = (deps.now ?? Date.now)();
  let parsed = parseQuery(query, { now: new Date(now) });
  const notes: string[] = [];

  // A bare number is a CCLI number: the keyword search reads credits.
  if (/^\s*\d{3,}\s*$/.test(query)) {
    const songs = await deps.searchSongs(query);
    return { parsed, songs, reasons: {}, meaning: "unused", historySince: null, notes };
  }

  // A title typed out wins over reading it as filters: "Old Country Hymn"
  // is that song, not "hymns" about old countries.
  const raw = await deps.searchSongs(query);
  if (raw.length > 0 && titleTier(raw[0]!.title, query) <= 1) {
    return { parsed, songs: raw, reasons: {}, meaning: "unused", historySince: null, notes };
  }

  const all = await deps.listSongs();

  // "by …" and "fits with …" must name something in this library; when they
  // don't, they were words ("saved by grace", "goes with communion").
  const ignore: PartKind[] = [];
  if (parsed.author && !all.some((s) => creditsMatch(parsed.author!, credits(s)))) ignore.push("author");
  const target = parsed.fit ? findSong(all, parsed.fit.title) : null;
  if (parsed.fit && !target) ignore.push("fit");
  if (ignore.length > 0) parsed = parseQuery(query, { ignore, now: new Date(now) });
  const filtered = hasFilters(parsed);

  const targetMeta = target ? deps.chartMeta[target.id] : undefined;
  const fitByMusic = parsed.fit?.how === "fit" && target !== null && hasMusicalFacts(targetMeta);
  const texts = [...(parsed.topic ? [parsed.topic] : []), ...(parsed.occasion?.phrases ?? [])];
  if (parsed.fit && target) {
    if (parsed.fit.how === "fit" && !fitByMusic) {
      // Nothing to match by ear: offer songs that sing about the same things.
      notes.push(`${target.title} has no key, tempo or time signature yet: matched by theme`);
      if (texts.length === 0) texts.push(...firstPassages(target.body_text, 2));
    } else if (parsed.fit.how === "fit") {
      notes.push(`Matched to ${target.title}: ${describeMeta(targetMeta!)}`);
    } else if (!targetMeta?.key) {
      notes.push(`${target.title} has no key yet`);
    }
  }

  const [keyword, meaning, played] = await Promise.all([
    filtered
      ? parsed.keywords
        ? deps.searchSongs(parsed.keywords)
        : Promise.resolve([] as SongRow[])
      : Promise.resolve(raw),
    texts.length > 0
      ? // Filtered: rank every song, so a filter never drops one that ranked low.
        deps.semanticSearch(texts, filtered ? 5000 : 200).catch(() => ({ status: "unavailable" as const, hits: [] }))
      : Promise.resolve(null),
    parsed.played ? playedWindow(parsed, deps, now) : Promise.resolve(null),
  ]);

  const byId = new Map(all.map((s) => [s.id, s]));
  const reasons: Record<string, string> = {};
  for (const h of meaning?.hits ?? []) reasons[h.song_id] = h.passage;
  const semanticIds = (meaning?.hits ?? []).map((h) => h.song_id);
  const keywordIds = keyword.map((s) => s.id);

  let musicIds: string[] = [];
  if (fitByMusic) {
    const scored = all
      .filter((s) => s.id !== target!.id)
      .map((s) => ({ id: s.id, ...musicalFit(targetMeta!, deps.chartMeta[s.id]) }))
      .filter((f) => f.score > 0)
      .sort((a, b) => b.score - a.score);
    musicIds = scored.map((f) => f.id);
    // The musical reason wins: it's what was asked.
    for (const f of scored) reasons[f.id] = f.reason;
  }

  let ranked: string[];
  if (fitByMusic) {
    ranked = texts.length > 0 ? rrf([musicIds, semanticIds, keywordIds]) : musicIds;
  } else if (texts.length > 0) {
    ranked = rrf([semanticIds, keywordIds]);
  } else if (parsed.played?.mode === "in" && played) {
    ranked = played.order; // "what did we play last Sunday": most recent first
  } else if (parsed.added) {
    ranked = [...all].sort((a, b) => b.created_at - a.created_at).map((s) => s.id); // newest first
  } else if (parsed.tempo) {
    // Fast songs fastest first, slow songs slowest first.
    const bpm = (s: SongRow) => deps.chartMeta[s.id]?.tempo ?? 0;
    const slowFirst = parsed.tempo.max !== undefined && parsed.tempo.min === undefined;
    ranked = [...all].sort((a, b) => (slowFirst ? bpm(a) - bpm(b) : bpm(b) - bpm(a))).map((s) => s.id);
  } else {
    ranked = [...all].sort((a, b) => a.title.localeCompare(b.title)).map((s) => s.id);
  }

  const sameKey = parsed.fit?.how === "sameKey" ? targetMeta?.key ?? null : null;
  const sameKeyChord = sameKey ? parseChord(sameKey) : null;
  const keep = (s: SongRow): boolean => {
    const meta = deps.chartMeta[s.id];
    if (target && s.id === target.id) return false;
    if (parsed.fit?.how === "sameKey") {
      if (!sameKeyChord) return false;
      const minor = /^(m(?!aj)|min|-)/.test(sameKeyChord.suffix);
      if (!keyMatches({ name: sameKey!, pc: sameKeyChord.root, minor }, meta?.key)) return false;
    }
    if (parsed.hymn && !isHymn(s)) return false;
    if (parsed.key && !keyMatches(parsed.key, meta?.key)) return false;
    if (parsed.tempo && !tempoMatches(parsed.tempo, meta?.tempo)) return false;
    if (parsed.time && normalizeTime(meta?.time) !== parsed.time) return false;
    if (parsed.author && !creditsMatch(parsed.author, credits(s))) return false;
    if (parsed.added && s.created_at < now - parsed.added.days * 86_400_000) return false;
    if (parsed.played && played) {
      const inWindow = played.ids.has(s.id);
      if (parsed.played.mode === "in" ? !inWindow : inWindow) return false;
    }
    return true;
  };

  // Every keyword match stays. Songs found by meaning or musical fit alone
  // stop at MEANING_RESULTS — unless a filter has already narrowed the list,
  // where the whole ranked remainder is the answer.
  const kwSet = new Set(keywordIds);
  const narrowed =
    parsed.hymn ||
    parsed.key !== null ||
    parsed.tempo !== null ||
    parsed.time !== null ||
    parsed.author !== null ||
    parsed.added !== null ||
    parsed.fit?.how === "sameKey";
  const capped = (texts.length > 0 || fitByMusic) && !narrowed;
  let rankedOnly = 0;
  const songs: SongRow[] = [];
  for (const id of ranked) {
    const s = byId.get(id);
    if (!s || !keep(s)) continue;
    if (capped && !kwSet.has(id)) {
      if (rankedOnly >= MEANING_RESULTS) continue;
      rankedOnly += 1;
    }
    songs.push(s);
  }

  if (parsed.tempo || parsed.time) {
    const missing = all.filter((s) => {
      const m = deps.chartMeta[s.id];
      return parsed.tempo ? !m?.tempo : !normalizeTime(m?.time);
    }).length;
    if (missing > 0) {
      const what = parsed.tempo ? "tempo" : "time signature";
      notes.push(`${missing} ${missing === 1 ? "song has" : "songs have"} no ${what} yet`);
    }
  }

  return {
    parsed,
    songs,
    reasons,
    meaning: meaning?.status ?? "unused",
    historySince: played?.since ?? null,
    notes,
  };
}

function credits(s: SongRow): string {
  return `${s.author ?? ""} ${s.copyright ?? ""}`;
}

/** The library song a typed title means: exact, then the title starting with
 *  it, then every word in it; the shortest title wins a tie ("Oceans" over
 *  "Oceans (Where Feet May Fail) (2)"). */
export function findSong(all: SongRow[], typed: string): SongRow | null {
  const want = normalizeTitle(typed);
  if (!want) return null;
  const words = want.split(" ");
  const tiers: [number, SongRow][] = [];
  for (const s of all) {
    const t = normalizeTitle(s.title);
    const tier =
      t === want ? 0 : t.startsWith(want) ? 1 : words.every((w) => t.split(" ").some((x) => x.startsWith(w))) ? 2 : -1;
    if (tier >= 0) tiers.push([tier, s]);
  }
  tiers.sort((a, b) => a[0] - b[0] || a[1].title.length - b[1].title.length);
  return tiers[0]?.[1] ?? null;
}

/** A song's first passages, to search for others that sing like it. */
function firstPassages(body: string, n: number): string[] {
  return body
    .split(/\n\s*\n/)
    .map((block) =>
      block
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l && !/^\[.*\]$/.test(l))
        .join(" / "),
    )
    .filter(Boolean)
    .slice(0, n);
}

/** Merge rankings by rank alone: scores from different searches don't compare. */
export function rrf(lists: string[][], k = RRF_K): string[] {
  const score = new Map<string, number>();
  for (const list of lists) {
    list.forEach((id, rank) => score.set(id, (score.get(id) ?? 0) + 1 / (k + rank + 1)));
  }
  return [...score.keys()].sort((a, b) => score.get(b)! - score.get(a)!);
}

async function playedWindow(
  parsed: ParsedQuery,
  deps: SearchDeps,
  now: number,
): Promise<{ ids: Set<string>; order: string[]; since: number | null }> {
  const p = parsed.played!;
  // All of it (a few hundred rows at most): `since` must be where the log starts.
  const services = await deps.listRecentServices(1000);
  const inWindow =
    p.services !== undefined
      ? services.slice(0, p.services)
      : p.days !== undefined
        ? services.filter((s) => s.started_at >= now - p.days! * 86_400_000)
        : services;
  const order: string[] = [];
  const ids = new Set<string>();
  for (const s of inWindow) {
    for (const id of s.song_ids) {
      if (!ids.has(id)) order.push(id);
      ids.add(id);
    }
  }
  const since = services.length > 0 ? services[services.length - 1]!.started_at : null;
  return { ids, order, since };
}

/** Same tiers as the keyword search's own ordering (`title_tier` in
 *  src-tauri/src/db.rs): 0 exactly the title, 1 the title starts with it,
 *  2 every word starts a title word, 3 matched in the lyrics. */
export function titleTier(title: string, query: string): number {
  const words = (t: string) => t.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const titleWords = words(title);
  const terms = words(query);
  const joined = titleWords.join(" ");
  const wanted = terms.join(" ");
  if (wanted && joined === wanted) return 0;
  if (wanted && joined.startsWith(wanted)) return 1;
  if (terms.length > 0 && terms.every((t) => titleWords.some((w) => w.startsWith(t)))) return 2;
  return 3;
}
