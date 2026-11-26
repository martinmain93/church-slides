/**
 * Scripture and text items in a service (`docs/done/SERVICE_ITEMS.md`).
 *
 * A setlist item is a song, a passage, or one-off text. Everything that shows
 * a service — the workspace grid, the operator view, the projector, the band
 * screens — was built for songs, so a passage or a text item is presented to
 * it *as* a song: `withServiceItems` gives each one a song-shaped `song`,
 * whose id is the item's own id (never a library song's) and whose slides
 * come from here. Code that must tell them apart — the AI, the library,
 * charts — asks `isSongItem`.
 *
 * - A **scripture** item holds its reference, translation and the verse text
 *   copied when it was added, so it shows offline and survives the Bible
 *   being removed. The slides are cut from the verses here; an operator's
 *   edits go in `slides_override`, exactly like a song edited for one service.
 * - A **text** item's slides live only in `slides_override`; its content is
 *   just the title the running order shows.
 */

import { loadDisplayLook, SIZE_SCALE, type DisplaySize } from "@/lib/displayLook";
import { tokenize } from "@/lib/parseSong";
import { parseSlidesOverride } from "@/lib/slideSequence";
import {
  bodyFromSlides,
  isSongItem,
  itemKind,
  itemTitle,
  scriptureContent,
  textSlides,
  type ItemKind,
  type ScriptureContent,
} from "@/lib/serviceItemContent";
import type { SetlistDetail, SetlistItemWithSong, SongRow } from "@/lib/tauri";
import type { ScripturePassage, ScriptureVerse } from "@/types/generated";

export {
  bodyFromSlides,
  isSongItem,
  itemKind,
  readingBody,
  readingSlides,
  scriptureContent,
  textContent,
  textSlides,
  type ItemKind,
  type ScriptureContent,
  type TextContent,
} from "@/lib/serviceItemContent";

/** What the running order calls an item. */
export function itemLabel(item: SetlistItemWithSong): string {
  return itemTitle(item, item.song);
}

/** "5 songs", or "4 songs · 2 passages · 1 text" once there is more than songs. */
export function itemCountLabel(items: Pick<SetlistItemWithSong, "kind">[]): string {
  const count = (k: ItemKind) => items.filter((i) => itemKind(i) === k).length;
  const n = (k: number, one: string, many = `${one}s`) => `${k} ${k === 1 ? one : many}`;
  const parts = [n(count("song"), "song")];
  if (count("scripture") > 0) parts.push(n(count("scripture"), "passage"));
  if (count("text") > 0) parts.push(n(count("text"), "text", "texts"));
  return parts.join(" · ");
}

/** Whether a scripture item still needs its verse text looked up. */
export function needsVerses(item: SetlistItemWithSong): boolean {
  const c = scriptureContent(item);
  return c !== null && !c.own_text && !(c.verses && c.verses.length > 0);
}

// --- Content for new items ---------------------------------------------------

export function scriptureFromPassage(p: ScripturePassage): ScriptureContent {
  return {
    reference: p.reference,
    translation: p.translation,
    ...(p.abbreviation ? { abbreviation: p.abbreviation } : {}),
    verses: p.verses,
  };
}

// --- Cutting a passage into slides -------------------------------------------

/** How much scripture goes on a slide at the medium lyric size, with its
 * reference line. Measured on the stage's own layout: a 1280x720 projector
 * is the tightest common screen, and there 230 characters fill it, 200 fill
 * 88%, 160 about 75% (1080p and 4:3 have more room). A larger lyric size
 * fits less, in proportion to its area. */
const CHARS_PER_SLIDE_AT_M = 160;

export function charsPerSlide(size: DisplaySize): number {
  const k = SIZE_SCALE[size];
  return Math.round(CHARS_PER_SLIDE_AT_M / (k * k));
}

const SUPERSCRIPT = "⁰¹²³⁴⁵⁶⁷⁸⁹";

function superscript(n: string): string {
  return n.replace(/\d/g, (d) => SUPERSCRIPT[Number(d)]!);
}

/** "John 3:16" -> "16". */
function verseNumber(ref: string): string {
  const m = /:(\d+)\s*$/.exec(ref);
  return m ? m[1]! : "";
}

/** The reference a slide's verses span: "John 3:16" or "John 3:16–18". */
function spanReference(first: ScriptureVerse, last: ScriptureVerse): string {
  if (first.ref === last.ref) return first.ref;
  return `${first.ref}–${verseNumber(last.ref)}`;
}

/**
 * Split text longer than `budget` into as few parts as fit, of about equal
 * length, each ending after punctuation where one falls near the cut — so a
 * long verse becomes two even halves rather than a full slide and a stray
 * "with him." on the next.
 */
function splitLong(text: string, budget: number): string[] {
  if (text.length <= budget) return [text];
  const words = text.split(/\s+/).filter(Boolean);
  const parts = Math.ceil(text.length / budget);
  const target = text.length / parts;
  const out: string[] = [];
  let cur: string[] = [];
  let len = 0;
  for (let i = 0; i < words.length; i++) {
    const word = words[i]!;
    cur.push(word);
    len += word.length + 1;
    const left = words.length - i - 1;
    if (left === 0 || out.length === parts - 1) continue;
    const pause = /[.!?;:,]["'”’)]*$/.test(word);
    if ((len >= target * 0.8 && pause) || len >= target * 1.15) {
      out.push(cur.join(" "));
      cur = [];
      len = 0;
    }
  }
  if (cur.length) out.push(cur.join(" "));
  return out;
}

function footer(reference: string, abbreviation: string | undefined): string {
  return abbreviation ? `${reference} · ${abbreviation}` : reference;
}

/**
 * A passage as slides: verses kept whole where they fit, each slide ending
 * with the reference it shows (so the room always knows where it is), the
 * verse numbers small. A verse too long for one slide is split at its
 * sentences.
 */
export function scriptureSlides(content: ScriptureContent, budget: number): string[] {
  if (content.own_text?.trim()) return ownTextSlides(content, budget);
  const verses = content.verses ?? [];
  if (verses.length === 0) {
    return [`${content.reference}\n(The verse text isn't available — check the Bible is installed in Settings → Scripture.)`];
  }
  const slides: string[] = [];
  let group: { verse: ScriptureVerse; text: string }[] = [];
  let size = 0;
  const flush = () => {
    if (group.length === 0) return;
    const body = group.map((g) => g.text).join(" ");
    const ref = spanReference(group[0]!.verse, group[group.length - 1]!.verse);
    slides.push(`${body}\n${footer(ref, content.abbreviation)}`);
    group = [];
    size = 0;
  };
  for (const verse of verses) {
    const num = superscript(verseNumber(verse.ref));
    const text = verse.text.trim();
    const parts = splitLong(text, budget);
    parts.forEach((part, i) => {
      const shown = i === 0 ? `${num}${part}` : part;
      if (group.length > 0 && size + shown.length > budget) flush();
      group.push({ verse, text: shown });
      size += shown.length + 1;
      if (parts.length > 1 && i < parts.length - 1) flush();
    });
  }
  flush();
  return slides;
}

function ownTextSlides(content: ScriptureContent, budget: number): string[] {
  const text = content.own_text!.replace(/\r\n?/g, "\n").trim();
  // Blank lines are the church's own slide breaks; otherwise cut by length.
  const chunks = /\n\s*\n/.test(text)
    ? text.split(/\n\s*\n/).map((c) => c.trim()).filter(Boolean)
    : splitLong(text.replace(/\s*\n\s*/g, " "), budget);
  const foot = footer(content.reference, content.abbreviation);
  const slides = chunks.map((c) => `${c}\n${foot}`);
  if (content.credit?.trim() && slides.length > 0) {
    slides[slides.length - 1] += `\n${content.credit.trim()}`;
  }
  return slides;
}

// --- Read-along (docs/done/SERVICE_ITEMS.md phase 2) ------------------------------

/** Item kinds as the AI should treat them: with read-along on, a passage is
 * followed like a song; text items still aren't. */
export function aiKinds(kinds: readonly ItemKind[], readAlong: boolean): ItemKind[] {
  return kinds.map((k) => (readAlong && k === "scripture" ? "song" : k));
}

const SUPERSCRIPT_DIGITS = /[⁰¹²³⁴⁵⁶⁷⁸⁹]+/g;

/** The reference line `scriptureSlides` puts under a passage's words:
 * "John 3:16–17 · WEB", "1 John 4:8", "Psalm 23:1–6". */
const REFERENCE_LINE = /^\s*(?:[1-4]\s*)?\p{L}[\p{L} .'’]*\s\d+:\d+(?:\s*[–-]\s*\d+)?(?:\s·\s.+)?\s*$/u;

/**
 * What is said aloud of a passage slide: its words, without the small verse
 * numbers and without the reference line (and the copyright line after it)
 * — none of which a reader reads. Tokenised like a song's slides.
 */
export function spokenWords(lyrics: string): string[] {
  const lines = lyrics.split("\n");
  const footer = lines.findIndex((line) => REFERENCE_LINE.test(line));
  const said = footer >= 0 ? lines.slice(0, footer) : lines;
  return tokenize(said.join("\n").replace(SUPERSCRIPT_DIGITS, " "));
}

/**
 * A passage's slides as the scroll display lays them out: without the
 * reference (and copyright) lines, so the column reads straight on and its
 * words are the ones the engine follows (`spokenWords`). Anything else is
 * returned as it is.
 */
export function flowSlides<S extends { lyrics: string; words: string[] }>(
  kind: ItemKind | undefined,
  slides: S[],
): S[] {
  if (kind !== "scripture") return slides;
  return slides.map((s) => {
    const lines = s.lyrics.split("\n");
    const footer = lines.findIndex((line) => REFERENCE_LINE.test(line));
    const lyrics = (footer >= 0 ? lines.slice(0, footer) : lines).join("\n");
    return { ...s, lyrics, words: s.words.length ? spokenWords(s.lyrics) : s.words };
  });
}

// --- The AI while a passage or text is up ------------------------------------

export interface Position {
  itemIdx: number;
  slideIdx: number;
}

/**
 * Where to tell the AI the screen is. A song is itself. A passage or text
 * item isn't something the AI follows yet, so it is told the start of the
 * next song instead — waiting there, it can't pull the screen back into the
 * song before the reading, and it picks up when the band starts. With no
 * song after, the last song before is used; with no songs at all, nothing.
 */
export function aiAnchor(kinds: readonly ItemKind[], pos: Position): Position | null {
  if (kinds[pos.itemIdx] === "song" || kinds[pos.itemIdx] === undefined) return pos;
  for (let i = pos.itemIdx + 1; i < kinds.length; i++) {
    if (kinds[i] === "song") return { itemIdx: i, slideIdx: 0 };
  }
  for (let i = pos.itemIdx - 1; i >= 0; i--) {
    if (kinds[i] === "song") return { itemIdx: i, slideIdx: 0 };
  }
  return null;
}

/**
 * Whether an AI move may take the screen off what's showing. Over a passage
 * or text item only a song getting under way may: a lyric slide of a song
 * (not its leading blank). Spoken requests are a person asking, and always
 * apply — they don't come through here.
 */
export function aiMayLeave(kinds: readonly ItemKind[], showing: number, target: Position): boolean {
  if (kinds[showing] === "song" || kinds[showing] === undefined) return true;
  return kinds[target.itemIdx] === "song" && target.slideIdx >= 1;
}

// --- Presenting items as songs -----------------------------------------------

/** The song-shaped stand-in a scripture or text item is presented as. */
export function itemSong(item: SetlistItemWithSong, budget: number): SongRow {
  const scripture = scriptureContent(item);
  const body = scripture ? bodyFromSlides(scriptureSlides(scripture, budget)) : "";
  return {
    id: item.id,
    title: itemLabel(item),
    body_text: body,
    ccli: null,
    author: null,
    copyright: scripture?.credit ?? null,
    default_tempo_wps: null,
    slide_order: null,
    created_at: 0,
    updated_at: 0,
  };
}

/**
 * The setlist with every scripture and text item given its song-shaped
 * stand-in. Applied once, where a setlist is read (`getSetlist`), so no
 * screen downstream needs to know items aren't all songs to show them.
 */
export function withServiceItems(detail: SetlistDetail, size: DisplaySize = loadDisplayLook().size): SetlistDetail {
  const budget = charsPerSlide(size);
  let changed = false;
  const items = detail.items.map((item) => {
    if (isSongItem(item)) return item;
    changed = true;
    const song = itemSong(item, budget);
    // A text item with no slides yet still shows one empty slide to edit.
    const override =
      itemKind(item) === "text" && !parseSlidesOverride(item.slides_override)?.length
        ? textSlides([])
        : item.slides_override;
    return { ...item, song, slides_override: override };
  });
  return changed ? { ...detail, items } : detail;
}
