import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InfoTip } from "@/components/ui/InfoTip";

describe("InfoTip", () => {
  it("opens on click, closes on Escape and on a click outside", () => {
    render(
      <div>
        <InfoTip label="About reports">Never audio.</InfoTip>
        <p>elsewhere</p>
      </div>,
    );
    const button = screen.getByRole("button", { name: "About reports" });
    expect(screen.queryByText("Never audio.")).toBeNull();

    fireEvent.click(button);
    expect(screen.getByRole("note")).toHaveTextContent("Never audio.");
    expect(button).toHaveAttribute("aria-expanded", "true");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();

    fireEvent.click(button);
    fireEvent.mouseDown(screen.getByText("elsewhere"));
    expect(screen.queryByRole("note")).toBeNull();
  });
});
