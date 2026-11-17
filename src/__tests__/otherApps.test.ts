/**
 * "Bring your songs" readers, on libraries captured from the real apps
 * (data/fixtures/other_apps: three public-domain hymns and a setlist each).
 * ProPresenter and OpenLP fixtures are what `other_apps.rs` reads out of their
 * databases — its tests regenerate them.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { readImportFiles } from "@/lib/chords/import";
import { chartToSong } from "@/lib/chords/import/toSong";
import { fromFreeShow } from "@/lib/otherApps/freeshow";
import { fromOpenLp } from "@/lib/otherApps/openlp";
import { fromOpenSong } from "@/lib/otherApps/opensong";
import { importOsz } from "@/lib/otherApps/osz";
import { importProFile, importProPlaylist, looksLikeProPresenterFile } from "@/lib/otherApps/proFile";
import { fromProPresenter } from "@/lib/otherApps/propresenter";
import { rtfToText } from "@/lib/otherApps/rtf";
import type { OtherAppImport } from "@/lib/otherApps/types";

const FIXTURES = path.resolve(__dirname, "..", "..", "data", "fixtures", "other_apps");
const json = (rel: string) => JSON.parse(readFileSync(path.join(FIXTURES, rel), "utf-8"));

function song(imp: OtherAppImport, title: string) {
  const s = imp.results.flatMap((r) => r.songs).find((x) => x.title === title);
  if (!s) throw new Error(`no ${title}`);
  return s;
}

function setlistTitles(imp: OtherAppImport) {
  const titleOf = new Map(imp.results.map((r) => [r.fileName, r.songs[0]?.title]));
  return imp.setlists.map((s) => ({ name: s.name, songs: s.songs.map((k) => titleOf.get(k)) }));
}

const HYMNS = ["A Mighty Fortress is Our God", "Amazing Grace", "Be Thou My Vision"];

describe("rtfToText", () => {
  it("keeps line breaks and decodes escapes", () => {
    const rtf = "{\\rtf1\\ansi{\\fonttbl\\f0\\fnil Helvetica;}{\\*\\expandedcolortbl;;}\n\\f0\\fs84 \\cf2 Tis grace\\'92s\\\nthe hour I first \\u8220?believed\\u8221?}";
    expect(rtfToText(rtf)).toBe("Tis grace’s\nthe hour I first “believed”");
  });
});

describe("ProPresenter 22", () => {
  const imp = fromProPresenter(json("propresenter22/read.json"));

  it("reads every presentation as a song with its verses", () => {
    expect(imp.results.flatMap((r) => r.songs.map((s) => s.title)).sort()).toEqual(HYMNS);
    const grace = song(imp, "Amazing Grace");
    expect(grace.chart.sections.map((s) => s.label)).toEqual(["Verse 1", "Verse 2", "Verse 3", "Verse 4"]);
    expect(grace.chart.sections[0]!.stanzas[0]!.map((l) => l.text)).toEqual([
      "Amazing grace how sweet the sound that saved a wretch like me;",
      "I once was lost but now I'm found, was blind but now I see.",
    ]);
  });

  it("uses the song's only arrangement as its order", () => {
    const grace = song(imp, "Amazing Grace");
    expect(grace.order).toEqual(["Verse 1", "Verse 2", "Verse 3", "Verse 4", "Verse 1"]);
    expect(chartToSong(grace).slide_order).toBe("V1, V2, V3, V4, V1");
  });

  it("brings the playlist across in order", () => {
    expect(setlistTitles(imp)).toEqual([{ name: "Playlist", songs: HYMNS }]);
  });
});

describe("FreeShow", () => {
  const dir = path.join(FIXTURES, "freeshow");
  const shows = readdirSync(dir).filter((f) => f.endsWith(".show")).map((f) => json(`freeshow/${f}`));
  const imp = fromFreeShow({ shows, projects: json("freeshow/projects.json") });

  it("numbers repeated groups and keeps chords and credits", () => {
    const vision = song(imp, "Be Thou My Vision");
    expect(vision.chart.sections.map((s) => s.label)).toEqual(["Verse 1", "Verse 2", "Verse 3", "Verse 4"]);
    expect(vision.artist).toBe("Irish Folk Hymn; Elanor H. Hull");
    const first = vision.chart.sections[0]!.stanzas[0]![0]!;
    expect(first.text).toBe("Be Thou my Vision, O Lord of my heart,");
    expect(first.chords.map((c) => c.chord)).toEqual(["D", "Bm7", "G", "D"]);
  });

  it("brings the project across, skipping FreeShow's example", () => {
    expect(setlistTitles(imp)).toEqual([{ name: "10/26/26", songs: ["Amazing Grace", "Be Thou My Vision", "A Mighty Fortress is Our God"] }]);
  });
});

describe("OpenLP", () => {
  const imp = fromOpenLp(json("openlp/read.json"));

  it("reads lyrics, inline chords, verse order and authors", () => {
    expect(imp.results.flatMap((r) => r.songs.map((s) => s.title)).sort()).toEqual(HYMNS);
    const grace = song(imp, "Amazing Grace");
    expect(grace.ccli).toBe("1037882");
    const line = grace.chart.sections[0]!.stanzas[0]![0]!;
    expect(line.text).toBe("Amazing grace how sweet the sound that saved a wretch like me;");
    expect(line.chords[0]).toEqual({ pos: 0, chord: "D" });
    const fortress = song(imp, "A Mighty Fortress is Our God");
    expect(fortress.order).toEqual(["Verse 1", "Verse 2", "Verse 3", "Verse 4"]);
    expect(song(imp, "Be Thou My Vision").artist).toBe("Irish Folk Hymn, Elanor H. Hull");
  });
});

describe("OpenSong", () => {
  const read = (sub: string) => {
    const root = path.join(FIXTURES, "opensong", sub);
    const out: { path: string; text: string }[] = [];
    const walk = (d: string) => {
      for (const f of readdirSync(d)) {
        const p = path.join(d, f);
        if (statSync(p).isDirectory()) walk(p);
        // "/" on every OS, as other_apps.rs's read_tree hands them over
        else out.push({ path: path.relative(root, p).split(path.sep).join("/"), text: readFileSync(p, "utf-8") });
      }
    };
    walk(root);
    return out;
  };
  const imp = fromOpenSong({ songs: read("Songs"), sets: read("Sets") });

  it("reads songs from subfolders too", () => {
    expect(imp.results.flatMap((r) => r.songs.map((s) => s.title)).sort()).toEqual(HYMNS);
  });

  it("matches set entries by folder and name, skipping non-song items", () => {
    expect(setlistTitles(imp)).toEqual([
      { name: "Sunday Hymns", songs: ["Amazing Grace", "Be Thou My Vision", "A Mighty Fortress is Our God"] },
    ]);
  });
});

// --- Files from the Song files import -------------------------------------------

describe("ProPresenter 7 files", () => {
  const bytes = (rel: string) => new Uint8Array(readFileSync(path.join(FIXTURES, rel)));

  it("reads an exported .pro with its verses and arrangement", () => {
    const r = importProFile("Amazing Grace.pro", bytes("propresenter7/Amazing Grace.pro"));
    expect(r.error).toBeNull();
    const s = r.songs[0]!;
    expect(s.title).toBe("Amazing Grace");
    expect(s.chart.sections.map((x) => x.label)).toEqual(["Verse 1", "Verse 2", "Verse 3", "Verse 4"]);
    expect(s.chart.sections[1]!.stanzas[0]![0]!.text).toMatch(/^Twas grace that taught my heart to fear/);
    expect(s.order).toEqual(["Verse 1", "Verse 2", "Verse 3", "Verse 4", "Verse 1"]);
  });

  it("tells a ProPresenter .pro from a ChordPro .pro", () => {
    expect(looksLikeProPresenterFile(bytes("propresenter7/Amazing Grace.pro"))).toBe(true);
    expect(looksLikeProPresenterFile(new TextEncoder().encode("{title: Amazing Grace}\n[G]Amazing grace"))).toBe(false);
  });

  it("reads a real .proplaylist export into its songs and running order", () => {
    // Exported from ProPresenter 22: ZIP64 with a broken central directory,
    // and the playlist wrapped in a document message.
    const { results, setlists } = importProPlaylist("Real export.proplaylist", bytes("propresenter7/Real export.proplaylist"));
    const key = (t: string) => `Real export.proplaylist › ${t}.pro`;
    expect(results.map((r) => r.songs[0]?.title)).toEqual(["A Mighty Fortress is Our God", "Amazing Grace", "Be Thou My Vision"]);
    expect(results.find((r) => r.fileName === key("Amazing Grace"))!.songs[0]!.order).toHaveLength(5);
    // Named from the playlist inside the file, not the file name (the fallback).
    expect(setlists).toEqual([
      { name: "Playlist", songs: [key("A Mighty Fortress is Our God"), key("Amazing Grace"), key("Be Thou My Vision")] },
    ]);
  });
});

describe("OpenLP service files", () => {
  it("reads every song in a .osz, in service order, as one service", () => {
    const { results, setlists } = importOsz("Sunday Service.osz", new Uint8Array(readFileSync(path.join(FIXTURES, "openlp/Sunday Service.osz"))));
    expect(results.map((r) => r.songs[0]?.title)).toEqual(["Amazing Grace", "Be Thou My Vision", "A Mighty Fortress is Our God"]);
    const grace = results[0]!.songs[0]!;
    expect(grace.ccli).toBe("1037882");
    expect(grace.chart.sections[0]!.stanzas[0]![0]!.chords[0]?.chord).toBe("D");
    expect(setlists).toEqual([{ name: "Sunday Service", songs: results.map((r) => r.fileName) }]);
  });
});

describe("the Song files import", () => {
  it("routes .pro, .proplaylist and .osz files and carries their services", async () => {
    // jsdom's File has no arrayBuffer(); the importer only needs these three.
    const file = (rel: string) => {
      const buf = readFileSync(path.join(FIXTURES, rel));
      const bytes = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
      return { name: path.basename(rel), size: buf.byteLength, arrayBuffer: async () => bytes } as unknown as File;
    };
    const read = await readImportFiles(
      [file("propresenter7/Amazing Grace.pro"), file("propresenter7/Real export.proplaylist"), file("openlp/Sunday Service.osz")],
      { isWholeLibrary: () => false },
    );
    expect(read.results.every((r) => r.error === null)).toBe(true);
    expect(read.results.map((r) => r.songs[0]?.format)).toEqual([...Array(4).fill("propresenter"), ...Array(3).fill("openlyrics")]);
    expect(read.setlists.map((s) => s.name)).toEqual(["Playlist", "Sunday Service"]);
  });
});
