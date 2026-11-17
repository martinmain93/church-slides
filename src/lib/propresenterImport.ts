/**
 * ProPresenter `Export.txt` → Church Slides library JSON.
 *
 * Produces the payload the `import_library` Tauri command accepts, so a
 * ProPresenter export goes through the same import as a Church Slides
 * backup. Song IDs are a SHA-1 prefix of the title (see
 * `__tests__/propresenterImport.test.ts`), so re-importing overwrites the
 * same rows.
 */

const TITLE_PREFIX = "Title:";
const BOM = "\u{FEFF}";

export interface ImportedSong {
  title: string;
  body_text: string;
  raw_stanza_count: number;
}

export interface ImportReport {
  songs: ImportedSong[];
  skipped_empty: number;
  duplicate_titles_renamed: string[];
}

export interface LibraryExportPayload {
  schema_version: number;
  exported_at: number;
  songs: Array<{
    id: string;
    title: string;
    body_text: string;
    ccli: string | null;
    author: string | null;
    copyright: string | null;
    default_tempo_wps: number | null;
    created_at: number;
    updated_at: number;
  }>;
}


/**
 * Cheap content sniff: a ProPresenter export starts with `Title:`
 * (possibly preceded by a UTF-8 BOM); a library JSON starts with `{`.
 * Used to dispatch between the two import paths without a separate
 * UI affordance.
 */
export function looksLikeProPresenterExport(text: string): boolean {
  const stripped = text.startsWith(BOM) ? text.slice(1) : text;
  return stripped.trimStart().startsWith(TITLE_PREFIX);
}


export function parseProPresenterExport(text: string): ImportReport {
  const cleaned = (text.startsWith(BOM) ? text.slice(1) : text)
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");

  const blocks = splitIntoBlocks(cleaned);
  const songs: ImportedSong[] = [];
  let skipped_empty = 0;
  const seenCounts = new Map<string, number>();
  const renamed: string[] = [];

  for (const block of blocks) {
    const title = cleanTitle(block.titleLine);
    if (!title) {
      skipped_empty += 1;
      continue;
    }
    const stanzas = block.stanzas
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    if (stanzas.length === 0) {
      skipped_empty += 1;
      continue;
    }

    const count = (seenCounts.get(title) ?? 0) + 1;
    seenCounts.set(title, count);
    const uniqueTitle = count === 1 ? title : `${title} (${count})`;
    if (count > 1) renamed.push(uniqueTitle);

    songs.push({
      title: uniqueTitle,
      body_text: formatBody(stanzas),
      raw_stanza_count: stanzas.length,
    });
  }

  return { songs, skipped_empty, duplicate_titles_renamed: renamed };
}


export function toLibraryExport(
  report: ImportReport,
  nowMs: number = Date.now(),
): LibraryExportPayload {
  return {
    schema_version: 1,
    exported_at: nowMs,
    songs: report.songs.map((s) => ({
      id: songIdFromTitle(s.title),
      title: s.title,
      body_text: s.body_text,
      ccli: null,
      author: null,
      copyright: null,
      default_tempo_wps: null,
      created_at: nowMs,
      updated_at: nowMs,
    })),
  };
}


// --------------------------------------------------------------------- internals

interface RawBlock {
  titleLine: string;
  stanzas: string[];
}

function splitIntoBlocks(text: string): RawBlock[] {
  const lines = text.split("\n");
  const blocks: RawBlock[] = [];
  let currentTitle: string | null = null;
  let currentLines: string[] = [];

  const flush = () => {
    if (currentTitle === null) return;
    blocks.push({
      titleLine: currentTitle,
      stanzas: groupStanzas(currentLines),
    });
  };

  for (const line of lines) {
    if (line.trimStart().startsWith(TITLE_PREFIX)) {
      flush();
      currentTitle = line;
      currentLines = [];
    } else if (currentTitle !== null) {
      currentLines.push(line);
    }
  }
  flush();
  return blocks;
}


function groupStanzas(lines: string[]): string[] {
  const out: string[] = [];
  let buffer: string[] = [];
  for (const raw of lines) {
    const stripped = raw.replace(/\s+$/, "");
    if (stripped === "") {
      if (buffer.length > 0) {
        out.push(buffer.join("\n"));
        buffer = [];
      }
    } else {
      buffer.push(stripped);
    }
  }
  if (buffer.length > 0) out.push(buffer.join("\n"));
  return out;
}


function cleanTitle(line: string): string {
  const trimmed = line.trim();
  if (!trimmed.toLowerCase().startsWith(TITLE_PREFIX.toLowerCase())) {
    return trimmed;
  }
  return trimmed.slice(TITLE_PREFIX.length).trim();
}


function normalizeForMatch(stanza: string): string {
  return stanza
    .toLowerCase()
    .replace(/[^\w\s']/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}


function detectChorus(stanzas: string[]): string | null {
  const counter = new Map<string, number>();
  for (const stanza of stanzas) {
    const norm = normalizeForMatch(stanza);
    if (norm.split(/\s+/).filter(Boolean).length >= 5) {
      counter.set(norm, (counter.get(norm) ?? 0) + 1);
    }
  }
  if (counter.size === 0) return null;
  let best: [string, number] | null = null;
  for (const entry of counter.entries()) {
    if (best === null || entry[1] > best[1]) best = entry;
  }
  if (best === null || best[1] < 2) return null;
  return best[0];
}


function formatBody(stanzas: string[]): string {
  const chorusKey = detectChorus(stanzas);
  const out: string[] = [];
  let verseNum = 0;
  for (const stanza of stanzas) {
    const key = normalizeForMatch(stanza);
    if (chorusKey !== null && key === chorusKey) {
      out.push("[chorus]");
    } else {
      verseNum += 1;
      out.push(`[verse ${verseNum}]`);
    }
    out.push(stanza);
    out.push("");
  }
  while (out.length > 0 && out[out.length - 1] === "") out.pop();
  return out.join("\n") + "\n";
}


/**
 * Deterministic ID — matches the Python importer's
 * `song_pp_<sha1(title.lower())[:16]>` so re-imports overwrite the
 * same rows whether they come from the desktop app or the CLI.
 */
function songIdFromTitle(title: string): string {
  const digest = sha1Hex(title.toLowerCase());
  return `song_pp_${digest.slice(0, 16)}`;
}


// --- SHA-1 (32-bit pure JS) -------------------------------------------------
// We avoid `crypto.subtle` because it's async; the importer is sync and
// stays in a single function call. The Python importer uses
// `hashlib.sha1(...).hexdigest()[:16]`; this implementation matches
// byte-for-byte against UTF-8-encoded input.

function sha1Hex(input: string): string {
  const bytes = new TextEncoder().encode(input);
  const h0 = 0x67452301;
  const h1 = 0xefcdab89;
  const h2 = 0x98badcfe;
  const h3 = 0x10325476;
  const h4 = 0xc3d2e1f0;

  // Padding.
  const bitLen = bytes.length * 8;
  const padLen = (56 - ((bytes.length + 1) % 64) + 64) % 64;
  const padded = new Uint8Array(bytes.length + 1 + padLen + 8);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  // Big-endian 64-bit length at the end. JS numbers are 53-bit safe; for
  // titles this is fine.
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 4, bitLen >>> 0);
  view.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000));

  let H0 = h0, H1 = h1, H2 = h2, H3 = h3, H4 = h4;
  const w = new Uint32Array(80);
  for (let chunkStart = 0; chunkStart < padded.length; chunkStart += 64) {
    for (let i = 0; i < 16; i++) {
      w[i] = view.getUint32(chunkStart + i * 4);
    }
    for (let i = 16; i < 80; i++) {
      const v = w[i - 3]! ^ w[i - 8]! ^ w[i - 14]! ^ w[i - 16]!;
      w[i] = ((v << 1) | (v >>> 31)) >>> 0;
    }
    let a = H0, b = H1, c = H2, d = H3, e = H4;
    for (let i = 0; i < 80; i++) {
      let f: number;
      let k: number;
      if (i < 20) {
        f = (b & c) | (~b & d);
        k = 0x5a827999;
      } else if (i < 40) {
        f = b ^ c ^ d;
        k = 0x6ed9eba1;
      } else if (i < 60) {
        f = (b & c) | (b & d) | (c & d);
        k = 0x8f1bbcdc;
      } else {
        f = b ^ c ^ d;
        k = 0xca62c1d6;
      }
      const temp =
        ((((a << 5) | (a >>> 27)) >>> 0) + f + e + k + w[i]!) >>> 0;
      e = d;
      d = c;
      c = ((b << 30) | (b >>> 2)) >>> 0;
      b = a;
      a = temp;
    }
    H0 = (H0 + a) >>> 0;
    H1 = (H1 + b) >>> 0;
    H2 = (H2 + c) >>> 0;
    H3 = (H3 + d) >>> 0;
    H4 = (H4 + e) >>> 0;
  }
  return [H0, H1, H2, H3, H4]
    .map((n) => n.toString(16).padStart(8, "0"))
    .join("");
}
