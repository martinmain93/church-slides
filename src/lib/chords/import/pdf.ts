/**
 * Read a chord-sheet PDF: extract positioned text with PDF.js, lay it out as
 * a text chart (`pdfLayout.ts`), then read that like any other text file.
 *
 * PDF.js is loaded only when a PDF is imported — it's large, and most
 * sessions never need it.
 */

import { fromFileName } from "@/lib/chords/import/fileName";
import { pagesToChartText, type PdfHyphenRect, type PdfTextItem } from "@/lib/chords/import/pdfLayout";
import { parseTextChart } from "@/lib/chords/import/text";
import type { ImportFileResult } from "@/lib/chords/import/types";

/** The slice of the PDF.js API we use, so Node scripts can pass the legacy build. */
export interface PdfJsLike {
  OPS?: {
    save?: number;
    restore?: number;
    transform?: number;
    constructPath?: number;
    fill?: number;
  };
  getDocument(src: { data: Uint8Array; isEvalSupported?: boolean; useSystemFonts?: boolean }): {
    promise: Promise<{
      numPages: number;
      getPage(n: number): Promise<{
        getTextContent(): Promise<{ items: unknown[] }>;
        getOperatorList?(): Promise<{ fnArray: number[]; argsArray: unknown[] }>;
      }>;
      destroy(): Promise<void>;
    }>;
  };
}

let browserPdfJs: Promise<PdfJsLike> | null = null;

async function loadBrowserPdfJs(): Promise<PdfJsLike> {
  browserPdfJs ??= (async () => {
    // The legacy build: pdf.js 4's modern build calls Promise.withResolvers
    // (Safari 17.4+), and the worker is shipped as a URL, so Vite never
    // transpiles it. The legacy build carries its own polyfills and runs in
    // the web view on older Macs, where the modern one failed every import.
    const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
    const worker = await import("pdfjs-dist/legacy/build/pdf.worker.min.mjs?url");
    pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
    return pdfjs as unknown as PdfJsLike;
  })();
  return browserPdfJs;
}

interface RawItem {
  str?: string;
  transform?: number[];
  width?: number;
  height?: number;
}

type Matrix6 = [number, number, number, number, number, number];

function multMatrix(m1: Matrix6, m2: Matrix6): Matrix6 {
  return [
    m1[0] * m2[0] + m1[2] * m2[1],
    m1[1] * m2[0] + m1[3] * m2[1],
    m1[0] * m2[2] + m1[2] * m2[3],
    m1[1] * m2[2] + m1[3] * m2[3],
    m1[0] * m2[4] + m1[2] * m2[5] + m1[4],
    m1[1] * m2[4] + m1[3] * m2[5] + m1[5],
  ];
}

function applyMatrix(m: Matrix6, x: number, y: number): [number, number] {
  return [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
}

async function extractPageHyphens(
  page: { getOperatorList?(): Promise<{ fnArray: number[]; argsArray: unknown[] }> },
  opsConst?: PdfJsLike["OPS"],
): Promise<PdfHyphenRect[]> {
  if (!page.getOperatorList) return [];
  try {
    const ops = await page.getOperatorList();
    const saveOp = opsConst?.save ?? 10;
    const restoreOp = opsConst?.restore ?? 11;
    const transformOp = opsConst?.transform ?? 12;
    const constructPathOp = opsConst?.constructPath ?? 91;
    const fillOp = opsConst?.fill ?? 22;
    let ctm: Matrix6 = [1, 0, 0, 1, 0, 0];
    const stack: Matrix6[] = [];
    const hyphens: PdfHyphenRect[] = [];
    for (let i = 0; i < ops.fnArray.length; i++) {
      const fn = ops.fnArray[i];
      const args = ops.argsArray[i] as unknown[];
      if (fn === saveOp) stack.push([...ctm]);
      else if (fn === restoreOp) ctm = stack.pop() ?? [1, 0, 0, 1, 0, 0];
      else if (fn === transformOp && Array.isArray(args) && args.length >= 6) {
        ctm = multMatrix(ctm, args as Matrix6);
      } else if (
        fn === constructPathOp &&
        Array.isArray(args) &&
        Array.isArray(args[0]) &&
        (args[0] as number[]).length === 1 &&
        (args[0] as number[])[0] === 19 &&
        Array.isArray(args[1]) &&
        ops.fnArray[i + 1] === fillOp
      ) {
        const [x, y, w, h] = args[1] as [number, number, number, number];
        const [x0, y0] = applyMatrix(ctm, x, y);
        const [x1, y1] = applyMatrix(ctm, x + w, y + h);
        const bx0 = Math.min(x0, x1);
        const bx1 = Math.max(x0, x1);
        const by0 = Math.min(y0, y1);
        const by1 = Math.max(y0, y1);
        if (by1 - by0 < 1.2 && bx1 - bx0 > 1.2 && bx1 - bx0 < 6.0) {
          hyphens.push({ x: (bx0 + bx1) / 2, y: (by0 + by1) / 2 });
        }
      }
    }
    return hyphens;
  } catch {
    return [];
  }
}

/** Every text run on every page, with position and size. */
export async function extractPdfItems(bytes: Uint8Array, pdfjs?: PdfJsLike): Promise<PdfTextItem[][]> {
  const { pages } = await extractPdfItemsAndHyphens(bytes, pdfjs);
  return pages;
}

export async function extractPdfItemsAndHyphens(
  bytes: Uint8Array,
  pdfjs?: PdfJsLike,
): Promise<{ pages: PdfTextItem[][]; pageHyphens: PdfHyphenRect[][] }> {
  const lib = pdfjs ?? (await loadBrowserPdfJs());
  // PDF.js takes ownership of the buffer; hand it a copy.
  const doc = await lib.getDocument({ data: bytes.slice(), isEvalSupported: false, useSystemFonts: true }).promise;
  try {
    const pages: PdfTextItem[][] = [];
    const pageHyphens: PdfHyphenRect[][] = [];
    for (let n = 1; n <= doc.numPages; n++) {
      const page = await doc.getPage(n);
      const content = await page.getTextContent();
      const items: PdfTextItem[] = [];
      for (const raw of content.items as RawItem[]) {
        if (typeof raw.str !== "string" || !raw.transform) continue;
        const [a, b, , , x, y] = raw.transform as [number, number, number, number, number, number];
        const size = Math.hypot(a, b) || raw.height || 10;
        items.push({ str: raw.str, x, y, width: raw.width ?? 0, size });
      }
      pages.push(items);
      pageHyphens.push(await extractPageHyphens(page, lib.OPS));
    }
    return { pages, pageHyphens };
  } finally {
    await doc.destroy();
  }
}

/**
 * Words a missing ligature glyph turns up in, with the ligature filled in.
 * Some PDFs (SongSelect's among them) draw fi/fl/ff as one glyph with no
 * text behind it, so PDF.js hands back U+0000 where the letters were.
 */
const LIGATURE_WORDS = new Set(
  ("effort efforts afford afforded offend offended suffice different difference offer offering offerings suffer suffered suffering effect affection afflicted affliction " +
    "sufficient sufficiency fire fires fiery find finds found first field fields fill filled fills final finally " +
    "finished fight fights figure fix fixed confident confidence define defined infinite sacrifice sacrificed " +
    "magnificent glorified glorify unified crucified crucify justified justify sanctified sanctify satisfied " +
    "satisfy magnified magnify purified purify testify testified qualified identified fine faithful " +
    "flame flames flow flows flowing flood floods flesh fly flee flower flowers reflect reflection reflected " +
    "inflame overflow overflowing overflows fled flock float afflict conflict inflict").split(" "),
);
const LIGATURES = ["fi", "fl", "ff", "ffi", "ffl"];

/** Fill missing ligatures back into words; reports the words it had to guess. */
export function restoreLigatures(text: string): { text: string; guessed: string[] } {
  if (!text.includes("\u0000")) return { text, guessed: [] };
  const guessed: string[] = [];
  // eslint-disable-next-line no-control-regex -- U+0000 is exactly what PDF.js hands back
  const out = text.replace(/(\p{L}*)\u0000(\p{L}*)/gu, (_m, before: string, after: string) => {
    // A word split for a chord ("re - \0ection") only shows us one side.
    const fits = (lig: string) => {
      const w = `${before}${lig}${after}`.toLowerCase();
      if (LIGATURE_WORDS.has(w)) return true;
      return [...LIGATURE_WORDS].some((k) => (before === "" && k.endsWith(w)) || (after === "" && k.startsWith(w)));
    };
    const known = LIGATURES.find(fits);
    if (known) return `${before}${known}${after}`;
    // The glyph swallowed a space too: "the\0ow" is "the flow".
    const opening = LIGATURES.find((lig) => LIGATURE_WORDS.has(`${lig}${after}`.toLowerCase()));
    if (before && opening) return `${before} ${opening}${after}`;
    const word = `${before}fi${after}`;
    guessed.push(word);
    return word;
  });
  return { text: out, guessed };
}

export async function importPdfFile(
  fileName: string,
  bytes: Uint8Array,
  pdfjs?: PdfJsLike,
): Promise<ImportFileResult> {
  const named = fromFileName(fileName);
  const fallback = named.title;
  let pages: PdfTextItem[][];
  let pageHyphens: PdfHyphenRect[][];
  try {
    const extracted = await extractPdfItemsAndHyphens(bytes, pdfjs);
    pages = extracted.pages;
    pageHyphens = extracted.pageHyphens;
  } catch (err) {
    return { fileName, songs: [], error: `Couldn't open this PDF (${err instanceof Error ? err.message : err})` };
  }
  if (pages.every((p) => p.every((i) => i.str.trim() === ""))) {
    return {
      fileName,
      songs: [],
      error: "This PDF is a scanned image with no text in it — it would need OCR to import",
    };
  }
  const { text, guessed } = restoreLigatures(pagesToChartText(pages, pageHyphens));
  // A PDF is one song; if the reader found a `{new_song}`-like split it's a
  // misread, so keep only the first.
  const songs = parseTextChart(text, fallback)
    .slice(0, 1)
    .map((s) => guessed.length === 0 ? s : {
      ...s,
      warnings: [...s.warnings, `Some letters weren't readable in the PDF and were guessed: ${guessed.join(", ")}`],
    });
  if (songs.length === 0) return { fileName, songs: [], error: "No lyrics or chords found in this PDF" };
  return {
    fileName,
    songs: songs.map((s) => {
      // Sheets are usually saved as "<song>-<key>.pdf": trust the name for
      // the key only when the sheet itself doesn't say.
      if (s.chart.key || !named.key) return { ...s, format: "pdf" as const };
      return {
        ...s,
        format: "pdf" as const,
        chart: { ...s.chart, key: named.key },
        warnings: [...s.warnings, `Key ${named.key} taken from the file name`],
      };
    }),
    error: null,
  };
}
