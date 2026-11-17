import { describe, expect, it } from "vitest";

import { buildLibraryIndex, matchingLine, recentSongs, searchLibrary, type LibrarySong } from "@/lib/band/librarySearch";

function song(id: string, title: string, body: string, extra: Partial<LibrarySong> = {}): LibrarySong {
  return { id, title, author: null, body, lastUsed: null, uses: 0, key: null, hasChart: false, ...extra };
}

const lib = buildLibraryIndex([
  song("worthy", "Worthy Is The Lamb", "[Verse 1]\nWorthy is the Lamb who was slain"),
  song("way", "Way Maker", "[Verse 1]\nYou are here moving in our midst\n\n[Chorus]\nWay maker miracle worker", { uses: 9, lastUsed: 300 }),
  song("wonder", "Wonder", "[Verse 1]\nI see the light"),
  song("goodness", "Goodness Of God", "[Verse 1]\nI love You Lord\n\n[Chorus]\nEvery day You have been faithful\nSo I will sing of Your kindness", { lastUsed: 500 }),
  song("reasons", "10,000 Reasons (Bless The Lord)", "[Chorus]\nBless the Lord O my soul", { lastUsed: 100 }),
  song("lamb", "Agnus Dei", "[Verse 1]\nAlleluia\nWorthy is the Lamb"),
]);
const titles = (q: string) => searchLibrary(lib, q).map((h) => h.song.title);

describe("library search", () => {
  it("one letter: titles starting with it, A–Z", () => {
    expect(titles("w")).toEqual(["Way Maker", "Wonder", "Worthy Is The Lamb"]);
    expect(titles("x")).toEqual([]);
  });

  it("ranks title matches above lyrics matches", () => {
    // "Worthy Is The Lamb" (title) before "Agnus Dei" (lyrics).
    expect(titles("worthy")).toEqual(["Worthy Is The Lamb", "Agnus Dei"]);
    expect(searchLibrary(lib, "worthy")[1]!.via).toBe("lyrics");
  });

  it("matches title words in any order, and ignores punctuation", () => {
    expect(titles("lamb worthy")[0]).toBe("Worthy Is The Lamb");
    expect(titles("10000")[0]).toBe("10,000 Reasons (Bless The Lord)");
    expect(titles("bless the")[0]).toBe("10,000 Reasons (Bless The Lord)");
  });

  it("finds songs by lyrics, and shows the matching line", () => {
    expect(titles("miracle worker")).toEqual(["Way Maker"]);
    expect(titles("faithful sing")).toEqual(["Goodness Of God"]);
    expect(matchingLine(lib.entries[1]!.song, "miracle worker")).toBe("Way maker miracle worker");
    // Words spread over two lines: the line holding most of them.
    expect(matchingLine(lib.entries[3]!.song, "faithful sing")).toBe("Every day You have been faithful");
  });

  it("lists recently played songs before anything is typed", () => {
    expect(recentSongs(lib).map((s) => s.id)).toEqual(["goodness", "way", "reasons"]);
  });

  // Timings mean something on a known machine, not on a shared cloud runner.
  // A tripwire for an order-of-magnitude regression only (typically ~60 ms):
  // a busy laptop easily doubles it, and the build's performance report
  // (scripts/perf_report.py) is where slower-than-usual gets flagged.
  it.skipIf(process.env.CI)("is fast enough to run on every keystroke", () => {
    const big = buildLibraryIndex(
      Array.from({ length: 1500 }, (_, i) =>
        song(`s${i}`, `Song number ${i}`, `[Verse 1]\n${"grace mercy love hope ".repeat(40)}line ${i}`),
      ),
    );
    const t0 = performance.now();
    for (const q of ["s", "so", "son", "grace", "mercy lo", "line 14"]) searchLibrary(big, q);
    expect(performance.now() - t0).toBeLessThan(1000);
  });
});
