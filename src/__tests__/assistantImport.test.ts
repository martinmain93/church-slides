// @vitest-environment node — jsdom's File has no arrayBuffer(); Node's does.
import { describe, expect, it, vi } from "vitest";

import { runAssistantImport, type ImportDeps } from "@/lib/assistantImport";
import type { ChartImportItem, SongRow } from "@/lib/tauri";

const CORNERSTONE = `{title: Cornerstone}
{artist: Hillsong}
{ccli: 6158927}
{key: C}

{start_of_verse: Verse 1}
My [C]hope is built on [F]nothing less
Than [C]Jesus' blood and [G]righteousness
{end_of_verse}

{start_of_chorus}
Christ [C]alone, [F]cornerstone
{end_of_chorus}
`;

const HOLY_LYRICS = `Holy Forever

Verse 1
A thousand voices rising in the morning

Chorus
Holy forever
`;

function song(id: string, title: string, ccli: string | null = null): SongRow {
  return {
    id, title, ccli, body_text: "[v]\nx", author: null, copyright: null,
    default_tempo_wps: null, slide_order: null, created_at: 0, updated_at: 0,
  };
}

function deps(files: Record<string, string>, library: SongRow[]) {
  const saved: ChartImportItem[][] = [];
  const d: ImportDeps = {
    readFile: async (path) => {
      if (!(path in files)) throw new Error(`can't read ${path}`);
      return new TextEncoder().encode(files[path]).buffer as ArrayBuffer;
    },
    listSongs: async () => library,
    importCharts: vi.fn(async (items: ChartImportItem[]) => {
      saved.push(items);
      return {
        songs_created: items.filter((i) => i.song).length,
        charts_attached: items.filter((i) => i.existing_song_id).length,
        song_ids: items.map((i, k) => i.existing_song_id ?? `song_new_${k}`),
      };
    }),
  };
  return { d, saved };
}

const req = (paths: string[], dry_run = false) => ({
  id: "r1",
  dry_run,
  files: paths.map((p) => ({ path: p, name: p.split("/").pop()! })),
});

describe("assistant import", () => {
  it("creates new songs with their chords and reports ids", async () => {
    const { d, saved } = deps({ "/c/Cornerstone.cho": CORNERSTONE }, []);
    const report = await runAssistantImport(req(["/c/Cornerstone.cho"]), d);

    expect(report.songs).toHaveLength(1);
    const s = report.songs[0]!;
    expect(s).toMatchObject({ title: "Cornerstone", outcome: "new_song", has_chords: true, key: "C", ccli: "6158927", song_id: "song_new_0" });
    expect(saved[0]![0]!.song!.body_text).toContain("[Verse 1]");
    expect(saved[0]![0]!.song!.body_text).not.toContain("[C]");
    expect(report.summary).toMatch(/^Imported: 1 new song/);
  });

  it("adds chords to a library song matched by CCLI, keeping its lyrics", async () => {
    const { d, saved } = deps({ "/c/x.cho": CORNERSTONE }, [song("song_lib", "Cornerstone (Live)", "6158927")]);
    const report = await runAssistantImport(req(["/c/x.cho"]), d);
    expect(report.songs[0]).toMatchObject({
      outcome: "chords_added_to_existing",
      song_id: "song_lib",
      existing_song: { id: "song_lib", title: "Cornerstone (Live)" },
    });
    expect(saved[0]![0]!.song).toBeNull();
  });

  it("skips a lyrics-only file for a song already in the library", async () => {
    const { d, saved } = deps({ "/c/holy.txt": HOLY_LYRICS }, [song("song_h", "Holy Forever")]);
    const report = await runAssistantImport(req(["/c/holy.txt"]), d);
    expect(report.songs[0]!.outcome).toBe("skipped");
    expect(report.songs[0]!.reason).toMatch(/no chords to add/);
    expect(saved).toHaveLength(0);
  });

  it("dry run saves nothing but says what would happen", async () => {
    const { d } = deps({ "/c/Cornerstone.cho": CORNERSTONE }, []);
    const report = await runAssistantImport(req(["/c/Cornerstone.cho"], true), d);
    expect(d.importCharts).not.toHaveBeenCalled();
    expect(report.songs[0]).toMatchObject({ outcome: "new_song", song_id: null });
    expect(report.summary).toMatch(/^Would import/);
  });

  it("keeps the best copy when a folder holds the same song twice", async () => {
    const lyricsOnly = CORNERSTONE.replace(/\[[A-G][^\]]*\]/g, "");
    const { d } = deps({ "/c/a.cho": lyricsOnly, "/c/b.cho": CORNERSTONE }, []);
    const report = await runAssistantImport(req(["/c/a.cho", "/c/b.cho"]), d);
    const byFile = Object.fromEntries(report.songs.map((s) => [s.file, s]));
    expect(byFile["b.cho"]!.outcome).toBe("new_song");
    expect(byFile["a.cho"]!.outcome).toBe("skipped");
    expect(byFile["a.cho"]!.reason).toMatch(/b\.cho/);
  });

  it("reports files it couldn't read and whole-library exports", async () => {
    const { d } = deps({ "/c/Export.txt": "Title: A\n\nLine one\n\nTitle: B\n\nLine two\n" }, []);
    const report = await runAssistantImport(req(["/c/Export.txt", "/c/missing.pdf"]), d);
    const errors = report.unreadable.map((u) => `${u.file}: ${u.error}`).join("\n");
    expect(errors).toMatch(/missing\.pdf: can't read/);
    expect(errors).toMatch(/Export\.txt: A whole-library export/);
  });
});

describe("isWholeLibraryFile", () => {
  it("tells a library backup from a ChordPro file that also starts with a brace", async () => {
    const { isWholeLibraryFile } = await import("@/lib/chords/import");
    expect(isWholeLibraryFile(CORNERSTONE)).toBe(false);
    expect(isWholeLibraryFile('{"schema_version":1,"exported_at":0,"songs":[]}')).toBe(true);
    expect(isWholeLibraryFile("﻿Title: A\n\nLine\n\nTitle: B\n\nLine\n")).toBe(true);
  });
});
