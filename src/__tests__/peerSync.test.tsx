import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SyncReviewDialog } from "@/components/peerSync/SyncReviewDialog";
import { decisionsFor, normalizeBody, planSetlistSync } from "@/lib/peerSync/plan";
import type { BundleSong, SetlistBundle, SongRow } from "@/lib/tauri";

const library: SongRow[] = [];
const applySetlistBundle = vi.fn();
vi.mock("@/lib/tauri", async (orig) => ({
  ...(await orig<typeof import("@/lib/tauri")>()),
  listSongs: () => Promise.resolve(library),
  applySetlistBundle: (...a: unknown[]) => applySetlistBundle(...a),
}));

function row(id: string, title: string, body: string, extra: Partial<SongRow> = {}): SongRow {
  return {
    id, title, body_text: body, ccli: null, author: null, copyright: null,
    default_tempo_wps: null, slide_order: null, created_at: 0, updated_at: 0, ...extra,
  } as SongRow;
}

function incoming(ref: string, title: string, body: string, extra: Partial<BundleSong> = {}): BundleSong {
  return {
    ref_id: ref, title, body_text: body, slide_order: null, ccli: null, author: null,
    copyright: null, default_tempo_wps: null, chart: null, scriptures: [], ...extra,
  };
}

const bundle: SetlistBundle = {
  protocol: 1,
  setlist_name: "Sunday",
  songs: [
    incoming("a", "Oceans (Where Feet May Fail)", "[v1]\r\nYou call me out upon the waters  \n"),
    incoming("b", "Way Maker", "[c]\nWay maker, miracle worker"),
    incoming("c", "Brand New Song", "[v]\nNew words"),
    incoming("d", "Goodness", "[v]\nI love you Lord", { ccli: "7117726" }),
  ],
  items: [{ song_ref: "a", slides_override: null }, { song_ref: "b", slides_override: null }],
};

const lib = [
  row("L1", "Oceans", "[v1]\nYou call me out upon the waters"),
  row("L2", "Way Maker", "[c]\nWay maker (older wording)"),
  // Different title, same CCLI: still the same song.
  row("L3", "Goodness of God", "[v]\nI love you Lord", { ccli: "7117726" }),
];

describe("planSetlistSync", () => {
  it("reuses identical songs, flags different words, adds the rest", () => {
    const rows = planSetlistSync(bundle, lib);
    expect(rows.map((r) => [r.song.ref_id, r.kind])).toEqual([
      ["a", "same"], ["b", "differs"], ["c", "new"], ["d", "same"],
    ]);
  });

  it("prefers an identical copy when the library has two with that title", () => {
    const two = [row("X1", "Way Maker", "[c]\nsomething else"), row("X2", "Way Maker", "[c]\nWay maker, miracle worker")];
    const r = planSetlistSync(bundle, two).find((x) => x.song.ref_id === "b")!;
    expect(r.kind).toBe("same");
    expect(r.kind === "same" && r.match.id).toBe("X2");
  });

  it("ignores line endings and spacing but not slide order", () => {
    expect(normalizeBody("a  b\r\n\n\n\nc ")).toBe("a b\n\nc");
    const ordered = { ...bundle, songs: [incoming("a", "Oceans", "[v1]\nYou call me out upon the waters", { slide_order: "V1, V1" })] };
    expect(planSetlistSync(ordered, lib)[0]!.kind).toBe("differs");
  });

  it("has no decisions until every differing song is chosen", () => {
    const rows = planSetlistSync(bundle, lib);
    expect(decisionsFor(rows, {})).toBeNull();
    expect(decisionsFor(rows, { b: "theirs" })).toEqual({
      a: { kind: "use_existing", song_id: "L1" },
      b: { kind: "replace_existing", song_id: "L2" },
      c: { kind: "new" },
      d: { kind: "use_existing", song_id: "L3" },
    });
    expect(decisionsFor(rows, { b: "both" })!.b).toEqual({ kind: "keep_both" });
    expect(decisionsFor(rows, { b: "mine" })!.b).toEqual({ kind: "use_existing", song_id: "L2" });
  });
});

describe("SyncReviewDialog", () => {
  it("won't import until the differing song is decided, then sends the decisions", async () => {
    library.splice(0, library.length, ...lib);
    applySetlistBundle.mockResolvedValue({
      setlist_id: "s", setlist_name: "Sunday", songs_created: 1, songs_reused: 3, songs_replaced: 0, charts_added: 0, backup_path: null,
    });
    const onImported = vi.fn();
    render(<SyncReviewDialog bundle={bundle} fromName="Sarah's MacBook" onCancel={() => {}} onImported={onImported} />);
    await waitFor(() => expect(screen.getAllByTestId("sync-conflict")).toHaveLength(1));
    const importBtn = screen.getByTestId("sync-import");
    expect(importBtn).toBeDisabled();
    expect(importBtn).toHaveTextContent("Choose for 1 more song");

    fireEvent.click(screen.getByRole("radio", { name: "Keep mine" }));
    expect(importBtn).toBeEnabled();
    fireEvent.click(importBtn);
    await waitFor(() => expect(onImported).toHaveBeenCalled());
    const [, decisions, from] = applySetlistBundle.mock.calls[0]!;
    expect(from).toBe("Sarah's MacBook");
    expect((decisions as Record<string, unknown>).b).toEqual({ kind: "use_existing", song_id: "L2" });
  });

  it("reviews a whole library as a summary, with one choice for every differing song", async () => {
    library.splice(0, library.length, ...lib, row("L4", "Brand New Song", "[v]\nOther words"));
    applySetlistBundle.mockReset().mockResolvedValue({
      setlist_id: "", setlist_name: "", songs_created: 0, songs_reused: 4, songs_replaced: 0, charts_added: 0, backup_path: null,
    });
    const whole: SetlistBundle = { ...bundle, setlist_name: "", items: [], library: true };
    render(<SyncReviewDialog bundle={whole} fromName="Sarah's MacBook" onCancel={() => {}} onImported={vi.fn()} />);
    expect(screen.getByTestId("sync-import")).toBeDisabled(); // still reading this library
    const summary = await screen.findByTestId("sync-library-summary");
    expect(summary).not.toHaveTextContent("new to this library");
    expect(summary).toHaveTextContent("2 songs already here");
    expect(summary).toHaveTextContent("2 songs here with different words");
    expect(screen.queryAllByTestId("sync-conflict")).toHaveLength(0);
    const importBtn = screen.getByTestId("sync-import");
    expect(importBtn).toBeDisabled();

    fireEvent.click(screen.getByRole("radio", { name: "Keep mine" }));
    expect(importBtn).toHaveTextContent("Import library");
    fireEvent.click(importBtn);
    await waitFor(() => expect(applySetlistBundle).toHaveBeenCalled());
    const [, decisions] = applySetlistBundle.mock.calls[0]!;
    expect(decisions).toMatchObject({
      b: { kind: "use_existing", song_id: "L2" },
      c: { kind: "use_existing", song_id: "L4" },
    });
  });
});
