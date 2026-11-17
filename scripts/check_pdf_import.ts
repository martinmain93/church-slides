/**
 * Run the PDF chord-sheet importer over a folder and report what it made of
 * each file. Uses exactly the app's code path (PDF.js → layout → text reader
 * → song), with PDF.js's Node build.
 *
 *   node node_modules/.pnpm/vite-node@<version>/node_modules/vite-node/vite-node.mjs \
 *     scripts/check_pdf_import.ts -- "<folder>" <out-dir>
 *
 * Writes report.json (per-file title/key/CCLI/writers/…) and dump.txt (the
 * intermediate text, the chart as chords-over-lyrics, and the slide text).
 */
import { readdirSync, readFileSync, statSync, writeFileSync, mkdirSync } from "node:fs";
import { join, basename } from "node:path";
import * as pdfjs from "pdfjs-dist/legacy/build/pdf.mjs";

import { extractPdfItems, importPdfFile, type PdfJsLike } from "@/lib/chords/import/pdf";
import { pagesToChartText } from "@/lib/chords/import/pdfLayout";
import { chartToSong } from "@/lib/chords/import/toSong";
import { chartHasChords, renderChordRows } from "@/lib/chords/chart";

const [folder, outDir] = process.argv.slice(2).filter((a) => a !== "--");
mkdirSync(outDir!, { recursive: true });

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : n.toLowerCase().endsWith(".pdf") ? [p] : [];
  });
}

const seen = new Map<string, string>();
for (const p of walk(folder!).sort()) {
  const k = basename(p).toLowerCase();
  if (!seen.has(k)) seen.set(k, p);
}

const rows: Record<string, unknown>[] = [];
let dump = "";
const t0 = Date.now();
for (const [, path] of seen) {
  const name = basename(path);
  try {
    const pages = await extractPdfItems(new Uint8Array(readFileSync(path)), pdfjs as unknown as PdfJsLike);
    if (pages.every((p) => p.every((i) => i.str.trim() === ""))) {
      rows.push({ name, error: "scanned" });
      continue;
    }
    const text = pagesToChartText(pages);
    const result = await importPdfFile(name, new Uint8Array(readFileSync(path)), pdfjs as unknown as PdfJsLike);
    const song = result.songs[0];
    if (!song) {
      rows.push({ name, error: result.error ?? "nothing found" });
      continue;
    }
    const { body_text, slide_order } = chartToSong(song);
    const chords = song.chart.sections.flatMap((s) => s.stanzas.flat()).reduce((n, l) => n + l.chords.length, 0);
    rows.push({
      name,
      title: song.title,
      artist: song.artist,
      key: song.chart.key,
      tempo: song.chart.tempo,
      time: song.chart.time,
      ccli: song.ccli,
      license: song.ccliLicense ?? null,
      copyright: song.copyright,
      sections: song.chart.sections.map((s) => s.label),
      order: song.order,
      slide_order,
      chords,
      hasChords: chartHasChords(song.chart),
      slides: body_text.split(/\n\s*\n/).length,
      warnings: song.warnings,
    });
    dump += `\n\n######## ${name}\n--- text ---\n${text}\n--- chart ---\n`;
    for (const s of song.chart.sections) {
      dump += `[${s.label}]\n`;
      for (const st of s.stanzas) {
        for (const l of st) {
          const r = renderChordRows(l);
          if (r.chords.trim()) dump += `${r.chords}\n`;
          if (l.text) dump += `${r.lyrics}\n`;
        }
        dump += "\n";
      }
    }
    dump += `--- body ---\n${body_text}\n--- order: ${slide_order}\n`;
  } catch (err) {
    rows.push({ name, error: String(err) });
  }
}
writeFileSync(join(outDir!, "report.json"), JSON.stringify(rows, null, 1));
writeFileSync(join(outDir!, "dump.txt"), dump);
console.log(`${rows.length} files in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
