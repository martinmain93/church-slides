import { describe, expect, it } from "vitest";

import { layoutFlow } from "@/lib/presentation/flow";
import { buildContext } from "@/lib/presentation/state";
import { preparedSlideFor } from "@/lib/scripture/prepared";
import {
  aiAnchor,
  aiKinds,
  flowSlides,
  aiMayLeave,
  charsPerSlide,
  itemCountLabel,
  itemLabel,
  readingBody,
  readingSlides,
  scriptureSlides,
  spokenWords,
  textSlides,
  withServiceItems,
  type ItemKind,
  type ScriptureContent,
} from "@/lib/serviceItems";
import { buildSetlistContent } from "@/lib/setlistContent";
import { parseSlidesOverride, resolveSlideSequence } from "@/lib/slideSequence";
import type { SetlistDetail, SetlistItemWithSong, SongRow } from "@/lib/tauri";

const SONG: SongRow = {
  id: "song_a",
  title: "Amazing Grace",
  body_text: "[verse 1]\nAmazing grace how sweet the sound\n\n[chorus]\nMy chains are gone",
  ccli: null,
  author: null,
  copyright: null,
  default_tempo_wps: null,
  slide_order: null,
  created_at: 0,
  updated_at: 0,
};

const JOHN_3: ScriptureContent = {
  reference: "John 3:16–18",
  translation: "web",
  abbreviation: "WEB",
  verses: [
    { ref: "John 3:16", text: "For God so loved the world, that he gave his one and only Son." },
    { ref: "John 3:17", text: "For God didn't send his Son into the world to judge the world." },
    { ref: "John 3:18", text: "He who believes in him is not judged." },
  ],
};

function item(partial: Partial<SetlistItemWithSong> & { id: string }): SetlistItemWithSong {
  return {
    setlist_id: "sl",
    kind: "song",
    song_id: null,
    position: 0,
    slides_override: null,
    content: null,
    song: null,
    ...partial,
  };
}

function detail(items: SetlistItemWithSong[]): SetlistDetail {
  return { id: "sl", name: "Sunday", created_at: 0, last_used_at: null, items };
}

const SERVICE = detail([
  item({ id: "i_song", song_id: SONG.id, song: SONG }),
  item({ id: "i_john", kind: "scripture", content: JSON.stringify(JOHN_3) }),
  item({ id: "i_text", kind: "text", content: JSON.stringify({ title: "Announcements" }), slides_override: textSlides(["Welcome!", "Coffee after"]) }),
  item({ id: "i_song2", song_id: SONG.id, song: SONG }),
]);

describe("cutting a passage into slides", () => {
  it("keeps verses whole, numbers them, and ends each slide with what it shows", () => {
    const slides = scriptureSlides(JOHN_3, 140);
    expect(slides).toHaveLength(2);
    expect(slides[0]).toBe(
      "¹⁶For God so loved the world, that he gave his one and only Son. " +
        "¹⁷For God didn't send his Son into the world to judge the world.\nJohn 3:16–17 · WEB",
    );
    expect(slides[1]).toBe("¹⁸He who believes in him is not judged.\nJohn 3:18 · WEB");
  });

  it("splits a verse too long for one slide at its sentences", () => {
    const long: ScriptureContent = {
      reference: "Ephesians 1:3",
      verses: [{ ref: "Ephesians 1:3", text: "Blessed be the God and Father. ".repeat(6).trim() }],
    };
    const slides = scriptureSlides(long, 70);
    expect(slides.length).toBeGreaterThan(1);
    expect(slides[0]!.startsWith("³Blessed")).toBe(true);
    expect(slides[1]!.startsWith("Blessed")).toBe(true);
    for (const s of slides) expect(s.endsWith("Ephesians 1:3")).toBe(true);
  });

  it("splits a long verse into even parts at a pause, not a full slide and a scrap", () => {
    const text =
      "The same came to him by night, and said to him, “Rabbi, we know that you are a teacher " +
      "come from God, for no one can do these signs that you do, unless God is with him.”";
    const slides = scriptureSlides({ reference: "John 3:2", verses: [{ ref: "John 3:2", text }] }, 160);
    expect(slides).toHaveLength(2);
    const bodies = slides.map((s) => s.split("\n")[0]!);
    expect(bodies[0]!.endsWith(",")).toBe(true);
    expect(Math.abs(bodies[0]!.length - bodies[1]!.length)).toBeLessThan(60);
  });

  it("uses the church's own text, its blank lines as slide breaks, with the copyright last", () => {
    const own: ScriptureContent = {
      ...JOHN_3,
      abbreviation: undefined,
      own_text: "For God so loved the world\n\nthat he gave his one and only Son",
      credit: "Scripture taken from the NIV.",
    };
    expect(scriptureSlides(own, 230)).toEqual([
      "For God so loved the world\nJohn 3:16–18",
      "that he gave his one and only Son\nJohn 3:16–18\nScripture taken from the NIV.",
    ]);
  });

  it("says so when the text isn't there", () => {
    const [slide] = scriptureSlides({ reference: "John 3:16" }, 230);
    expect(slide).toContain("isn't available");
  });

  it("fits less on a slide at a larger lyric size", () => {
    expect(charsPerSlide("xl")).toBeLessThan(charsPerSlide("m"));
    expect(charsPerSlide("s")).toBeGreaterThan(charsPerSlide("m"));
  });
});

describe("presenting items as songs", () => {
  const presented = withServiceItems(SERVICE, "m");

  it("gives each passage and text item a song-shaped stand-in, under its own id", () => {
    const john = presented.items[1]!;
    expect(john.song?.id).toBe("i_john");
    expect(john.song?.title).toBe("John 3:16–18");
    expect(resolveSlideSequence(john.song!.body_text, null).map((s) => s.lyrics)[0]).toContain("¹⁶For God");
    expect(presented.items[2]!.song?.title).toBe("Announcements");
    expect(presented.items[0]).toBe(SERVICE.items[0]);
  });

  it("gives a text item with no slides one empty slide to edit", () => {
    const empty = withServiceItems(detail([item({ id: "t", kind: "text", content: '{"title":"X"}' })]), "m");
    expect(parseSlidesOverride(empty.items[0]!.slides_override)).toEqual([
      { section: { type: "other", number: 1 }, lyrics: "" },
    ]);
  });

  it("keeps a line that looks like a section tag as text", () => {
    const odd: ScriptureContent = { reference: "Psalm 1:1", verses: [{ ref: "Psalm 1:1", text: "x" }], own_text: "[Selah]" };
    const it_ = withServiceItems(detail([item({ id: "p", kind: "scripture", content: JSON.stringify(odd) })]), "m");
    const slides = resolveSlideSequence(it_.items[0]!.song!.body_text, null);
    expect(slides).toHaveLength(1);
    expect(slides[0]!.lyrics).toContain("[Selah]");
  });

  it("names items in the running order", () => {
    expect(presented.items.map(itemLabel)).toEqual([
      "Amazing Grace",
      "John 3:16–18",
      "Announcements",
      "Amazing Grace",
    ]);
    expect(itemCountLabel(presented.items)).toBe("2 songs · 1 passage · 1 text");
    expect(itemCountLabel([presented.items[0]!])).toBe("1 song");
  });

  it("round-trips a reading through the library format", () => {
    const body = readingBody(["I believe in God,\nthe Father almighty", "  ", "Amen."]);
    expect(parseSlidesOverride(readingSlides(body))!.map((s) => s.lyrics)).toEqual([
      "I believe in God,\nthe Father almighty",
      "Amen.",
    ]);
  });
});

describe("the AI around a passage or text item", () => {
  const presented = withServiceItems(SERVICE, "m");

  it("keeps every item's place in what the engine is sent: a reading with its words, text with none", () => {
    const content = buildSetlistContent(presented);
    expect(content.items.map((i) => i.item_idx)).toEqual([0, 1, 2, 3]);
    expect(content.items[1]!.spoken).toBe(true);
    expect(content.items[1]!.slides[1]!.slice(0, 2)).toEqual(["for", "god"]);
    expect(content.items[2]!.slides).toEqual([[]]);
    expect(content.items[3]!.title).toBe("Amazing Grace");
  });

  const kinds: ItemKind[] = ["song", "scripture", "text", "song"];

  it("tells the engine the next song's start while a reading is up", () => {
    expect(aiAnchor(kinds, { itemIdx: 0, slideIdx: 2 })).toEqual({ itemIdx: 0, slideIdx: 2 });
    expect(aiAnchor(kinds, { itemIdx: 1, slideIdx: 1 })).toEqual({ itemIdx: 3, slideIdx: 0 });
    expect(aiAnchor(kinds, { itemIdx: 2, slideIdx: 0 })).toEqual({ itemIdx: 3, slideIdx: 0 });
    expect(aiAnchor(["song", "text"], { itemIdx: 1, slideIdx: 0 })).toEqual({ itemIdx: 0, slideIdx: 0 });
    expect(aiAnchor(["text"], { itemIdx: 0, slideIdx: 0 })).toBeNull();
  });

  it("only lets the AI leave a reading for a song that has started", () => {
    expect(aiMayLeave(kinds, 0, { itemIdx: 0, slideIdx: 0 })).toBe(true);
    expect(aiMayLeave(kinds, 1, { itemIdx: 3, slideIdx: 1 })).toBe(true);
    expect(aiMayLeave(kinds, 1, { itemIdx: 3, slideIdx: 0 })).toBe(false);
    expect(aiMayLeave(kinds, 1, { itemIdx: 0, slideIdx: 0 })).toBe(false);
  });
});

describe("read-along: a passage followed as it is read aloud", () => {
  const presented = withServiceItems(SERVICE, "m");

  it("hears only what is read: no verse numbers, no reference or copyright line", () => {
    expect(spokenWords("¹⁶For God so loved the world, ¹⁷For God didn't send\nJohn 3:16–17 · WEB")).toEqual([
      "for", "god", "so", "loved", "the", "world", "for", "god", "didn't", "send",
    ]);
    expect(spokenWords("that he gave\nJohn 3:16–18\nScripture taken from the NIV.")).toEqual([
      "that", "he", "gave",
    ]);
    expect(spokenWords("¹Yahweh is my shepherd\nPsalm 23:1 · KJV")).toEqual(["yahweh", "is", "my", "shepherd"]);
    // a line of scripture that merely contains a number stays
    expect(spokenWords("And there were 7 loaves")).toEqual(["and", "there", "were", "7", "loaves"]);
  });

  it("sends a passage's words, marked spoken, only with read-along on", () => {
    const on = buildSetlistContent(presented, { readAlong: true });
    const john = on.items[1]!;
    expect(john.spoken).toBe(true);
    expect(john.title).toBe("John 3:16–18");
    expect(john.slides[0]).toEqual([]); // the leading blank
    expect(john.slides[1]!.slice(0, 4)).toEqual(["for", "god", "so", "loved"]);
    expect(john.slides.flat()).not.toContain("web");
    expect(on.items[2]!.slides).toEqual([[]]); // text items still wait
    const off = buildSetlistContent(presented, { readAlong: false });
    expect(off.items[1]).toEqual({ item_idx: 1, song_id: "i_john", slides: [[]], slide_sections: ["other:0"] });
  });

  it("scrolls a passage on the words the engine follows, without reference lines", () => {
    const ctx = buildContext(presented);
    const john = ctx.items[1]!;
    const sent = buildSetlistContent(presented).items[1]!.slides;
    const flow = flowSlides(john.kind, john.slides);
    // the engine's word track counts exactly these words, slide by slide
    expect(flow.map((s) => s.words)).toEqual(sent);
    // and the column's lines hold the same number of words, so the track
    // lands on the line being read
    for (const layout of layoutFlow(flow)) {
      const lines = layout.lines;
      expect(lines[lines.length - 1]!.end - lines[0]!.start).toBe(flow[layout.slideIdx]!.words.length);
    }
    expect(flow.some((s) => s.lyrics.includes("WEB"))).toBe(false);
    expect(flow[1]!.lyrics.startsWith("¹⁶For God")).toBe(true);
    // songs pass through untouched
    expect(flowSlides("song", ctx.items[0]!.slides)).toBe(ctx.items[0]!.slides);
  });

  it("lets the AI follow a passage like a song only with read-along on", () => {
    const kinds: ItemKind[] = ["song", "scripture", "text", "song"];
    expect(aiKinds(kinds, false)).toEqual(kinds);
    const on = aiKinds(kinds, true);
    expect(on).toEqual(["song", "song", "text", "song"]);
    expect(aiAnchor(on, { itemIdx: 1, slideIdx: 2 })).toEqual({ itemIdx: 1, slideIdx: 2 });
    expect(aiMayLeave(on, 1, { itemIdx: 1, slideIdx: 3 })).toBe(true);
  });
});

describe("a passage asked for live that the service has prepared", () => {
  const ctx = buildContext(withServiceItems(SERVICE, "m"));
  // At this size each verse gets its own slide: blank, 16, 17, 18.
  const small = buildContext(withServiceItems(SERVICE, "xl"));

  it("goes to the slide showing its first verse", () => {
    expect(preparedSlideFor(ctx.items, "John 3:16")).toEqual({ itemIdx: 1, slideIdx: 1 });
    const at18 = preparedSlideFor(small.items, "John 3:18");
    expect(at18?.itemIdx).toBe(1);
    expect(small.items[1]!.slides[at18!.slideIdx]!.lyrics).toContain("¹⁸");
  });

  it("looks in the passage on screen first", () => {
    // "Back to verse 16" during a reading that also has its own copy earlier.
    const twice = [...ctx.items, ctx.items[1]!];
    const last = twice.length - 1;
    expect(preparedSlideFor(twice, "John 3:16", last)?.itemIdx).toBe(last);
    expect(preparedSlideFor(twice, "John 3:16", 0)?.itemIdx).toBe(1);
    expect(preparedSlideFor(twice, "John 3:16")?.itemIdx).toBe(1);
  });

  it("leaves anything else to be shown live", () => {
    expect(preparedSlideFor(ctx.items, "John 3:20")).toBeNull();
    expect(preparedSlideFor(ctx.items, "1 John 3:16")).toBeNull();
    expect(preparedSlideFor(ctx.items, "Romans 5:8")).toBeNull();
  });
});
