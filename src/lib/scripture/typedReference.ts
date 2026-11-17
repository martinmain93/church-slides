/**
 * Reading what someone is typing into the Scripture box, before it is a
 * reference the Bible accepts.
 *
 * The reference parser wants chapter *and* verse ("John 3:16"). Halfway
 * there — "John 3", "John 3:", "John 3:16-" — it refuses, and the box used to
 * fall back to searching verse text for the words, which for "John 3" meant
 * every verse that mentions John. Someone typing a book and a number means
 * that chapter.
 */

import type { ScripturePassage } from "@/types/generated";

/** What was typed, without a separator the typist hasn't finished:
 * "John 3:" -> "John 3", "John 3:16-" -> "John 3:16". */
export function tidyTyped(q: string): string {
  return q.trim().replace(/[\s:.,;‐-―−-]+$/u, "").trim();
}

/** The last verse asked for when a whole chapter is meant; the Bible stops
 * at the chapter's real last verse ("John 3:1-999" is "John 3:1–36"). */
const WHOLE_CHAPTER = "1-999";

/**
 * "John 3" or "1 Cor 13" — a book and a chapter, no verse — as the reference
 * for that whole chapter, or null for anything else. A book name is letters
 * (any script), optionally after a leading 1–4 ("1 John").
 */
export function wholeChapterReference(q: string): string | null {
  const m = /^((?:[1-4]\s*)?\p{L}[\p{L} .'’]*?)\s*(\d{1,3})$/u.exec(tidyTyped(q));
  if (!m) return null;
  return `${m[1]!.trim()} ${m[2]}:${WHOLE_CHAPTER}`;
}

/** A passage's verses, each as a passage of its own — the chapter laid out
 * to pick from, in order. */
export function versesOf(passage: ScripturePassage): ScripturePassage[] {
  return passage.verses.map((v) => ({ ...passage, reference: v.ref, verses: [v], text: v.text }));
}
