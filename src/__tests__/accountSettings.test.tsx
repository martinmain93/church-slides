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
});
