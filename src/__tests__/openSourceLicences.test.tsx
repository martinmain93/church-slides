import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OpenSourceLicencesLink } from "@/components/settings/OpenSourceLicences";

const api = vi.hoisted(() => ({ readThirdPartyNotices: vi.fn() }));
vi.mock("@/lib/tauri", async (orig) => ({
  ...(await orig<typeof import("@/lib/tauri")>()),
  ...api,
}));

describe("OpenSourceLicencesLink", () => {
  it("shows the bundled notices and closes on Escape", async () => {
    api.readThirdPartyNotices.mockResolvedValue("serde 1.0.228 — MIT OR Apache-2.0");
    render(<OpenSourceLicencesLink />);

    fireEvent.click(screen.getByRole("button", { name: "Open-source licences" }));
    expect(await screen.findByText(/serde 1\.0\.228/)).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("says so when the file can't be read", async () => {
    api.readThirdPartyNotices.mockRejectedValue("couldn't read THIRD-PARTY-NOTICES.txt");
    render(<OpenSourceLicencesLink />);

    fireEvent.click(screen.getByRole("button", { name: "Open-source licences" }));
    expect(await screen.findByText(/Couldn.t open the licences/)).toBeInTheDocument();
  });
});
