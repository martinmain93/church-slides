import { describe, expect, it, vi } from "vitest";

import { rrf, smartSearch, titleTier, type SearchDeps, type SemanticHit } from "@/lib/smartSearch/search";
import type { ServiceRow, SongRow } from "@/lib/tauri";

function song(
  id: string,
  title: string,
  body = "",
  extra: Partial<SongRow> = {},
): SongRow {
  return {
    id, title, body_text: body, ccli: null, author: null, copyright: null, default_tempo_wps: null,
    slide_order: null, created_at: 0, updated_at: 0, ...extra,
  };
}

const LIB = [
  song("grace", "Amazing Grace", "Amazing grace how sweet the sound"),
  song("blood", "Nothing But The Blood", "What can wash away my sin"),
  song("well", "It Is Well With My Soul", "When peace like a river"),
  song("risen", "Christ The Lord Is Risen Today", "Alleluia"),
  song("reckless", "Reckless Love", "Oh the deep unending mercy of God", {
    author: "Cory Asbury, Caleb Culver, Ran Jackson",
    copyright: "2017 Bethel Music Publishing",
  }),
  song("tomb", "Resurrecting", "The stone is rolled away", { author: "Elevation Worship" }),
  song("happy", "Happy Day", "A day of joy for everyone", { author: "Tim Hughes, Ben Cantelon" }),
];

const DAY = 86_400_000;
const NOW = 100 * DAY;
// Added: the newest two in the last month, the rest long ago.
LIB.find((s) => s.id === "happy")!.created_at = NOW - 3 * DAY;
LIB.find((s) => s.id === "tomb")!.created_at = NOW - 20 * DAY;

const META: SearchDeps["chartMeta"] = {
  grace: { key: "G", tempo: 72, time: "3/4" },
  blood: { key: "E", tempo: null, time: null },
  well: { key: "C", tempo: 70, time: "6/8" },
  risen: { key: "E", tempo: 118, time: "4/4" },
  reckless: { key: "E", tempo: 83, time: "6/8" },
  tomb: { key: "A", tempo: 74, time: "4/4" },
  happy: { key: "E", tempo: 130, time: "4/4" },
};

function deps(over: Partial<SearchDeps> & { meaning?: SemanticHit[]; services?: ServiceRow[] } = {}): SearchDeps {
  const { meaning = [], services = [], ...rest } = over;
  return {
    // A crude stand-in for FTS: every query word appears in the title or body.
    searchSongs: async (q) => {
      const words = q.toLowerCase().split(/\W+/).filter(Boolean);
      return LIB.filter((s) =>
        words.every((w) => `${s.title} ${s.body_text} ${s.author ?? ""} ${s.copyright ?? ""} ${s.ccli ?? ""}`.toLowerCase().includes(w)),
      );
    },
    listSongs: async () => LIB,
    chartMeta: META,
    listRecentServices: async () => services,
    semanticSearch: async () => ({ status: "ready", hits: meaning }),
    now: () => NOW,
    ...rest,
  };
}

const hit = (song_id: string, passage = `${song_id} passage`): SemanticHit => ({ song_id, passage });

describe("smartSearch", () => {
  it("answers a title exactly as the keyword search did, without asking the engine", async () => {
    const semanticSearch = vi.fn();
    const r = await smartSearch("Amazing grace", deps({ semanticSearch }));
    expect(r.songs.map((s) => s.id)).toEqual(["grace"]);
    expect(r.meaning).toBe("unused");
    expect(semanticSearch).not.toHaveBeenCalled();
  });

  it("merges meaning with keywords and says why each song came up", async () => {
    const semanticSearch = vi.fn(async () => ({ status: "ready" as const, hits: [hit("risen"), hit("tomb"), hit("well")] }));
    const r = await smartSearch("songs for easter", deps({ semanticSearch }));
    expect(semanticSearch).toHaveBeenCalledWith(expect.arrayContaining(["He is risen from the grave"]), expect.any(Number));
    expect(r.songs.map((s) => s.id).slice(0, 2)).toEqual(["risen", "tomb"]);
    expect(r.reasons.risen).toBe("risen passage");
    expect(r.parsed.parts.map((p) => p.label)).toEqual(["Easter"]);
  });

  it("filters hymns in a key, ranked by the topic", async () => {
    const r = await smartSearch(
      "Hymns in the key of E about the blood of Jesus",
      deps({ meaning: [hit("reckless"), hit("blood"), hit("risen"), hit("grace")] }),
    );
    // reckless and happy are in E but no hymns; grace is a hymn but in G.
    expect(r.songs.map((s) => s.id)).toEqual(["blood", "risen"]);
  });

  it("leaves out what was played in the last N services", async () => {
    const services: ServiceRow[] = [
      { id: "s3", setlist_id: "x", started_at: NOW - 1 * DAY, song_ids: ["risen"] },
      { id: "s2", setlist_id: "x", started_at: NOW - 8 * DAY, song_ids: ["tomb"] },
      { id: "s1", setlist_id: "x", started_at: NOW - 15 * DAY, song_ids: ["well"] },
    ];
    const r = await smartSearch(
      "easter songs we haven't played in the last 2 services",
      deps({ services, meaning: [hit("risen"), hit("tomb"), hit("well")] }),
    );
    expect(r.songs.map((s) => s.id)).toEqual(["well"]);
    expect(r.historySince).toBe(NOW - 15 * DAY);
  });

  it("lists last Sunday's songs, most recent service first", async () => {
    const services: ServiceRow[] = [
      { id: "s2", setlist_id: "x", started_at: NOW - 2 * DAY, song_ids: ["well", "grace"] },
      { id: "s1", setlist_id: "x", started_at: NOW - 9 * DAY, song_ids: ["tomb"] },
    ];
    const r = await smartSearch("what did we play last sunday", deps({ services }));
    expect(r.songs.map((s) => s.id)).toEqual(["well", "grace"]);
  });

  it("with no service log yet, 'not played' excludes nothing and says so", async () => {
    const r = await smartSearch("hymns we haven't played in a while", deps());
    // happy and tomb are no hymns; reckless neither.
    expect(r.songs.map((s) => s.id).sort()).toEqual(["blood", "grace", "risen", "well"]);
    expect(r.historySince).toBeNull();
  });

  it("without the engine, keywords and filters still answer", async () => {
    const r = await smartSearch(
      "hymns about the blood",
      deps({ semanticSearch: async () => Promise.reject(new Error("no engine")) }),
    );
    expect(r.meaning).toBe("unavailable");
    expect(r.songs.map((s) => s.id)).toEqual(["blood"]);
  });

  it("reports a first index still building", async () => {
    const r = await smartSearch("songs about victory", deps({ semanticSearch: async () => ({ status: "indexing", hits: [] }) }));
    expect(r.meaning).toBe("indexing");
  });
});

describe("smartSearch: credits, added, tempo, time, fit", () => {
  const ids = (r: { songs: SongRow[] }) => r.songs.map((s) => s.id);

  it("filters by author or publisher", async () => {
    expect(ids(await smartSearch("songs by Cory Asbury", deps()))).toEqual(["reckless"]);
    expect(ids(await smartSearch("songs from Bethel", deps()))).toEqual(["reckless"]);
  });

  it("reads 'by …' as words when no song has that credit", async () => {
    const semanticSearch = vi.fn(async () => ({ status: "ready" as const, hits: [hit("grace")] }));
    const r = await smartSearch("saved by grace", deps({ semanticSearch }));
    expect(r.parsed.author).toBeNull();
    expect(semanticSearch).toHaveBeenCalledWith(["saved by grace"], expect.any(Number));
  });

  it("new songs, newest first", async () => {
    expect(ids(await smartSearch("new songs", deps()))).toEqual(["happy", "tomb"]);
    expect(ids(await smartSearch("songs added in the last week", deps()))).toEqual(["happy"]);
  });

  it("fast and slow by the chart's tempo, and says how many have none", async () => {
    const fast = await smartSearch("fast songs", deps());
    expect(ids(fast)).toEqual(["happy", "risen"]);
    expect(fast.notes).toEqual(["1 song has no tempo yet"]);
    expect(ids(await smartSearch("slow songs", deps()))).toEqual(["well", "grace", "tomb"]);
    expect(ids(await smartSearch("songs around 82 bpm", deps()))).toEqual(["reckless"]);
  });

  it("time signature", async () => {
    expect(ids(await smartSearch("songs in 6/8", deps())).sort()).toEqual(["reckless", "well"]);
    expect(ids(await smartSearch("waltzes", deps()))).toEqual(["grace"]);
  });

  it("fits with a song: close key, tempo and feel first, and why", async () => {
    // Reckless Love: E, 83 bpm, 6/8.
    const r = await smartSearch("Songs that would musically fit well with reckless love", deps());
    expect(r.parsed.fit?.title).toBe("reckless love");
    const order = ids(r);
    expect(order).not.toContain("reckless");
    // well: C (far), 70 bpm, 6/8 vs tomb: A (next to E), 74, 4/4 vs blood: E only.
    expect(order[0]).toBe("tomb");
    expect(r.reasons.tomb).toBe("Key A, next to E · 74 bpm · 4/4");
    expect(r.notes[0]).toBe("Matched to Reckless Love: E · 83 bpm · 6/8");
  });

  it("same key as a song", async () => {
    expect(ids(await smartSearch("songs in the same key as Happy Day", deps())).sort()).toEqual(
      ["blood", "reckless", "risen"],
    );
  });

  it("a song with no musical facts is matched by theme instead", async () => {
    const semanticSearch = vi.fn(async () => ({ status: "ready" as const, hits: [hit("well")] }));
    const lib = LIB.map((s) => (s.id === "blood" ? { ...s, body_text: "[v1]\nWhat can wash away my sin" } : s));
    const r = await smartSearch(
      "what goes with Nothing But The Blood",
      deps({ semanticSearch, listSongs: async () => lib, chartMeta: { ...META, blood: { key: null, tempo: null, time: null } } }),
    );
    expect(semanticSearch).toHaveBeenCalledWith(["What can wash away my sin"], expect.any(Number));
    expect(r.notes[0]).toMatch(/no key, tempo or time signature yet/);
  });

  it("a CCLI number finds the song by its credits", async () => {
    const lib = LIB.map((s) => (s.id === "grace" ? { ...s, ccli: "22025" } : s));
    const r = await smartSearch("22025", deps({ listSongs: async () => lib, searchSongs: async () => lib.filter((s) => s.ccli === "22025") }));
    expect(ids(r)).toEqual(["grace"]);
    expect(r.meaning).toBe("unused");
  });
});

describe("helpers", () => {
  it("rrf rewards agreement between rankings", () => {
    expect(rrf([["a", "b", "c"], ["b", "c"]])[0]).toBe("b");
  });

  it("titleTier matches the Rust tiers", () => {
    expect(titleTier("Amazing Grace", "amazing grace")).toBe(0);
    expect(titleTier("Amazing Grace (My Chains Are Gone)", "amazing grace")).toBe(1);
    expect(titleTier("King Of My Heart", "king heart")).toBe(2);
    expect(titleTier("Reckless Love", "unending")).toBe(3);
  });
});
