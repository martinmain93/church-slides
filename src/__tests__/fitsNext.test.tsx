import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LibrarySheet } from "@/components/band/LeaderSheets";
import { fitsNext, playedMeta } from "@/lib/band/fitsNext";
import type { LibrarySong } from "@/lib/band/librarySearch";
import { isBandCommand, type BandSong } from "@/lib/band/protocol";
import { resolveBandCommand } from "@/lib/band/useBandHost";
import type { ChordChart } from "@/lib/chords/chart";
import type { PresentationContext } from "@/lib/presentation/state";

function chart(key: string | null, tempo: number | null, time: string | null): ChordChart {
  return { version: 1, key, tempo, time, sections: [] };
}

/** Written in G, played in A this service (+2). */
const current: BandSong = {
  itemId: "it1",
  songId: "now",
  title: "Now Playing",
  author: null,
  slides: [],
  chart: chart("G", 72, "4/4"),
  transpose: 2,
};

function lib(id: string, key: string | null, tempo: number | null, time: string | null, extra: Partial<LibrarySong> = {}): LibrarySong {
  return { id, title: id, author: null, body: "", lastUsed: null, uses: 0, key, tempo, time, hasChart: true, ...extra };
}

describe("fits next", () => {
  it("scores against the key the song is played in, not its library key", () => {
    expect(playedMeta(current).key).toBe("A");
    const out = fitsNext(current, [lib("inA", "A", 74, "4/4"), lib("inG", "G", 200, "6/8", { hasChart: false })], new Set());
    // Same key as the PLAYED key; the G song (current's written key) doesn't fit.
    expect(out.map((f) => f.song.id)).toEqual(["inA"]);
    expect(out[0]).toMatchObject({ reason: "Same key, same tempo, also 4/4", key: "A", semitones: 0 });
  });

  it("still suggests a far-key song on tempo and time, as written", () => {
    // F -> A would be +4: too far to move, so it's offered in F.
    const out = fitsNext(current, [lib("inF", "F", 70, "4/4")], new Set());
    expect(out[0]).toMatchObject({ key: "F", semitones: 0, reason: "Same tempo, also 4/4" });
  });

  it("moves at most a few semitones, and only songs with a chart", () => {
    // G -> A is +2; F -> A would be +4 (too far), so F stays unmoved and doesn't fit on key.
    const moved = fitsNext(current, [lib("inG", "G", 72, "4/4")], new Set());
    expect(moved[0]).toMatchObject({ key: "A", semitones: 2, reason: "Play in A (+2), same tempo, also 4/4" });
    const far = fitsNext(current, [lib("inF", "F", 72, "4/4")], new Set());
    expect(far[0]?.semitones ?? 0).toBe(0);
    const noChart = fitsNext(current, [lib("inG", "G", 72, "4/4", { hasChart: false })], new Set());
    expect(noChart.every((f) => f.semitones === 0)).toBe(true);
  });

  it("leaves out songs in the set, the song itself, and songs it knows nothing about", () => {
    const out = fitsNext(
      current,
      [lib("now", "A", 72, "4/4"), lib("set", "A", 72, "4/4"), lib("blank", null, null, null), lib("ok", "E", 80, "4/4")],
      new Set(["set"]),
    );
    expect(out.map((f) => f.song.id)).toEqual(["ok"]);
    expect(out[0]!.reason).toBe("Close key, similar tempo, also 4/4");
  });

  it("is empty when the current song's chart says nothing", () => {
    expect(fitsNext({ ...current, chart: null }, [lib("x", "A", 72, "4/4")], new Set())).toEqual([]);
    expect(fitsNext(undefined, [lib("x", "A", 72, "4/4")], new Set())).toEqual([]);
  });

  it("keeps the list short, best first", () => {
    const many = ["A", "A", "A", "A", "A"].map((k, i) => lib(`s${i}`, k, 72 + i * 3, "4/4"));
    const out = fitsNext(current, many, new Set());
    expect(out.map((f) => f.song.id)).toEqual(["s0", "s1", "s2"]);
  });
});

describe("add_song with a key", () => {
  it("carries the key through validation and resolution", () => {
    const cmd = { kind: "add_song", songId: "s9", when: "next", key: "A" } as const;
    expect(isBandCommand(cmd)).toBe(true);
    expect(isBandCommand({ ...cmd, key: 3 })).toBe(false);
    const ctx = { items: [] } as unknown as PresentationContext;
    expect(resolveBandCommand(cmd, ctx)).toEqual({ type: "add", songId: "s9", goNow: false, key: "A" });
  });
});

describe("leader picker", () => {
  it("shows Fits next before typing, and adds a moved song in its new key", () => {
    const onAdd = vi.fn();
    const ref = { current: null as HTMLInputElement | null };
    render(
      <LibrarySheet
        library={[lib("Moved Song", "G", 72, "4/4"), lib("Other", null, null, null, { lastUsed: 5 })]}
        current={current}
        inSet={new Map()}
        midSong={false}
        onAdd={onAdd}
        onGoExisting={() => undefined}
        onBack={() => undefined}
        onClose={() => undefined}
        inputRef={ref}
      />,
    );
    const fits = screen.getByTestId("fits-next");
    expect(fits.textContent).toContain("Moved Song");
    expect(fits.textContent).toContain("Play in A (+2)");
    act(() => screen.getByText("Moved Song").click());
    act(() => screen.getByText("Play next").click());
    expect(onAdd).toHaveBeenCalledWith("Moved Song", "next", "A");

    // Typing a search hides the suggestions.
    act(() => {
      fireEvent.change(screen.getByLabelText("Search the library"), { target: { value: "oth" } });
    });
    expect(screen.queryByTestId("fits-next")).toBeNull();
  });
});
