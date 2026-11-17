import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AccountSettings } from "@/components/settings/AccountSettings";
import { settingsPages } from "@/components/settings/settingsPages";
import { ConfirmProvider } from "@/lib/confirm";
import { mockTauri } from "@/lib/mockTauri";
import { ToastProvider } from "@/lib/toast";

function renderAccount() {
  return render(
    <ToastProvider>
      <ConfirmProvider>
        <AccountSettings />
      </ConfirmProvider>
    </ToastProvider>,
  );
}

afterEach(async () => {
  cleanup();
  await mockTauri.account_sign_out();
});

describe("Settings → Account", () => {
  it("is a settings page in development builds", () => {
    expect(settingsPages().map((p) => p.id)).toContain("account");
  });

  it("signs in, shows the organizations, and signs out", async () => {
    renderAccount();
    fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Ada")).toBeInTheDocument();
    expect(screen.getByText("ada@example.org")).toBeInTheDocument();
    expect(screen.getByText("Grace Chapel")).toBeInTheDocument();
    expect(screen.getByText("Operator")).toBeInTheDocument();
    // The official server isn't named; only another server would be.
    expect(screen.queryByText(/^Server:/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("asks before signing out everywhere", async () => {
    await mockTauri.account_sign_in();
    renderAccount();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out everywhere" }));
    expect(await screen.findByText(/including this one/)).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: "Sign out everywhere" });
    fireEvent.click(buttons[buttons.length - 1]!);
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("joins through an invite link while signing in", async () => {
    renderAccount();
    fireEvent.click(await screen.findByRole("button", { name: "Join with an invite" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Join with an invite" }), {
      target: { value: "https://api.churchslides.app/join/csi_abc" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(await screen.findByText("Hope Church")).toBeInTheDocument();
  });

  it("creates an organization and invites people to it", async () => {
    await mockTauri.account_sign_in();
    renderAccount();
    fireEvent.click(await screen.findByRole("button", { name: "New organization" }));
    fireEvent.change(screen.getByRole("textbox", { name: "New organization" }), { target: { value: "New Life" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    // Now in two organizations that can run the live set: pick where services go.
    const liveAt = await screen.findByRole("combobox", { name: "Services go live at" });
    expect([...(liveAt as HTMLSelectElement).options].map((o) => o.text)).toEqual(["Grace Chapel", "New Life"]);

    const teams = screen.getAllByRole("button", { name: "Team" });
    fireEvent.click(teams[teams.length - 1]!);
    expect(await screen.findByText("(you)")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Role for new people" }), { target: { value: "operator" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite people" }));
    expect(await screen.findByRole("textbox", { name: "Invite link" })).toHaveValue(
      "https://api.churchslides.app/join/csi_mockInviteCode0123456789",
    );
    expect(screen.getByText(/join as operator for the next 14 days/)).toBeInTheDocument();
    // The owner can't leave; that needs a transfer first.
    expect(screen.queryByRole("button", { name: /^Leave/ })).toBeNull();
  });

  it("shows an operator their team without managing it, and lets them leave", async () => {
    await mockTauri.account_sign_in();
    renderAccount();
    fireEvent.click(await screen.findByRole("button", { name: "Team" }));
    expect(await screen.findByText("Pat")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invite people" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: /Role for/ })).toBeNull();
    // Other people's emails are for owners and admins.
    expect(screen.queryByText("pat@example.org")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Leave Grace Chapel" }));
    const leave = await screen.findAllByRole("button", { name: "Leave" });
    fireEvent.click(leave[leave.length - 1]!);
    expect(await screen.findByText(/not in an organization yet/)).toBeInTheDocument();
  });
});
