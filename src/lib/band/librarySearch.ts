/**
 * Library search for the leader's screen, done entirely on the device.
 *
 * The desktop app sends the whole library once (a few hundred songs is a few
 * hundred KB). It's indexed here and searched on every keystroke, so results
 * never wait on church Wi-Fi.
 *
 * Ranking, best first:
 *   1. title starts with the query           ("wor" → "Worthy Is The Lamb")
 *   2. every query word starts a title word  ("lamb wor" → "Worthy Is The Lamb")
 *   3. title contains the query
 *   4. the first line starts with it          (people search by opening line)
 *   5. the lyrics contain the query as a phrase
 *   6. the lyrics contain every query word
 * Within a rank, songs the church plays more come first, then A–Z. A single
 * letter only matches titles starting with it — a one-letter lyrics search
 * would match everything.
 */

export interface LibrarySong {
  id: string;
  title: string;
  author: string | null;
  /** Song body in the app's format (section tags included). */
  body: string;
  /** When a setlist with this song was last used (ms), or null if never. */
  lastUsed: number | null;
  /** How many setlists include it. */
  uses: number;
  key: string | null;
  /** From the chart; absent from apps older than "fits next". */
  tempo?: number | null;
  time?: string | null;
  hasChart: boolean;
}

interface Indexed {
  song: LibrarySong;
  title: string;
  titleWords: string[];
  firstLine: string;
  /** Normalised lyrics, words separated by single spaces, padded with spaces. */
  lyrics: string;
  lyricWords: Set<string>;
}

export interface LibraryIndex {
  entries: Indexed[];
}

/** Lowercase, no accents, no punctuation, single spaces. */
export function normalize(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[‘’']/g, "")
    .replace(/(\d)[,.](?=\d{3}\b)/g, "$1")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

function lyricsOf(body: string): string[] {
  return body
    .split("\n")
    .filter((l) => !/^\s*\[[^\]]+\]\s*$/.test(l))
    .map((l) => l.trim())
    .filter(Boolean);
}

export function buildLibraryIndex(songs: LibrarySong[]): LibraryIndex {
  return {
    entries: songs.map((song) => {
      const lines = lyricsOf(song.body);
      const lyrics = normalize(lines.join(" "));
      const title = normalize(song.title);
      return {
        song,
        title,
        titleWords: title.split(" ").filter(Boolean),
        firstLine: normalize(lines[0] ?? ""),
        lyrics: ` ${lyrics} `,
        lyricWords: new Set(lyrics.split(" ").filter(Boolean)),
      };
    }),
  };
}

function rank(e: Indexed, q: string, words: string[]): number {
  if (e.title.startsWith(q)) return 6;
  if (words.every((w) => e.titleWords.some((t) => t.startsWith(w)))) return 5;
  if (e.title.includes(q)) return 4;
  if (e.firstLine.startsWith(q)) return 3;
  if (e.lyrics.includes(` ${q}`)) return 2;
  if (words.every((w) => e.lyricWords.has(w) || [...e.lyricWords].some((l) => l.startsWith(w)))) return 1;
  return 0;
}

function byPopularity(a: LibrarySong, b: LibrarySong): number {
  return b.uses - a.uses || a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
}

export interface SearchHit {
  song: LibrarySong;
  /** Why it matched, for a hint under the title. */
  via: "title" | "lyrics";
}

export function searchLibrary(index: LibraryIndex, query: string, limit = 60): SearchHit[] {
  const q = normalize(query);
  if (!q) return [];
  if (q.length === 1) {
    return index.entries
      .filter((e) => e.title.startsWith(q))
      .map((e) => e.song)
      .sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }))
      .slice(0, limit)
      .map((song) => ({ song, via: "title" }));
  }
  const words = q.split(" ");
  const scored: { e: Indexed; r: number }[] = [];
  for (const e of index.entries) {
    const r = rank(e, q, words);
    if (r > 0) scored.push({ e, r });
  }
  scored.sort((a, b) => b.r - a.r || byPopularity(a.e.song, b.e.song));
  return scored.slice(0, limit).map(({ e, r }) => ({ song: e.song, via: r >= 4 ? "title" : "lyrics" }));
}

/** What to show before anything is typed: recently played, then most played. */
export function recentSongs(index: LibraryIndex, limit = 24): LibrarySong[] {
  return index.entries
    .map((e) => e.song)
    .filter((s) => s.lastUsed !== null)
    .sort((a, b) => (b.lastUsed ?? 0) - (a.lastUsed ?? 0) || byPopularity(a, b))
    .slice(0, limit);
}

/** The line of lyrics a query hit, for showing under a lyrics match. */
export function matchingLine(song: LibrarySong, query: string): string | null {
  const q = normalize(query);
  if (!q) return null;
  const words = q.split(" ");
  const lines = lyricsOf(song.body);
  const exact = lines.find((l) => normalize(l).includes(q));
  if (exact) return exact;
  // The words are spread over several lines: show the one with the most of them.
  let best: string | null = null;
  let bestCount = 0;
  for (const l of lines) {
    const n = ` ${normalize(l)}`;
    const count = words.filter((w) => n.includes(` ${w}`)).length;
    if (count > bestCount) {
      best = l;
      bestCount = count;
    }
  }
  return best;
}
