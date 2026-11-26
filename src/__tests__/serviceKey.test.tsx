import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ServiceKeyBadge } from "@/components/ServiceKeyBadge";
import { buildBandSetlist, serviceTranspose } from "@/lib/band/useBandHost";
import { emptyChart, type ChordChart } from "@/lib/chords/chart";
import { semitonesToKey } from "@/lib/chords/chord";
import { buildContext } from "@/lib/presentation/state";
import type { SetlistDetail } from "@/lib/tauri";

const inKey = (key: string): ChordChart => ({ ...emptyChart(), key });

describe("a song's key for one service", () => {
  it("moves the chart the short way round, and reads minor charts by their major", () => {
    expect(semitonesToKey("G", "Bb")).toBe(3);
    expect(serviceTranspose(inKey("G"), "Bb")).toBe(3);
    expect(serviceTranspose(inKey("G"), "E")).toBe(-3);
    expect(serviceTranspose(inKey("Em"), "Gm")).toBe(3);
    expect(serviceTranspose(inKey("Em"), "G")).toBe(0);
    expect(serviceTranspose(inKey("G"), null)).toBe(0);
    expect(serviceTranspose(null, "Bb")).toBe(0);
  });

  it("is what the band screens are sent, unless the leader moved it this service", () => {
    const detail: SetlistDetail = {
      id: "sl", name: "Sunday", created_at: 0, last_used_at: null,
      items: [{
        id: "it1", setlist_id: "sl", kind: "song", song_id: "s1", position: 0,
        slides_override: null, song_key: "Bb",
        song: {
          id: "s1", title: "Song", body_text: "[V1]\nWords", ccli: null, author: null, copyright: null,
          default_tempo_wps: null, slide_order: null, created_at: 0, updated_at: 0,
        },
      }],
    } as SetlistDetail;
    const ctx = buildContext(detail);
    expect(buildBandSetlist(ctx, { s1: inKey("G") }, {}).songs[0]!.transpose).toBe(3);
    expect(buildBandSetlist(ctx, { s1: inKey("G") }, { it1: 1 }).songs[0]!.transpose).toBe(1);
  });

  it("picks a key from the badge, and the song's own key clears it", () => {
    const onChange = vi.fn();
    const { rerender } = render(<ServiceKeyBadge originalKey="G" serviceKey={null} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("service-key"));
    fireEvent.click(screen.getByTestId("service-key-Bb"));
    expect(onChange).toHaveBeenLastCalledWith("Bb");

    rerender(<ServiceKeyBadge originalKey="G" serviceKey="Bb" onChange={onChange} />);
    expect(screen.getByTestId("service-key").getAttribute("title")).toContain("(original key G)");
    fireEvent.click(screen.getByTestId("service-key"));
    fireEvent.click(screen.getByTestId("service-key-G"));
    expect(onChange).toHaveBeenLastCalledWith(null);
    fireEvent.click(screen.getByTestId("service-key"));
    fireEvent.click(screen.getByText("Original key (G)"));
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("offers minor keys for a song in a minor key", () => {
    render(<ServiceKeyBadge originalKey="Em" serviceKey={null} onChange={() => undefined} />);
    fireEvent.click(screen.getByTestId("service-key"));
    expect(screen.getByTestId("service-key-Gm")).toBeTruthy();
    expect(screen.queryByTestId("service-key-Bb")).toBeNull();
  });
});
