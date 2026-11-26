import { describe, expect, it } from "vitest";

import { checkReference } from "@/core/bibleBooks";

describe("checking a typed Bible reference", () => {
  it("tidies short forms", () => {
    expect(checkReference("ps 23")).toEqual({ reference: "Psalm 23" });
    expect(checkReference("jn 3:16-17")).toEqual({ reference: "John 3:16–17" });
    expect(checkReference("1 cor 13")).toEqual({ reference: "1 Corinthians 13" });
    expect(checkReference("II Tim. 3:16")).toEqual({ reference: "2 Timothy 3:16" });
    expect(checkReference("Gen 1:1")).toEqual({ reference: "Genesis 1:1" });
    expect(checkReference("Jude 24")).toEqual({ reference: "Jude 1:24" });
    expect(checkReference("Sirach 2")).toEqual({ reference: "Sirach 2" });
  });

  it("says what's wrong in one line", () => {
    expect(checkReference("Hezekiah 4:2")).toEqual({ problem: "No book called Hezekiah." });
    expect(checkReference("Psalm 151")).toEqual({ problem: "Psalm has 150 chapters." });
    expect(checkReference("Ph 2")).toEqual({ problem: "Which book: Philippians or Philemon?" });
    expect(checkReference("John")).toEqual({ problem: "Write it like John 3:16 or Psalm 23." });
    expect(checkReference("John 3:18-16")).toEqual({ problem: "The last verse comes before verse 18." });
  });
});
