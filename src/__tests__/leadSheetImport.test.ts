// @vitest-environment node — jsdom's File has no arrayBuffer(); Node's does.
/**
 * Lead sheets in the importer: a PDF the engine reads as notation becomes a
 * reference on its song, not a chart; matching follows the chart rules
 * (CCLI number, then title) and can ride on a song the same import creates.
 */
import { describe, expect, it, vi } from "vitest";

import { runAssistantImport, type ImportDeps } from "@/lib/assistantImport";
import { importChartFile, readImportFiles, isWholeLibraryFile, type ImportFileResult } from "@/lib/chords/import";
import type { ImportedLeadSheet } from "@/lib/chords/import/types";
import type { LeadSheetCheck } from "@/lib/chords/import/leadSheet";
import {
  defaultLeadSheetAction,
  planImport,
  planLeadSheets,
  resolveLeadSheetSong,
  toReferenceInput,
} from "@/lib/chords/import/plan";
import type { ChartImportItem, NewSongReference, SongRow } from "@/lib/tauri";

function song(id: string, title: string, ccli: string | null = null): SongRow {
  return {
    id, title, ccli, body_text: "[v]\nx", author: null, copyright: null,
    default_tempo_wps: null, slide_order: null, created_at: 0, updated_at: 0,
  };
}

function sheet(over: Partial<ImportedLeadSheet> = {}): ImportedLeadSheet {
  return {
    title: "King Of My Heart", ccli: "7046145", key: "Bb", copyright: null,
    barCount: 63, problemBars: 0, sections: ["VERSE 1", "CHORUS"], readerVersion: 1,
    scoreJson: '{"reader_version":1,"bars":[]}', ...over,
  };
}

const sheetFile = (name: string, s: ImportedLeadSheet): ImportFileResult => ({ fileName: name, songs: [], leadSheet: s, error: null });

const CHART = `{title: King of My Heart}
{ccli: 7046145}
{key: Bb}

{start_of_verse: Verse 1}
Let the [Bb]King of my heart
{end_of_verse}
`;

describe("planning lead sheets", () => {
  it("matches the library by CCLI number before title", () => {
    const library = [song("a", "King of My Heart (Live)", "7046145"), song("b", "Something Else")];
    const [row] = planLeadSheets([sheetFile("King Of My Heart-lead-Bb.pdf", sheet({ title: "Different" }))], library, []);
    expect(row!.match?.id).toBe("a");
    expect(defaultLeadSheetAction(row!)).toEqual({ kind: "attach", songId: "a" });
  });

  it("falls back to the title, ignoring case and parentheses", () => {
    const library = [song("a", "king of my heart (Bethel)")];
    const [row] = planLeadSheets([sheetFile("x.pdf", sheet({ ccli: null }))], library, []);
    expect(row!.match?.id).toBe("a");
  });

  it("uses the file name when the sheet has no readable title", () => {
    const library = [song("a", "What A Beautiful Name")];
    const [row] = planLeadSheets([sheetFile("What A Beautiful Name-lead-D.pdf", sheet({ title: null, ccli: null }))], library, []);
    expect(row!.match?.id).toBe("a");
  });

  it("rides on a song this import creates, and is resolved to its new id", () => {
    const results: ImportFileResult[] = [importChartFile("komh.cho", CHART), sheetFile("King Of My Heart-lead-Bb.pdf", sheet())];
    const rows = planImport(results, []);
    const [row] = planLeadSheets(results, [], rows);
    const action = defaultLeadSheetAction(row!);
    expect(action).toEqual({ kind: "with_import", rowKey: rows[0]!.key });
    expect(resolveLeadSheetSong(action, new Map([[rows[0]!.key, "song_new"]]))).toBe("song_new");
  });

  it("skips a sheet with nothing to attach to", () => {
    const [row] = planLeadSheets([sheetFile("Unknown-lead-G.pdf", sheet({ title: "Unknown", ccli: null }))], [song("a", "Other")], []);
    expect(defaultLeadSheetAction(row!)).toEqual({ kind: "skip" });
  });

  it("keeps the better of two sheets for one song", () => {
    const library = [song("a", "King of My Heart", "7046145")];
    const rows = planLeadSheets(
      [sheetFile("in-Bb.pdf", sheet({ problemBars: 4 })), sheetFile("in-C.pdf", sheet({ problemBars: 0 }))],
      library,
      [],
    );
    expect(rows[0]!.betterCopy?.fileName).toBe("in-C.pdf");
    expect(defaultLeadSheetAction(rows[0]!)).toEqual({ kind: "skip" });
    expect(defaultLeadSheetAction(rows[1]!)).toEqual({ kind: "attach", songId: "a" });
  });

  it("stores the score verbatim with a small summary", () => {
    const [row] = planLeadSheets([sheetFile("f.pdf", sheet())], [song("a", "King of My Heart")], []);
    const ref = toReferenceInput(row!, "a");
    expect(ref).toMatchObject({ song_id: "a", kind: "lead_sheet", source_name: "f.pdf", data_json: sheet().scoreJson });
    expect(JSON.parse(ref.summary_json!)).toMatchObject({ key: "Bb", bars: 63, ccli: "7046145", reader_version: 1 });
  });
});

describe("reading PDFs", () => {
  const pdf = (name: string) => new File([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d])], name);

  it("a lead sheet becomes a lead-sheet result, not a chart", async () => {
    const readLeadSheet = vi.fn(async (): Promise<LeadSheetCheck> => ({ kind: "lead_sheet", sheet: sheet() }));
    const read = await readImportFiles([pdf("King Of My Heart-lead-Bb.pdf")], { isWholeLibrary: isWholeLibraryFile, readLeadSheet });
    expect(readLeadSheet).toHaveBeenCalledOnce();
    expect(read.results).toHaveLength(1);
    expect(read.results[0]!.leadSheet?.barCount).toBe(63);
    expect(read.results[0]!.songs).toEqual([]);
    expect(read.results[0]!.error).toBeNull();
  });

  it("other files never reach the lead-sheet check", async () => {
    const readLeadSheet = vi.fn(async (): Promise<LeadSheetCheck> => ({ kind: "not_lead_sheet", reason: "" }));
    await readImportFiles([new File([CHART], "komh.cho")], { isWholeLibrary: isWholeLibraryFile, readLeadSheet });
    expect(readLeadSheet).not.toHaveBeenCalled();
  });
});

describe("assistant import", () => {
  function deps(files: Record<string, string | Uint8Array>, library: SongRow[], check: LeadSheetCheck) {
    const charts: ChartImportItem[][] = [];
    const refs: NewSongReference[] = [];
    const d: ImportDeps = {
      readFile: async (path) => {
        const v = files[path]!;
        return (typeof v === "string" ? new TextEncoder().encode(v) : v).buffer as ArrayBuffer;
      },
      listSongs: async () => library,
      importCharts: vi.fn(async (items: ChartImportItem[]) => {
        charts.push(items);
        return {
          songs_created: items.filter((i) => i.song).length,
          charts_attached: items.filter((i) => i.existing_song_id).length,
          song_ids: items.map((i, k) => i.existing_song_id ?? `song_new_${k}`),
        };
      }),
      readLeadSheet: async () => check,
      saveSongReference: vi.fn(async (r: NewSongReference) => {
        refs.push(r);
        return { id: "ref_1", song_id: r.song_id, kind: r.kind, source_name: r.source_name, summary_json: "{}", created_at: 0, updated_at: 0 };
      }),
    };
    return { d, charts, refs };
  }
  const PDF = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d]);
  const req = (paths: string[], dry_run = false) => ({
    id: "r1",
    dry_run,
    files: paths.map((p) => ({ path: p, name: p.split("/").pop()! })),
  });

  it("attaches a lead sheet to its library song and reports it", async () => {
    const { d, refs, charts } = deps({ "/x/komh-lead-Bb.pdf": PDF }, [song("a", "King of My Heart", "7046145")], { kind: "lead_sheet", sheet: sheet() });
    const report = await runAssistantImport(req(["/x/komh-lead-Bb.pdf"]), d);
    expect(charts).toEqual([]);
    expect(refs).toHaveLength(1);
    expect(refs[0]!.song_id).toBe("a");
    expect(report.lead_sheets[0]).toMatchObject({ outcome: "attached", song: { id: "a", title: "King of My Heart" }, bars: 63 });
    expect(report.summary).toContain("1 of 1 lead sheet(s) attached");
  });

  it("attaches to the song created from a chart in the same import", async () => {
    const { d, refs } = deps(
      { "/x/komh.cho": CHART, "/x/komh-lead-Bb.pdf": PDF },
      [],
      { kind: "lead_sheet", sheet: sheet() },
    );
    const report = await runAssistantImport(req(["/x/komh.cho", "/x/komh-lead-Bb.pdf"]), d);
    expect(report.songs[0]!.outcome).toBe("new_song");
    expect(refs[0]!.song_id).toBe("song_new_0");
  });

  it("saves nothing in a dry run but says where it would go", async () => {
    const { d, refs } = deps({ "/x/k.pdf": PDF }, [song("a", "King of My Heart", "7046145")], { kind: "lead_sheet", sheet: sheet() });
    const report = await runAssistantImport(req(["/x/k.pdf"], true), d);
    expect(refs).toEqual([]);
    expect(report.lead_sheets[0]!.song?.id).toBe("a");
  });

  it("skips a sheet whose song isn't in the library, with a reason", async () => {
    const { d, refs } = deps({ "/x/k.pdf": PDF }, [], { kind: "lead_sheet", sheet: sheet() });
    const report = await runAssistantImport(req(["/x/k.pdf"]), d);
    expect(refs).toEqual([]);
    expect(report.lead_sheets[0]).toMatchObject({ outcome: "skipped" });
    expect(report.lead_sheets[0]!.reason).toMatch(/import the song first/);
  });
});
