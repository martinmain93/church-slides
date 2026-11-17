/**
 * Importing a church's own Bible: the report comes first, and Install stays
 * disabled until the rights box is ticked and the credit line is filled in.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { requestSidecar } = vi.hoisted(() => ({ requestSidecar: vi.fn() }));
vi.mock("@/lib/sidecarClient", () => ({ requestSidecar }));

import {
  ScriptureImportDialog,
  suggestDetails,
} from "@/components/scripture/ScriptureImportDialog";
import type { ScriptureImportReport } from "@/types/generated";

const REPORT: ScriptureImportReport = {
  format: "OSIS",
  title: "Our Church Bible",
  abbreviation: "ocb",
  rights: "",
  verse_count: 31102,
  books: [
    {
      code: "GEN",
      name: "Genesis",
      source_name: "Genezis",
      matched_by: "shape",
      chapters: 50,
      verses: 1533,
      section: "ot",
    },
    {
      code: "",
      name: "Odes",
      source_name: "Ódy",
      matched_by: "",
      chapters: 14,
      verses: 300,
      section: "",
    },
  ],
  missing: ["Exodus"],
  unmatched: ["Ódy"],
  warnings: ["Old Testament only."],
};

// jsdom's File has no arrayBuffer(); every real webview does.
if (!File.prototype.arrayBuffer) {
  File.prototype.arrayBuffer = function (this: File) {
    return new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(this);
    });
  };
}

function dropFile(name = "bible.xml") {
  const file = new File(["<osis/>"], name, { type: "text/xml" });
  fireEvent.drop(screen.getByRole("dialog"), { dataTransfer: { files: [file] } });
}

describe("ScriptureImportDialog", () => {
  beforeEach(() => {
    requestSidecar.mockReset();
  });

  it("shows the report, and installs only once rights and credit are given", async () => {
    requestSidecar.mockImplementation(async (msg: { kind: string }) => {
      if (msg.kind === "preview_scripture_import") {
        return { kind: "preview_scripture_import_response", token: "t1", report: REPORT };
      }
      if (msg.kind === "commit_scripture_import") {
        return { kind: "commit_scripture_import_response", translation: "my-ocb" };
      }
      throw new Error(`unexpected ${msg.kind}`);
    });
    const onImported = vi.fn();
    render(<ScriptureImportDialog onClose={() => {}} onImported={onImported} />);

    dropFile();
    await screen.findByText(/31,102 verses/);
    expect(screen.getByText(/Missing: Exodus/)).toBeTruthy();
    expect(screen.getByText(/Not recognised: Ódy/)).toBeTruthy();
    expect(screen.getByText("Old Testament only.")).toBeTruthy();
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Our Church Bible");
    expect((screen.getByLabelText("Short name") as HTMLInputElement).value).toBe("OCB");

    const install = screen.getByRole("button", { name: "Install" }) as HTMLButtonElement;
    expect(install.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Credit line"), {
      target: { value: "Our Church Bible © 2020. Used by permission." },
    });
    expect(install.disabled).toBe(true); // rights not yet confirmed
    fireEvent.click(screen.getByLabelText("Our church has the right to use this translation"));
    expect(install.disabled).toBe(false);

    fireEvent.click(install);
    await waitFor(() => expect(onImported).toHaveBeenCalledWith("my-ocb", "Our Church Bible"));
    const commit = requestSidecar.mock.calls.find(([m]) => m.kind === "commit_scripture_import")![0];
    expect(commit).toMatchObject({
      token: "t1",
      abbreviation: "OCB",
      attribution: "Our Church Bible © 2020. Used by permission.",
      rights_confirmed: true,
    });
  });

  it("says why a file couldn't be read, and lets another be chosen", async () => {
    requestSidecar.mockResolvedValue({
      kind: "preview_scripture_import_response",
      error: "This XML file (<html>) isn't a Bible format we recognise.",
    });
    render(<ScriptureImportDialog onClose={() => {}} onImported={() => {}} />);
    dropFile("page.xml");
    expect((await screen.findByRole("alert")).textContent).toMatch(/isn't a Bible format/);
    expect(screen.getByText(/Choose a Bible file/)).toBeTruthy();
  });
});

describe("suggestDetails", () => {
  it("fills the form from the file, and from the file name when the file says nothing", () => {
    expect(suggestDetails({ ...REPORT, rights: "Public Domain" }, "x.xml")).toEqual({
      name: "Our Church Bible",
      abbreviation: "OCB",
      attribution: "Our Church Bible (public domain)",
    });
    expect(
      suggestDetails({ ...REPORT, title: "", abbreviation: "", rights: "" }, "new_living-translation.zip"),
    ).toEqual({ name: "new living translation", abbreviation: "NLT", attribution: "" });
  });
});
