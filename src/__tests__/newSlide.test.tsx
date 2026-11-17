import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { SongPreview } from "@/components/library/SongPreview";

afterEach(cleanup);

let latest = "";
function Editor({ initial }: { initial: string }) {
  const [body, setBody] = useState(initial);
  latest = body;
  return <SongPreview body={body} onChangeBody={setBody} />;
}

describe("a new slide in the song editor", () => {
  it("starts with an empty box, so typing replaces the hint instead of joining it", () => {
    render(<Editor initial={"[verse 1]\nAmazing grace"} />);
    fireEvent.click(screen.getByRole("button", { name: /add slide/i }));
    const box = screen.getByPlaceholderText("New slide lyrics…") as HTMLTextAreaElement;
    expect(box.value).toBe("");
    fireEvent.change(box, { target: { value: "The sun comes up" } });
    expect(latest).toContain("The sun comes up");
    expect(latest).not.toContain("New slide lyrics");
  });

  it("is removed if it's left empty", () => {
    render(<Editor initial={"[verse 1]\nAmazing grace"} />);
    fireEvent.click(screen.getByRole("button", { name: /add slide/i }));
    const box = screen.getByPlaceholderText("New slide lyrics…");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(latest).not.toContain("New slide lyrics");
    expect(latest).toContain("Amazing grace");
  });
});
