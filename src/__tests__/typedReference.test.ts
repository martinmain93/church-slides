import { describe, expect, it } from "vitest";

import { tidyTyped, versesOf, wholeChapterReference } from "@/lib/scripture/typedReference";
import type { ScripturePassage } from "@/types/generated";

describe("reading a reference while it is typed", () => {
  it("drops a separator the typist hasn't finished", () => {
    expect(tidyTyped("John 3:")).toBe("John 3");
    expect(tidyTyped("John 3:16-")).toBe("John 3:16");
    expect(tidyTyped("John 3:16–")).toBe("John 3:16");
    expect(tidyTyped(" Rom 5:8 ")).toBe("Rom 5:8");
  });

  it("reads a book and a chapter as the whole chapter", () => {
    expect(wholeChapterReference("John 3")).toBe("John 3:1-999");
    expect(wholeChapterReference("john 3:")).toBe("john 3:1-999");
    expect(wholeChapterReference("1 Cor 13")).toBe("1 Cor 13:1-999");
    expect(wholeChapterReference("Song of Solomon 2")).toBe("Song of Solomon 2:1-999");
    expect(wholeChapterReference("Ps 23")).toBe("Ps 23:1-999");
  });

  it("leaves verses, words and bare numbers alone", () => {
    expect(wholeChapterReference("John 3:16")).toBeNull();
    expect(wholeChapterReference("John")).toBeNull();
    expect(wholeChapterReference("3")).toBeNull();
    expect(wholeChapterReference("for God so loved the world")).toBeNull();
  });

  it("lays a chapter out verse by verse", () => {
    const chapter: ScripturePassage = {
      reference: "Psalm 23:1–2",
      translation: "web",
      abbreviation: "WEB",
      verses: [
        { ref: "Psalm 23:1", text: "Yahweh is my shepherd" },
        { ref: "Psalm 23:2", text: "He makes me lie down" },
      ],
      text: "Yahweh is my shepherd He makes me lie down",
    };
    expect(versesOf(chapter).map((p) => [p.reference, p.text, p.abbreviation])).toEqual([
      ["Psalm 23:1", "Yahweh is my shepherd", "WEB"],
      ["Psalm 23:2", "He makes me lie down", "WEB"],
    ]);
  });
});
