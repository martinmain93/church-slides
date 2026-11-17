/**
 * Add hymns: the list starts at "public domain everywhere", every other
 * status is labelled with why, and Add stores the hymn with its notice.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { requestSidecar, createSong, listSongs } = vi.hoisted(() => ({
  requestSidecar: vi.fn(),
  createSong: vi.fn(),
  listSongs: vi.fn(),
}));
vi.mock("@/lib/sidecarClient", () => ({ requestSidecar }));
vi.mock("@/lib/tauri", () => ({ createSong, listSongs, openLink: vi.fn() }));

import { HymnBrowserDialog } from "@/components/library/HymnBrowserDialog";
import { ToastProvider } from "@/lib/toast";

const RESULTS = [
  {
    hymn_id: "It_Is_Well",
    title: "It Is Well With My Soul",
    first_line: "When peace, like a river, attendeth my way",
    author: "Spafford, H.",
    status: "public_domain",
    status_label: "Public domain",
  },
  {
    hymn_id: "Great_Is_Thy_Faithfulness",
    title: "Great Is Thy Faithfulness",
    status: "public_domain_us",
    status_label: "Public domain in the US",
  },
  {
    hymn_id: "Amazing_Grace",
    title: "Amazing Grace",
    status: "public_domain",
    status_label: "Public domain",
  },
];

const DETAIL = {
  hymn_id: "It_Is_Well",
  title: "It Is Well With My Soul",
  body_text: "[verse 1]\nWhen peace, like a river, attendeth my way,\n\n[chorus]\nIt is well\n",
  slide_order: "V1, C",
  author: "Horatio Gates Spafford",
  copyright: "Words: Horatio G. Spafford, 1873. Public domain.",
  status: "public_domain",
  status_label: "Public domain",
  explanation: "Free to use anywhere.",
  source_url: "http://openhymnal.org/Lyrics/It_Is_Well.html",
};

function renderDialog(onAdded = vi.fn()) {
  render(
    <ToastProvider>
      <HymnBrowserDialog onClose={() => {}} onAdded={onAdded} />
    </ToastProvider>,
  );
  return onAdded;
}

describe("HymnBrowserDialog", () => {
  beforeEach(() => {
    requestSidecar.mockReset();
    createSong.mockReset().mockResolvedValue("song_new");
    listSongs.mockReset().mockResolvedValue([{ title: "Amazing Grace" }]);
    requestSidecar.mockImplementation(async (msg: { kind: string }) => {
      if (msg.kind === "search_hymns") return { kind: "search_hymns_response", results: RESULTS };
      if (msg.kind === "get_hymn") return { kind: "get_hymn_response", hymn: DETAIL };
      throw new Error(msg.kind);
    });
  });

  it("starts at public domain everywhere, and shows the rest with their status", async () => {
    renderDialog();
    await screen.findByText("It Is Well With My Soul");
    expect(screen.queryByText("Great Is Thy Faithfulness")).toBeNull();
    expect(screen.getByText("(1 more hidden)")).toBeTruthy();
    // Already in the library: marked.
    await waitFor(() => expect(screen.getByLabelText("In your library")).toBeTruthy());

    fireEvent.click(screen.getByLabelText("Only hymns that are public domain everywhere"));
    expect(screen.getByText("Great Is Thy Faithfulness")).toBeTruthy();
    expect(screen.getByText("Public domain in the US")).toBeTruthy();
  });

  it("previews a hymn and adds it with its notice and order", async () => {
    const onAdded = renderDialog();
    fireEvent.click(await screen.findByText("It Is Well With My Soul"));
    await screen.findByText("Free to use anywhere.");
    expect(screen.getByText("Sung as: V1, C")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Add to library/ }));
    await waitFor(() => expect(onAdded).toHaveBeenCalled());
    expect(createSong).toHaveBeenCalledWith({
      title: "It Is Well With My Soul",
      body_text: DETAIL.body_text,
      author: "Horatio Gates Spafford",
      copyright: "Words: Horatio G. Spafford, 1873. Public domain.",
      slide_order: "V1, C",
    });
    expect(await screen.findByText("In your library")).toBeTruthy();
  });

  it("says when the Open Hymnal can't be reached", async () => {
    requestSidecar.mockResolvedValue({
      kind: "search_hymns_response",
      error: "Couldn't reach the Open Hymnal (openhymnal.org).",
    });
    renderDialog();
    expect((await screen.findByRole("alert")).textContent).toMatch(/Couldn't reach/);
  });
});
