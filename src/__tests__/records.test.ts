import { describe, expect, it } from "vitest";

import {
  applySet,
  changedFields,
  readingFromRecord,
  readingToFields,
  recordProblems,
  setlistFromRecord,
  setlistToFields,
  songFromRecord,
  songToFields,
  type SyncRecord,
} from "@/core/records";

// The same samples the Rust conversion is tested against
// (src-tauri/src/library_sync/tests.rs, the_published_sample_records_round_trip).
const samples = Object.values(
  import.meta.glob<SyncRecord>("../../docs/server-protocol/records/*.json", { eager: true, import: "default" }),
);
const sample = (type: string) => samples.find((s) => s.type === type && (type !== "song" || s.id.includes("AMAZING")))!;

describe("the published record formats", () => {
  it("are all here, and all valid", () => {
    expect(samples.length).toBeGreaterThanOrEqual(6);
    for (const s of samples) expect(recordProblems(s), s.id).toEqual([]);
  });

  it("a song converts both ways, keeping the parts it doesn't carry", () => {
    for (const s of samples.filter((x) => x.type === "song")) {
      const song = songFromRecord(s.id, s.fields);
      expect(songToFields(song, s.fields)).toEqual(s.fields);
    }
  });

  it("a setlist converts both ways, refs included", () => {
    const s = sample("setlist");
    const { setlist, items } = setlistFromRecord(s.id, s.fields);
    expect(items.map((i) => i.kind)).toEqual(["song", "scripture", "text", "song"]);
    expect(setlistToFields(setlist, items)).toEqual(s.fields);
  });

  it("a reading converts both ways", () => {
    const s = sample("reading");
    expect(readingToFields(readingFromRecord(s.id, s.fields))).toEqual(s.fields);
  });

  it("refuses what the formats don't allow", () => {
    const s = sample("setlist");
    const items = (s.fields.items as Record<string, unknown>[]).map((i) => ({ ...i }));
    delete items[0]!.song_id;
    expect(recordProblems({ ...s, fields: { ...s.fields, items } }).join()).toMatch(/no song_id/);
    expect(recordProblems({ ...s, fields: { ...s.fields, refs: [] } }).join()).toMatch(/refs/);
    expect(recordProblems({ ...s, fields: { ...s.fields, items: [null] } }).join()).toMatch(/isn't an object/);
    expect(recordProblems({ type: "song", id: "x", fields: { title: "T", body_text: "", created_at: 0, updated_at: 0, ccli: null } }).join()).toMatch(
      /leave it out/,
    );
  });
});

describe("changes to push", () => {
  it("name only what changed, null for a field that's gone, and never fields it doesn't know", () => {
    const before = { title: "A", ccli: "1", body_text: "x", future_field: 1 };
    const now = { title: "B", body_text: "x", future_field: 2 };
    expect(changedFields("song", now, before)).toEqual({ title: "B", ccli: null });
    expect(applySet(before, { title: "B", ccli: null })).toEqual({ title: "B", body_text: "x", future_field: 1 });
  });

  it("a setlist edited on a phone keeps the song's chords untouched", () => {
    const s = sample("song");
    const song = songFromRecord(s.id, s.fields);
    const edited = songToFields({ ...song, title: "Amazing Grace (My Chains)" }, s.fields);
    expect(changedFields("song", edited, s.fields)).toEqual({ title: "Amazing Grace (My Chains)" });
  });
});
