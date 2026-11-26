import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { SongPreview } from "@/components/library/SongPreview";

afterEach(cleanup);

// Public-domain words only.
let latest = "";
function Editor({ initial, hasChords = false }: { initial: string; hasChords?: boolean }) {
  const [body, setBody] = useState(initial);
  latest = body;
  return <SongPreview body={body} onChangeBody={setBody} hasChords={hasChords} />;
}

describe("splitting a slide in the library song editor", () => {
  it("cuts the slide in two, both in its section", () => {
    render(<Editor initial={"[verse 1]\nAmazing grace how sweet the sound\nThat saved a wretch like me\nI once was lost but now am found"} />);
    expect(screen.getAllByTestId("preview-slide")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Split slide in two" }));
    expect(screen.getAllByTestId("preview-slide")).toHaveLength(2);
    // Nearest the middle by words: the long first line on its own.
    expect(latest).toBe("[verse 1]\n\nAmazing grace how sweet the sound\n\nThat saved a wretch like me\nI once was lost but now am found\n");
  });

  it("offers no split on an ordinary two-line slide, and stands out on a crowded one", () => {
    render(<Editor initial={"[verse 1]\nAmazing grace\nHow sweet the sound\n\nThat saved a wretch like me\nI once was lost\nBut now am found\nWas blind but now I see"} />);
    const cards = screen.getAllByTestId("preview-slide");
    expect(cards[0]!.querySelector('[aria-label="Split slide in two"]')).toBeNull();
    expect(cards[1]!.querySelector('[aria-label="Split slide in two"]')?.className).toContain("amber");
  });

  it("keeps to line breaks in a song with chords, so no line loses its chords", () => {
    render(<Editor initial={"[verse 1]\nPraise God from whom all blessings flow; praise him all creatures here below"} hasChords />);
    expect(screen.queryByRole("button", { name: "Split slide in two" })).toBeNull();
  });

  it("offers no split on a new slide still being typed", () => {
    render(<Editor initial={"[verse 1]\nAmazing grace"} />);
    fireEvent.click(screen.getByRole("button", { name: /add slide/i }));
    const cards = screen.getAllByTestId("preview-slide");
    expect(cards[1]!.querySelector('[aria-label="Split slide in two"]')).toBeNull();
  });
});
