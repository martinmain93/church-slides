import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantConnect } from "@/components/settings/AssistantConnect";
import type { McpConnectionInfo } from "@/lib/assistantConnect";
import { ToastProvider } from "@/lib/toast";

const api = vi.hoisted(() => ({
  getMcpConnectionInfo: vi.fn(),
  connectClaudeDesktop: vi.fn(),
  disconnectClaudeDesktop: vi.fn(),
}));
vi.mock("@/lib/assistantConnect", async (orig) => ({
  ...(await orig<typeof import("@/lib/assistantConnect")>()),
  ...api,
}));

function info(state: McpConnectionInfo["claude_desktop"]): McpConnectionInfo {
  return {
    command: "/Applications/Church Slides.app/Contents/MacOS/auto-presenter",
    args: ["--mcp"],
    config_snippet: "{}",
    claude_desktop: state,
    claude_desktop_config_path: "/cfg.json",
  };
}

function renderIt() {
  render(
    <ToastProvider>
      <AssistantConnect />
    </ToastProvider>,
  );
}

describe("AssistantConnect", () => {
  beforeEach(() => vi.clearAllMocks());

  it("connects Claude Desktop and says to restart it", async () => {
    api.getMcpConnectionInfo.mockResolvedValue(info("not_connected"));
    api.connectClaudeDesktop.mockResolvedValue(info("connected"));
    renderIt();
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    await screen.findByText(/Quit and reopen Claude/);
    expect(api.connectClaudeDesktop).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
  });

  it("offers a reconnect when registered to another copy", async () => {
    api.getMcpConnectionInfo.mockResolvedValue(info("stale"));
    renderIt();
    expect(await screen.findByRole("button", { name: "Reconnect" })).toBeTruthy();
  });

  it("can't connect when Claude Desktop is missing, but shows other apps' setup", async () => {
    api.getMcpConnectionInfo.mockResolvedValue(info("not_installed"));
    renderIt();
    const btn = await screen.findByRole("button", { name: "Connect" });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("Using a different app?"));
    await waitFor(() =>
      expect(screen.getByText(/claude mcp add church-slides/)).toBeTruthy(),
    );
  });

  it("explains itself outside the desktop app", async () => {
    api.getMcpConnectionInfo.mockRejectedValue(new Error("unimplemented mock command"));
    renderIt();
    expect(await screen.findByText("Available in the desktop app.")).toBeTruthy();
  });
});
