/**
 * Find a translation: eBible.org's public-domain Bibles, English first, and a
 * download reports back the id it was installed under.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { requestSidecar } = vi.hoisted(() => ({ requestSidecar: vi.fn() }));
vi.mock("@/lib/sidecarClient", () => ({ requestSidecar }));

import { TranslationBrowserDialog, contents } from "@/components/scripture/TranslationBrowserDialog";

const ENTRIES = [
  { id: "engwebp", store_id: "web", title: "World English Bible", abbreviation: "WEB", language: "English", ot_books: 39, nt_books: 27, dc_books: 0, installed: true },
  { id: "engbsb", store_id: "engbsb", title: "Berean Standard Bible", abbreviation: "BSB", language: "English", ot_books: 39, nt_books: 27, dc_books: 0, installed: false },
  { id: "spaRV1909", store_id: "sparv1909", title: "Reina Valera 1909", abbreviation: "RV1909", language: "Spanish", ot_books: 39, nt_books: 27, dc_books: 0, installed: false },
];

describe("TranslationBrowserDialog", () => {
  beforeEach(() => {
    requestSidecar.mockReset().mockImplementation(async (msg: { kind: string; translation?: string }) => {
      if (msg.kind === "search_scripture_catalog") return { entries: ENTRIES };
      if (msg.kind === "install_scripture_translation") {
        return { translation: "engbsb", installed: true };
      }
      throw new Error(msg.kind);
    });
  });

  it("shows English first, other languages on request, and downloads", async () => {
    const onInstalled = vi.fn();
    render(<TranslationBrowserDialog onClose={() => {}} onInstalled={onInstalled} />);
    await screen.findByText("Berean Standard Bible");
    expect(screen.queryByText("Reina Valera 1909")).toBeNull();
    expect(screen.getByText("Downloaded")).toBeTruthy(); // the WEB, already here

    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "" } });
    expect(screen.getByText("Reina Valera 1909")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Download Berean Standard Bible"));
    await waitFor(() => expect(onInstalled).toHaveBeenCalledWith("engbsb", "Berean Standard Bible"));
    expect(screen.getAllByText("Downloaded")).toHaveLength(2);
  });

  it("describes what each translation contains", () => {
    const base = { id: "x", store_id: "x", title: "x" };
    expect(contents({ ...base, ot_books: 39, nt_books: 27, dc_books: 15 })).toBe("Whole Bible + deuterocanon");
    expect(contents({ ...base, ot_books: 0, nt_books: 27, dc_books: 0 })).toBe("New Testament");
    expect(contents({ ...base, ot_books: 5, nt_books: 4, dc_books: 0 })).toBe("Portions");
    // A Septuagint's 37 books are its whole Old Testament.
    expect(contents({ ...base, ot_books: 37, nt_books: 0, dc_books: 16 })).toBe(
      "Old Testament + deuterocanon",
    );
  });
});
