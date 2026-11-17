import { describe, expect, it } from "vitest";

import { keyMatches, parseQuery, withoutPart, type ParsedQuery } from "@/lib/smartSearch/parseQuery";

interface Want {
  hymn?: boolean;
  key?: string;
  played?: { mode: "in" | "not_in"; services?: number; days?: number };
  occasion?: string;
  author?: string;
  added?: number;
  tempo?: { min?: number; max?: number };
  time?: string;
  fit?: { title: string; how: "fit" | "sameKey" };
  topic?: string | null;
}

function summary(q: ParsedQuery): Want {
  const out: Want = {};
  if (q.hymn) out.hymn = true;
  if (q.key) out.key = q.key.name;
  if (q.played) {
    const { mode, services, days } = q.played;
    out.played = { mode, ...(services !== undefined && { services }), ...(days !== undefined && { days }) };
  }
  if (q.occasion) out.occasion = q.occasion.id;
  if (q.author) out.author = q.author;
  if (q.added) out.added = q.added.days;
  if (q.tempo) out.tempo = { ...(q.tempo.min !== undefined && { min: q.tempo.min }), ...(q.tempo.max !== undefined && { max: q.tempo.max }) };
  if (q.time) out.time = q.time;
  if (q.fit) out.fit = q.fit;
  out.topic = q.topic;
  return out;
}

// The spike's queries (research/spikes/semantic_search). The second block
// was held out of the first rules and missed 9 of 25; the fixes for those
// are in, so they're tuned now too.
const CASES: [string, Want][] = [
  ["Hymns in the key of E about the blood of Jesus", { hymn: true, key: "E", topic: "blood of Jesus" }],
  ["Good songs for easter that we haven't played in the last 3 services",
    { occasion: "easter", played: { mode: "not_in", services: 3 }, topic: null }],
  ["hymns about grace", { hymn: true, topic: "grace" }],
  ["songs in G", { key: "G", topic: null }],
  ["christmas hymns", { hymn: true, occasion: "christmas", topic: null }],
  ["worship songs we haven't played in a while", { played: { mode: "not_in", services: 4 }, topic: null }],
  ["songs in the key of A about God's goodness", { key: "A", topic: "God's goodness" }],
  ["what did we play last sunday", { played: { mode: "in", services: 1 }, topic: null }],
  ["songs in Bb we played recently", { key: "Bb", played: { mode: "in", services: 4 }, topic: null }],
  ["advent hymns", { hymn: true, occasion: "advent", topic: null }],

  ["hymns we haven't done in over a month", { hymn: true, played: { mode: "not_in", days: 30 }, topic: null }],
  ["anything in F#", { key: "F#", topic: null }],
  ["easter hymns in C", { hymn: true, key: "C", occasion: "easter", topic: null }],
  ["songs about grace we sang last week", { played: { mode: "in", days: 7 }, topic: "grace" }],
  ["what haven't we played for 6 weeks", { played: { mode: "not_in", days: 42 }, topic: null }],
  ["christmas carols", { occasion: "christmas", topic: null }],
  ["songs in D minor about lament", { key: "Dm", topic: "lament" }],
  ["a song for baptism sunday", { occasion: "baptism", topic: null }],
  ["old hymns about heaven", { hymn: true, topic: "old about heaven" }],
  ["upbeat songs in A that we didn't play in the last two services",
    { key: "A", played: { mode: "not_in", services: 2 }, tempo: { min: 110 }, topic: null }],
  ["good friday songs not used recently", { occasion: "good-friday", played: { mode: "not_in", services: 4 }, topic: null }],
  ["songs in E flat", { key: "Eb", topic: null }],
  ["communion songs in G", { occasion: "communion", key: "G", topic: null }],
  ["praise songs about God's power", { topic: "God's power" }],
  ["songs we did last sunday about love", { played: { mode: "in", services: 1 }, topic: "love" }],
  ["slow songs for remembrance sunday", { occasion: "remembrance", tempo: { max: 80 }, topic: null }],
  ["thanksgiving songs in C we haven't played in 3 weeks",
    { occasion: "thanksgiving", key: "C", played: { mode: "not_in", days: 21 }, topic: null }],
  ["hymn about the cross in Bb", { hymn: true, key: "Bb", topic: "cross" }],
  ["songs about the resurrection", { topic: "resurrection" }],
  ["Hymns", { hymn: true, topic: null }],

  // Plain searches stay plain: no filter read out of a title or a lyric.
  ["In Christ Alone", { topic: "In Christ Alone" }],
  ["Be Thou My Vision in D", { key: "D", topic: "Be Thou My Vision" }],
  ["A mighty fortress", { topic: "A mighty fortress" }],
  ["How Great Thou Art", { topic: "How Great Thou Art" }],
  ["Amazing grace", { topic: "Amazing grace" }],
  ["10,000 reasons", { topic: "10 000 reasons" }],
  ["wretch like me", { topic: "wretch like me" }],
  ["Sing to the Lord", { topic: "Sing to the Lord" }],
  ["I will sing of your love forever", { topic: "I will sing of your love forever" }],
  ["Songs about victory in Jesus", { topic: "victory in Jesus" }],

  // Credits, date added, tempo, time signature, fit.
  ["songs by Chris Tomlin", { author: "Chris Tomlin", topic: null }],
  // A credit before "songs" stays in the topic: the keyword half finds it in
  // the credits ("hillsong grace").
  ["Hillsong songs about grace", { topic: "Hillsong songs about grace" }],
  ["Tomlin songs", { topic: "Tomlin" }],
  ["songs from Bethel about the Holy Spirit", { author: "Bethel", topic: "Holy Spirit" }],
  ["songs by Tomlin in G", { author: "Tomlin", key: "G", topic: null }],
  ["saved by grace", { author: "grace", topic: "saved" }],
  ["new songs", { added: 90, topic: null }],
  ["new songs we haven't played yet", { added: 90, played: { mode: "not_in" }, topic: null }],
  ["songs added in the last 2 months", { added: 60, topic: null }],
  ["recently added hymns", { added: 90, hymn: true, topic: null }],
  ["fast songs", { tempo: { min: 110 }, topic: null }],
  ["slow songs about grace", { tempo: { max: 80 }, topic: "grace" }],
  ["upbeat christmas songs", { tempo: { min: 110 }, occasion: "christmas", topic: null }],
  ["songs around 72 bpm", { tempo: { min: 66, max: 78 }, topic: null }],
  ["songs between 70 and 90 bpm", { tempo: { min: 70, max: 90 }, topic: null }],
  ["songs under 75 bpm", { tempo: { max: 75 }, topic: null }],
  ["mid-tempo songs", { tempo: { min: 81, max: 109 }, topic: null }],
  ["songs in 6/8", { time: "6/8", topic: null }],
  ["6/8 songs in D", { time: "6/8", key: "D", topic: null }],
  ["waltzes", { time: "3/4", topic: null }],
  ["Songs that would musically fit well with How great thou art",
    { fit: { title: "How great thou art", how: "fit" }, topic: null }],
  ["what goes with Oceans", { fit: { title: "Oceans", how: "fit" }, topic: null }],
  ["songs like Reckless Love", { fit: { title: "Reckless Love", how: "fit" }, topic: null }],
  ["songs in the same key as Cornerstone", { fit: { title: "Cornerstone", how: "sameKey" }, topic: null }],
  ["songs about grace that flow into Be Thou My Vision", { fit: { title: "Be Thou My Vision", how: "fit" }, topic: "grace" }],
  ["Hold fast", { tempo: { min: 110 }, topic: "Hold" }],
  ["I will sing a new song", { topic: "I will sing a new song" }],
];

describe("parseQuery", () => {
  it.each(CASES)("%s", (query, want) => {
    expect(summary(parseQuery(query))).toEqual({ topic: null, ...want });
  });

  it("reads 'by …' as plain words when asked to (no such author)", () => {
    expect(parseQuery("saved by grace", { ignore: ["author"] }).topic).toBe("saved by grace");
    expect(parseQuery("songs that go with communion", { ignore: ["fit"] }).occasion?.id).toBe("communion");
  });

  it("this year counts days from January 1", () => {
    const q = parseQuery("advent songs we haven't played this year");
    expect(q.occasion?.id).toBe("advent");
    expect(q.played?.mode).toBe("not_in");
    expect(q.played?.days).toBeGreaterThanOrEqual(1);
    expect(q.played?.days).toBeLessThanOrEqual(366);
  });

  it("keeps content words for the keyword search", () => {
    expect(parseQuery("Hymns in the key of E about the blood of Jesus").keywords).toBe("blood jesus");
    expect(parseQuery("songs in G").keywords).toBeNull();
  });

  it("removing a chip cuts its words out of the query", () => {
    const raw = "Hymns in the key of E about the blood of Jesus";
    const q = parseQuery(raw);
    expect(q.parts.map((p) => p.label)).toEqual(["Hymns", "Key E"]);
    const noKey = withoutPart(raw, q.parts.find((p) => p.kind === "key")!);
    expect(noKey).toBe("Hymns about the blood of Jesus");
    expect(parseQuery(noKey).key).toBeNull();
  });
});

describe("keyMatches", () => {
  const e = parseQuery("in E").key!;
  const bb = parseQuery("songs in Bb").key!;
  const dm = parseQuery("in D minor").key!;
  it("matches the written key, enharmonics included", () => {
    expect(keyMatches(e, "E")).toBe(true);
    expect(keyMatches(bb, "A#")).toBe(true);
    expect(keyMatches(dm, "Dm")).toBe(true);
  });
  it("tells major from minor and leaves keyless songs out", () => {
    expect(keyMatches(e, "Em")).toBe(false);
    expect(keyMatches(dm, "D")).toBe(false);
    expect(keyMatches(e, null)).toBe(false);
  });
});
