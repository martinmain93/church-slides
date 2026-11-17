/**
 * The import preview is summary-first: counts and one button, the per-song
 * table folded away for big imports, and only recent services by default.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChartImportDialog } from "@/components/library/ChartImportDialog";
import { emptyChart } from "@/lib/chords/chart";
import type { ImportFileResult, ImportedChartSong } from "@/lib/chords/import/types";
import { RECENT_SERVICES, splitRecentServices, type ImportedSetlist } from "@/lib/otherApps/types";
import type { SongRow } from "@/lib/tauri";

vi.mock("@/lib/tauri", async (orig) => ({
  ...(await orig<typeof import("@/lib/tauri")>()),
  importCharts: vi.fn(async (items: unknown[]) => ({
    songs_created: items.length,
    charts_attached: 0,
    song_ids: items.map((_, i) => `s-new-${i}`),
  })),
}));

function song(title: string): ImportedChartSong {
  return {
    title,
    artist: null,
    ccli: null,
    copyright: null,
    chart: { ...emptyChart(), sections: [{ label: "Verse 1", stanzas: [[{ text: `${title} words`, chords: [] }]] }] },
    order: null,
    format: "opensong",
    warnings: [],
  };
}

const files = (titles: string[]): ImportFileResult[] =>
  titles.map((t) => ({ fileName: t, songs: [song(t)], error: null }));

const DAY = 86_400_000;
const services = (n: number): ImportedSetlist[] =>
  Array.from({ length: n }, (_, i) => ({ name: `Service ${i}`, songs: ["Song 0"], date: 1_700_000_000_000 - i * 7 * DAY }));

describe("splitRecentServices", () => {
  it("keeps the newest dated services and every undated one", () => {
    const undated = { name: "Undated", songs: [] };
    const { recent, older } = splitRecentServices([...services(RECENT_SERVICES + 3), undated]);
    expect(recent).toHaveLength(RECENT_SERVICES + 1);
    expect(recent).toContain(undated);
    expect(older.map((s) => s.name)).toEqual(["Service 8", "Service 9", "Service 10"]);
  });
});

describe("import preview", () => {
  const library = [{ id: "s-held", title: "Song 0" } as SongRow];

  it("summarises a big import and folds the song table away", () => {
    const titles = Array.from({ length: 30 }, (_, i) => `Song ${i}`);
    render(
      <ChartImportDialog results={files(titles)} library={library} setlists={services(12)} source="OpenSong" onClose={() => {}} onImported={() => {}} />,
    );
    const summary = screen.getByTestId("import-summary");
    expect(summary.textContent).toContain("29 new songs");
    expect(summary.textContent).toContain("1 song already in your library");
    expect(summary.textContent).toContain(`${RECENT_SERVICES} recent services`);
    expect(screen.getByText(/Review 30 songs/).closest("details")!.open).toBe(false);
    expect(screen.queryAllByTestId("import-row")).toHaveLength(0); // not even built while folded
    expect(screen.getByTestId("import-confirm").textContent).toBe(`Import ${29 + RECENT_SERVICES} items`);

    fireEvent.click(screen.getByLabelText("Also bring 4 older services"));
    expect(screen.getByTestId("import-confirm").textContent).toBe("Import 41 items");
    expect(summary.textContent).toContain("12 services");
  });

  it("shows the table open for a few songs", () => {
    render(<ChartImportDialog results={files(["Song 1", "Song 2"])} library={[]} onClose={() => {}} onImported={() => {}} />);
    expect(screen.getByText(/Review 2 songs/).closest("details")!.open).toBe(true);
    expect(screen.getAllByTestId("import-row")).toHaveLength(2);
    expect(screen.getByTestId("import-confirm").textContent).toBe("Import 2 songs");
  });

  it("into a service: a song the library already has still joins it", async () => {
    let got: string[] | null = null;
    render(
      <ChartImportDialog
        results={files(["Song 0"])}
        library={library}
        serviceName="Sunday"
        onClose={() => {}}
        onImported={(_m, ids) => (got = ids)}
      />,
    );
    expect(screen.getByText(/added to Sunday/)).toBeTruthy();
    const button = screen.getByTestId("import-confirm");
    expect(button.textContent).toBe("Add 1 song to the service");
    fireEvent.click(button);
    await waitFor(() => expect(got).toEqual(["s-held"]));
  });

  it("into a service: new and existing songs come back in file order", async () => {
    let got: string[] | null = null;
    render(
      <ChartImportDialog
        results={files(["Song 1", "Song 0"])}
        library={library}
        serviceName="Sunday"
        onClose={() => {}}
        onImported={(_m, ids) => (got = ids)}
      />,
    );
    fireEvent.click(screen.getByTestId("import-confirm"));
    await waitFor(() => expect(got).toEqual(["s-new-0", "s-held"]));
  });
});
