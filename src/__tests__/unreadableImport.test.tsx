/**
 * The import's fallback for a format it doesn't recognise: a plain message,
 * a report of the file's shape (never its contents) when error reports are
 * on, and a pre-filled note to the developers when they're off.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const tauri = vi.hoisted(() => ({
  getSettings: vi.fn(),
  reportUnreadableImport: vi.fn(async () => undefined),
  openLink: vi.fn(async () => undefined),
}));
vi.mock("@/lib/tauri", async (orig) => ({ ...(await orig<object>()), ...tauri }));

import { UnreadableFiles } from "@/components/library/UnreadableFiles";
import { OPEN_FEEDBACK_EVENT } from "@/components/FeedbackDialog";
import { readImportFiles } from "@/lib/chords/import";

const failed = [{ fileName: "service.ewsx", songs: [], error: "No lyrics or chords found in this file", signature: ".ewsx · 40 bytes · starts 50 4b 03 04" }];

beforeEach(() => vi.clearAllMocks());

describe("unreadable files", () => {
  it("records a file's shape, not its contents", async () => {
    const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]);
    const file = { name: "Sunday.ewsx", size: bytes.length, arrayBuffer: async () => bytes.buffer } as unknown as File;
    const read = await readImportFiles([file], { isWholeLibrary: () => false });
    expect(read.results[0]!.error).not.toBeNull();
    expect(read.results[0]!.signature).toBe(".ewsx · 18 bytes · starts 50 4b 03 04 01 02 03 04 05 06 07 08 09 0a 0b 0c");
  });

  it("refuses a binary file instead of reading it as lyrics", async () => {
    // "SQLite format 3\0" and some binary — an app's database, not a song.
    const bytes = new Uint8Array([...new TextEncoder().encode("SQLite format 3"), 0, 1, 2, 3, 0, 0, 7]);
    const file = { name: "Sunday schedule.ewsx", size: bytes.length, arrayBuffer: async () => bytes.buffer } as unknown as File;
    const read = await readImportFiles([file], { isWholeLibrary: () => false });
    expect(read.results[0]!.songs).toHaveLength(0);
    expect(read.results[0]!.error).toBe("Not a format Church Slides recognises");
  });

  it("reports automatically when error reports are on", async () => {
    tauri.getSettings.mockResolvedValue({ send_error_reports: true });
    render(<UnreadableFiles failed={failed} />);
    expect(screen.getByText(/don.t recognise that file.s format/)).toBeTruthy();
    await waitFor(() => expect(tauri.reportUnreadableImport).toHaveBeenCalledWith("ewsx", failed[0]!.error, failed[0]!.signature));
    expect(await screen.findByText(/let the developers know/)).toBeTruthy();
    expect(screen.queryByText("Send note to developers")).toBeNull();
  });

  it("offers a pre-filled note when they're off, and sends nothing", async () => {
    tauri.getSettings.mockResolvedValue({ send_error_reports: false });
    const opened = vi.fn();
    window.addEventListener(OPEN_FEEDBACK_EVENT, (e) => opened((e as CustomEvent).detail));
    render(<UnreadableFiles failed={failed} />);
    fireEvent.click(await screen.findByText("Send note to developers"));
    expect(opened.mock.calls[0]![0].message).toContain(".ewsx · 40 bytes");
    expect(tauri.reportUnreadableImport).not.toHaveBeenCalled();
  });
});
