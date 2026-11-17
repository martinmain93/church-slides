import { describe, expect, it } from "vitest";

import { isHymn, normalizeTitle } from "@/lib/smartSearch/hymns";

const song = (title: string, body_text = "", copyright: string | null = null) => ({ title, body_text, copyright });

describe("isHymn", () => {
  it("knows hymns by title, including longer and numbered names", () => {
    expect(isHymn(song("Great Is Thy Faithfulness"))).toBe(true);
    expect(isHymn(song("It Is Well With My Soul (2)"))).toBe(true);
    expect(isHymn(song("Holy Holy Holy (Nicaea)"))).toBe(true);
    expect(isHymn(song("O Come All Ye Faithful"))).toBe(true);
  });

  it("leaves modern songs that share a hymn's short title alone", () => {
    expect(isHymn(song("It Is Well"))).toBe(false);
    expect(isHymn(song("At The Cross (Love Ran Red)"))).toBe(false);
    expect(isHymn(song("Reckless Love", "Oh the deep unending mercy of God"))).toBe(false);
  });

  it("reads public domain and old wording as a hymn", () => {
    expect(isHymn(song("Some Old Text", "", "Words: 1847. Public domain."))).toBe(true);
    const archaic = "Thou art my God and thee I praise / Thy mercy hath no end / Thine is the glory";
    expect(isHymn(song("Unknown Hymn", archaic))).toBe(true);
  });

  it("normalizes titles", () => {
    expect(normalizeTitle("How Deep The Father's Love For Us")).toBe("how deep the fathers love for us");
  });
});
