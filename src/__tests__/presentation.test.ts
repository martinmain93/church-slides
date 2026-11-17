/**
 * Pure-state-machine tests for the presentation controller.
 *
 * These exercise transitions directly without a React renderer. The
 * companion hook (`hook.ts`) is just `useState` plumbing on top.
 */

import { describe, expect, it } from "vitest";

import {
  INITIAL_STATE,
  buildContext,
  jumpTo,
  nextSlide,
  nextSong,
  prevSlide,
  prevSong,
  startSession,
  toggleBlank,
  upcomingItems,
  upcomingSlides,
  type PresentationContext,
  type PresentationState,
} from "@/lib/presentation/state";
import type { SetlistDetail, SetlistItemWithSong, SongRow } from "@/lib/tauri";

function makeSong(
  id: string,
  title: string,
  body: string,
  slide_order: string | null = null,
): SongRow {
  return {
    id,
    title,
    body_text: body,
    ccli: null,
    author: null,
    copyright: null,
    default_tempo_wps: null,
    slide_order,
    created_at: 0,
    updated_at: 0,
  };
}

function makeItem(
  item_id: string,
  song: SongRow,
  position: number,
  slides_override: string | null = null,
): SetlistItemWithSong {
  return {
    id: item_id,
    setlist_id: "sl1",
    kind: "song",
    song_id: song.id,
    position,
    slides_override,
    song,
  };
}

function setlistOf(songs: SongRow[]): SetlistDetail {
  return {
    id: "sl1",
    name: "Sunday",
    created_at: 0,
    last_used_at: null,
    items: songs.map((s, i) => makeItem(`it_${i}`, s, i)),
  };
}

const SONG_A = makeSong(
  "a",
  "Alpha",
  "[verse 1]\nA1 line\n\nA2 line\n\n[chorus]\nA chorus",
);
// Alpha has 3 slides
const SONG_B = makeSong("b", "Bravo", "[verse 1]\nB1\n\nB2");
// Bravo has 2 slides
const SONG_C = makeSong("c", "Charlie", "[chorus]\nC only");
// Charlie has 1 slide

const ctx: PresentationContext = buildContext(setlistOf([SONG_A, SONG_B, SONG_C]));

function advance(
  state: PresentationState,
  fns: ((s: PresentationState) => PresentationState)[],
): PresentationState {
  return fns.reduce((acc, fn) => fn(acc), state);
}

describe("buildContext", () => {
  it("parses each item's body into slides, with a leading blank per song", () => {
    expect(ctx.items).toHaveLength(3);
    // Each song gets a synthetic blank "song start" slide at index 0.
    expect(ctx.items[0]!.slides).toHaveLength(4); // blank + Alpha's 3
    expect(ctx.items[1]!.slides).toHaveLength(3); // blank + Bravo's 2
    expect(ctx.items[2]!.slides).toHaveLength(2); // blank + Charlie's 1
    expect(ctx.items[0]!.slides[0]!.lyrics).toBe("");
  });

  it("expands a song's default slide order, repeating sections", () => {
    // Bravo has two verse-1 slides (B1, B2) and no chorus; order them as
    // V1, V1 → all four slides (both, twice), behind the leading blank.
    const ordered = makeSong("b2", "Bravo", "[verse 1]\nB1\n\nB2", "V1, V1");
    const detail = setlistOf([ordered]);
    const items = buildContext(detail).items;
    expect(items[0]!.slides.map((s) => s.lyrics)).toEqual([
      "",
      "B1",
      "B2",
      "B1",
      "B2",
    ]);
  });

  it("skips items whose song is missing (orphan)", () => {
    const detail: SetlistDetail = {
      id: "sl1",
      name: "x",
      created_at: 0,
      last_used_at: null,
      items: [
        makeItem("a", SONG_A, 0),
        { ...makeItem("orphan", SONG_B, 1), song: null },
      ],
    };
    expect(buildContext(detail).items).toHaveLength(1);
  });
});

describe("startSession", () => {
  it("places state at item 0 / slide 0 / blank none", () => {
    const s = startSession(ctx);
    expect(s).toEqual({
      setlistId: "sl1",
      itemIdx: 0,
      slideIdx: 0,
      blankMode: "none",
    });
  });
});

describe("nextSlide", () => {
  it("advances within a song", () => {
    const s = nextSlide(ctx, startSession(ctx));
    expect(s.itemIdx).toBe(0);
    expect(s.slideIdx).toBe(1);
  });

  it("crosses to the next song at the last slide", () => {
    // Move to last slide of Alpha (blank + 3 slides → last index 3)
    const atLast: PresentationState = {
      setlistId: "sl1",
      itemIdx: 0,
      slideIdx: 3,
      blankMode: "none",
    };
    const after = nextSlide(ctx, atLast);
    expect(after.itemIdx).toBe(1);
    expect(after.slideIdx).toBe(0);
  });

  it("stops at the end of the setlist", () => {
    // Charlie has blank + 1 slide → last index 1.
    const atEnd: PresentationState = {
      setlistId: "sl1",
      itemIdx: 2,
      slideIdx: 1,
      blankMode: "none",
    };
    const after = nextSlide(ctx, atEnd);
    expect(after).toEqual(atEnd);
  });
});

describe("prevSlide", () => {
  it("retreats within a song", () => {
    const at: PresentationState = { ...startSession(ctx), slideIdx: 1 };
    expect(prevSlide(ctx, at).slideIdx).toBe(0);
  });

  it("crosses backwards to the previous song's last slide", () => {
    const at: PresentationState = {
      setlistId: "sl1",
      itemIdx: 1,
      slideIdx: 0,
      blankMode: "none",
    };
    const after = prevSlide(ctx, at);
    expect(after.itemIdx).toBe(0);
    expect(after.slideIdx).toBe(3); // Alpha has blank + 3 slides, last index 3
  });

  it("stops at the start of the setlist", () => {
    const after = prevSlide(ctx, startSession(ctx));
    expect(after).toEqual(startSession(ctx));
  });
});

describe("nextSong / prevSong", () => {
  it("jumps to next song's first slide", () => {
    const s = nextSong(ctx, startSession(ctx));
    expect(s.itemIdx).toBe(1);
    expect(s.slideIdx).toBe(0);
  });

  it("does not move past the last song", () => {
    const last: PresentationState = {
      setlistId: "sl1",
      itemIdx: 2,
      slideIdx: 0,
      blankMode: "none",
    };
    expect(nextSong(ctx, last)).toEqual(last);
  });

  it("rewinds to the current song's first slide if not yet there", () => {
    const mid: PresentationState = { ...startSession(ctx), slideIdx: 2 };
    const after = prevSong(ctx, mid);
    expect(after.itemIdx).toBe(0);
    expect(after.slideIdx).toBe(0);
  });

  it("crosses back to the previous song's first slide otherwise", () => {
    const at: PresentationState = {
      setlistId: "sl1",
      itemIdx: 1,
      slideIdx: 0,
      blankMode: "none",
    };
    const after = prevSong(ctx, at);
    expect(after.itemIdx).toBe(0);
    expect(after.slideIdx).toBe(0);
  });
});

describe("jumpTo", () => {
  it("clamps out-of-range indices", () => {
    const after = jumpTo(ctx, startSession(ctx), 99, 99);
    expect(after.itemIdx).toBe(2); // last item (Charlie)
    expect(after.slideIdx).toBe(1); // Charlie has blank + 1 slide, last index 1
  });
});

describe("toggleBlank", () => {
  it("toggles same mode off", () => {
    const black = toggleBlank(startSession(ctx), "black");
    expect(black.blankMode).toBe("black");
    expect(toggleBlank(black, "black").blankMode).toBe("none");
  });

  it("switches between modes", () => {
    const black = toggleBlank(startSession(ctx), "black");
    const white = toggleBlank(black, "white");
    expect(white.blankMode).toBe("white");
  });
});

describe("upcomingSlides", () => {
  it("walks across song boundaries", () => {
    const at: PresentationState = {
      setlistId: "sl1",
      itemIdx: 0,
      slideIdx: 1, // Alpha slide 1 (blank is 0)
      blankMode: "none",
    };
    const next = upcomingSlides(ctx, at, 3);
    // Alpha slide 2, Alpha slide 3, then Bravo slide 0 (its blank).
    expect(next).toHaveLength(3);
    expect(next[0]!.itemIdx).toBe(0);
    expect(next[0]!.slideIdx).toBe(2);
    expect(next[1]!.itemIdx).toBe(0);
    expect(next[1]!.slideIdx).toBe(3);
    expect(next[2]!.itemIdx).toBe(1);
    expect(next[2]!.slideIdx).toBe(0);
  });

  it("returns fewer slides at the end of the setlist", () => {
    // Charlie's last slide (blank + 1 → index 1); nothing follows.
    const at: PresentationState = {
      setlistId: "sl1",
      itemIdx: 2,
      slideIdx: 1,
      blankMode: "none",
    };
    expect(upcomingSlides(ctx, at, 3)).toHaveLength(0);
  });
});

describe("upcomingItems", () => {
  it("returns the next n songs", () => {
    const items = upcomingItems(ctx, startSession(ctx), 5);
    expect(items.map((i) => i.song.title)).toEqual(["Bravo", "Charlie"]);
  });
});

describe("end-to-end navigation sequence", () => {
  it("walks the entire setlist via repeated nextSlide", () => {
    const visited: [number, number][] = [];
    let s = startSession(ctx);
    visited.push([s.itemIdx, s.slideIdx]);
    for (let i = 0; i < 20; i++) {
      const after = nextSlide(ctx, s);
      if (after.itemIdx === s.itemIdx && after.slideIdx === s.slideIdx) break;
      s = after;
      visited.push([s.itemIdx, s.slideIdx]);
    }
    expect(visited).toEqual([
      // Alpha: blank + 3 slides
      [0, 0],
      [0, 1],
      [0, 2],
      [0, 3],
      // Bravo: blank + 2 slides
      [1, 0],
      [1, 1],
      [1, 2],
      // Charlie: blank + 1 slide
      [2, 0],
      [2, 1],
    ]);
  });

  it("symmetric: walking back via prevSlide reverses the path", () => {
    const forward: [number, number][] = [];
    let s = startSession(ctx);
    forward.push([s.itemIdx, s.slideIdx]);
    for (let i = 0; i < 20; i++) {
      const after = nextSlide(ctx, s);
      if (after.itemIdx === s.itemIdx && after.slideIdx === s.slideIdx) break;
      s = after;
      forward.push([s.itemIdx, s.slideIdx]);
    }

    const backward: [number, number][] = [[s.itemIdx, s.slideIdx]];
    for (let i = 0; i < 20; i++) {
      const after = prevSlide(ctx, s);
      if (after.itemIdx === s.itemIdx && after.slideIdx === s.slideIdx) break;
      s = after;
      backward.push([s.itemIdx, s.slideIdx]);
    }
    expect(backward).toEqual([...forward].reverse());
  });
});

describe("INITIAL_STATE", () => {
  it("is a valid no-setlist state", () => {
    expect(INITIAL_STATE.setlistId).toBeNull();
    expect(INITIAL_STATE.itemIdx).toBe(0);
    expect(INITIAL_STATE.slideIdx).toBe(0);
    expect(INITIAL_STATE.blankMode).toBe("none");
  });

  it("nextSlide on an empty context does nothing", () => {
    const empty = buildContext({
      id: "sl1",
      name: "empty",
      created_at: 0,
      last_used_at: null,
      items: [],
    });
    expect(nextSlide(empty, INITIAL_STATE)).toEqual(INITIAL_STATE);
    expect(prevSlide(empty, INITIAL_STATE)).toEqual(INITIAL_STATE);
  });

  it("compounds correctly when chained", () => {
    const out = advance(startSession(ctx), [
      (s) => nextSlide(ctx, s),
      (s) => nextSlide(ctx, s),
      (s) => nextSlide(ctx, s), // crosses to Bravo
      (s) => prevSlide(ctx, s), // back to Alpha last
    ]);
    expect(out.itemIdx).toBe(0);
    expect(out.slideIdx).toBe(2);
  });
});
