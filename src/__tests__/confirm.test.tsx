/**
 * `useConfirm` / `<ConfirmProvider>` — confirms the dialog renders,
 * resolves with `true`/`false` on the right buttons, and dismisses on
 * Escape / outside-click. Tauri's WKWebView doesn't implement
 * `window.confirm()`, so this hook is the only thing keeping
 * destructive flows working.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmProvider, useConfirm } from "@/lib/confirm";

function Harness({
  onResult,
  opts = { message: "Sure?" },
}: {
  onResult: (ok: boolean) => void;
  opts?: Parameters<ReturnType<typeof useConfirm>>[0];
}) {
  const confirm = useConfirm();
  return (
    <button
      type="button"
      onClick={async () => {
        const ok = await confirm(opts);
        onResult(ok);
      }}
    >
      open
    </button>
  );
}

function renderWithProvider(node: React.ReactNode) {
  return render(<ConfirmProvider>{node}</ConfirmProvider>);
}


describe("useConfirm", () => {
  it("renders a dialog when called and resolves true on Confirm", async () => {
    const onResult = vi.fn();
    renderWithProvider(<Harness onResult={onResult} />);

    fireEvent.click(screen.getByText("open"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /ok/i }));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
  });

  it("resolves false on Cancel", async () => {
    const onResult = vi.fn();
    renderWithProvider(<Harness onResult={onResult} />);

    fireEvent.click(screen.getByText("open"));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });

  it("resolves false on Escape and unmounts the dialog", async () => {
    const onResult = vi.fn();
    renderWithProvider(<Harness onResult={onResult} />);

    fireEvent.click(screen.getByText("open"));
    await screen.findByRole("dialog");
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("resolves true on Enter", async () => {
    const onResult = vi.fn();
    renderWithProvider(<Harness onResult={onResult} />);

    fireEvent.click(screen.getByText("open"));
    await screen.findByRole("dialog");
    act(() => {
      fireEvent.keyDown(window, { key: "Enter" });
    });

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
  });

  it("uses custom button labels", async () => {
    renderWithProvider(
      <Harness
        onResult={() => undefined}
        opts={{
          message: "Drop it?",
          confirmLabel: "Drop",
          cancelLabel: "Keep",
        }}
      />,
    );
    fireEvent.click(screen.getByText("open"));
    await screen.findByRole("dialog");
    expect(screen.getByRole("button", { name: "Drop" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep" })).toBeInTheDocument();
  });

  it("falls back to true when used without a provider", async () => {
    // Useful for component tests that don't bother wrapping; matches
    // the documented "soft fallback" behaviour in `confirm.tsx`.
    const onResult = vi.fn();
    render(<Harness onResult={onResult} />);
    fireEvent.click(screen.getByText("open"));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
  });
});
