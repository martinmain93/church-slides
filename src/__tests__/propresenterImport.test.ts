/**
 * The ProPresenter `Export.txt` importer (Library → Import). It began as a
 * port of a Python CLI importer, since removed; the deterministic-ID cases
 * below still pin the IDs that importer produced, so libraries imported
 * either way re-import onto the same rows.
 */

import { describe, expect, it } from "vitest";

import {
  looksLikeProPresenterExport,
  parseProPresenterExport,
  toLibraryExport,
} from "@/lib/propresenterImport";
import { parseSong } from "@/lib/parseSong";


describe("looksLikeProPresenterExport", () => {
  it("recognises a plain Title: prefix", () => {
    expect(looksLikeProPresenterExport("Title: Foo\n\nLine\n")).toBe(true);
  });

  it("handles the BOM-prefixed UTF-8 case", () => {
    expect(looksLikeProPresenterExport("﻿Title: Foo\n")).toBe(true);
  });

  it("rejects library JSON", () => {
    expect(looksLikeProPresenterExport('{"schema_version": 1}')).toBe(false);
  });

  it("rejects empty input", () => {
    expect(looksLikeProPresenterExport("")).toBe(false);
  });
});


describe("parseProPresenterExport", () => {
  it("splits on Title: lines", () => {
    const report = parseProPresenterExport(
      "Title: A\n\n\nStanza one\n\n\nTitle: B\n\n\nStanza two\n",
    );
    expect(report.songs.map((s) => s.title)).toEqual(["A", "B"]);
  });

  it("skips title-only blocks", () => {
    const report = parseProPresenterExport(
      "Title: Announcement Loop\n\n\nTitle: Real Song\n\n\nLyrics\n",
    );
    expect(report.songs).toHaveLength(1);
    expect(report.songs[0]!.title).toBe("Real Song");
    expect(report.skipped_empty).toBe(1);
  });

  it("renames duplicate titles", () => {
    const report = parseProPresenterExport(
      "Title: Song\n\n\nA\n\n\nTitle: Song\n\n\nB\n",
    );
    expect(report.songs.map((s) => s.title)).toEqual(["Song", "Song (2)"]);
    expect(report.duplicate_titles_renamed).toContain("Song (2)");
  });

  it("strips the BOM", () => {
    const report = parseProPresenterExport("﻿Title: Foo\n\n\nLine\n");
    expect(report.songs).toHaveLength(1);
    expect(report.songs[0]!.title).toBe("Foo");
  });

  it("tags a repeated stanza as [chorus]", () => {
    const text = [
      "Title: Test",
      "",
      "",
      "Verse one line one",
      "Verse one line two",
      "Verse one line three",
      "Verse one line four",
      "",
      "Grace upon grace now covers me",
      "My shepherd leads me home",
      "Grace has set me free",
      "",
      "Verse two line one",
      "Verse two line two",
      "Verse two line three",
      "Verse two line four",
      "",
      "Grace upon grace now covers me",
      "My shepherd leads me home",
      "Grace has set me free",
      "",
    ].join("\n");
    const body = parseProPresenterExport(text).songs[0]!.body_text;
    expect((body.match(/\[chorus\]/g) ?? []).length).toBe(2);
    expect(body).toContain("[verse 1]");
    expect(body).toContain("[verse 2]");
  });

  it("does not promote a short refrain to chorus", () => {
    const text =
      "Title: Alleluia Song\n\n\nAlleluia\n\n\nLong verse one word two word three word four word five\n\n\nAlleluia\n";
    const body = parseProPresenterExport(text).songs[0]!.body_text;
    expect(body).not.toContain("[chorus]");
  });

  it("returns nothing for empty input", () => {
    expect(parseProPresenterExport("").songs).toHaveLength(0);
  });

  it("returns nothing, counting the skips, when there are only titles", () => {
    const report = parseProPresenterExport("Title: A\n\n\n\nTitle: B\n\n\n");
    expect(report.songs).toHaveLength(0);
    expect(report.skipped_empty).toBe(2);
  });

  it("writes a body the song parser reads without falling back to (other, 1)", () => {
    // `other` is the parser's "don't know what this is" tag, which would
    // defeat the matcher's section priors.
    const text =
      "Title: Round Trip Song\n\n\n\nAmazing grace how sweet the sound\nThat saved a wretch like me\n\n\n" +
      "I once was lost but now am found\nWas blind but now I see\n\n\n" +
      "Amazing grace how sweet the sound\nThat saved a wretch like me\n";
    const { slides, section_order } = parseSong(parseProPresenterExport(text).songs[0]!.body_text);
    expect(slides.length).toBeGreaterThan(0);
    for (const s of slides) expect(s.section.type).not.toBe("other");
    const types = new Set(section_order.map((s) => s.type));
    expect(types.has("chorus")).toBe(true);
    expect(types.has("verse")).toBe(true);
  });

  it("emits one slide per stanza when no chorus exists", () => {
    const text =
      "Title: Linear\n\n\nFirst stanza unique words go here\nSome more\n\n\nSecond different content\n\n\nThird stanza distinct content\n";
    const body = parseProPresenterExport(text).songs[0]!.body_text;
    expect((body.match(/\[verse \d+\]/g) ?? []).length).toBe(3);
    expect(body).not.toContain("[chorus]");
  });
});


describe("toLibraryExport", () => {
  it("produces a SongRow-shaped payload", () => {
    const report = parseProPresenterExport("Title: X\n\n\nLine\n");
    const payload = toLibraryExport(report, 1_700_000_000_000);
    expect(payload.schema_version).toBe(1);
    expect(payload.exported_at).toBe(1_700_000_000_000);
    expect(payload.songs).toHaveLength(1);
    const song = payload.songs[0]!;
    expect(song).toHaveProperty("id");
    expect(song).toHaveProperty("title", "X");
    expect(song).toHaveProperty("body_text");
    expect(song).toHaveProperty("ccli", null);
    expect(song).toHaveProperty("created_at", 1_700_000_000_000);
  });

  it("keeps IDs stable across re-imports while timestamps update", () => {
    const text = "Title: Test\n\n\nLine\n";
    const a = toLibraryExport(parseProPresenterExport(text), 1).songs[0]!;
    const b = toLibraryExport(parseProPresenterExport(text), 2).songs[0]!;
    expect(a.id).toBe(b.id);
    expect(a.created_at).not.toBe(b.created_at);
  });

  it("produces deterministic IDs that match the Python SHA-1 prefix", () => {
    // Reference values produced by the Python importer for the same
    // titles. Parity guards re-import idempotency across CLI + desktop.
    const cases: Array<[string, string]> = [
      ["10,000 Reasons (Bless The Lord)", "song_pp_77a9558f37ef1683"],
      ["Agnus Dei", "song_pp_f8c3054a10d1774a"],
      ["Test", "song_pp_a94a8fe5ccb19ba6"],
    ];
    for (const [title, expectedId] of cases) {
      const payload = toLibraryExport(
        parseProPresenterExport(`Title: ${title}\n\n\nBody line\n`),
        1,
      );
      expect(payload.songs[0]!.id).toBe(expectedId);
    }
  });
});
