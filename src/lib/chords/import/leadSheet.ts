/**
 * Is this PDF a lead sheet? Asks the engine, which reads notation (staves,
 * notes, chords, lyrics) — the browser's PDF reader only sees text.
 *
 * Three answers: a lead sheet (with what it says), not one (carry on and
 * read it as a chord chart), or "couldn't ask" (the engine isn't running).
 */

import { requestSidecar } from "@/lib/sidecarClient";
import type { ImportedLeadSheet } from "@/lib/chords/import/types";

export type LeadSheetCheck =
  | { kind: "lead_sheet"; sheet: ImportedLeadSheet }
  | { kind: "not_lead_sheet"; reason: string }
  | { kind: "unavailable"; reason: string };

export type ReadLeadSheet = (fileName: string, bytes: Uint8Array) => Promise<LeadSheetCheck>;

/** Long PDFs take the engine a second or two each; a big folder queues. */
const TIMEOUT_MS = 60_000;

function toBase64(bytes: Uint8Array): string {
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

export const readLeadSheetWithEngine: ReadLeadSheet = async (fileName, bytes) => {
  try {
    const r = await requestSidecar(
      { kind: "parse_lead_sheet", pdf_base64: toBase64(bytes), file_name: fileName },
      { timeoutMs: TIMEOUT_MS },
    );
    if (!r.is_lead_sheet) return { kind: "not_lead_sheet", reason: r.reason ?? "" };
    return {
      kind: "lead_sheet",
      sheet: {
        title: r.title ?? null,
        ccli: r.ccli ?? null,
        key: r.key ?? null,
        copyright: r.copyright ?? null,
        barCount: r.bar_count ?? 0,
        problemBars: r.problem_bars ?? 0,
        sections: r.sections ?? [],
        readerVersion: r.reader_version ?? 0,
        scoreJson: r.score_json ?? "",
      },
    };
  } catch (err) {
    return { kind: "unavailable", reason: err instanceof Error ? err.message : String(err) };
  }
};
