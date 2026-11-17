/**
 * Resolve a song's `body_text` (+ optional default slide order) into the
 * concrete, ordered slide sequence that actually plays.
 *
 * A song's body lists each section once, in document order. A worship
 * arrangement, though, repeats sections (chorus after every verse). The
 * `slide_order` field captures that arrangement as a compact section-reference
 * string like `V1, V2, C, V3, C, V4, C, B, C`. This module expands that string
 * against the parsed slides — repeated section references produce repeated
 * slides.
 *
 * Both the live presentation (`buildContext` in `presentation/state.ts`) and
 * the AI matcher payload (`buildSetlistContent` in `setlistContent.ts`) resolve
 * through here, so the slide indices the operator sees and the indices the
 * matcher scores against stay identical.
 *
 * When `slide_order` is empty/missing, the output is byte-identical to the old
 * behaviour (each section once, document order) — so existing songs are
 * unaffected and no data backfill is needed.
 */

import {
  parseSong,
  parseSectionTag,
  tokenize,
  type ParsedSection,
  type ParsedSlide,
  type SectionType,
} from "@/lib/parseSong";

/** A slide in the resolved playback sequence. */
export interface ResolvedSlide extends ParsedSlide {
  /** Id of the source slide it was expanded from (stable across repeats). */
  sourceId: string;
}

/**
 * A single edited slide in a per-setlist override (the JSON stored in
 * `setlist_items.slides_override`). A flat, ordered, fully-resolved list —
 * not section shorthand — so arbitrary duplicate/reorder/edit is expressible.
 */
export interface OverrideSlide {
  section: ParsedSection;
  lyrics: string;
}

const SECTION_ABBREV: Record<SectionType, string> = {
  verse: "V",
  chorus: "C",
  bridge: "B",
  "pre-chorus": "P",
  tag: "T",
  intro: "I",
  outro: "O",
  instrumental: "Inst",
  other: "Other",
};

function sectionKey(s: ParsedSection): string {
  return `${s.type}:${s.number}`;
}

/**
 * Sentinel section for the synthetic "song start" blank slide. `number: 0`
 * never collides with a real parsed section (those start at 1), so
 * `isBlankSlide` can recognise it unambiguously.
 */
const BLANK_SECTION: ParsedSection = { type: "other", number: 0 };

/** True for the synthetic leading blank slide (no lyrics, song-start placeholder). */
export function isBlankSlide(slide: { section: ParsedSection }): boolean {
  return slide.section.type === "other" && slide.section.number === 0;
}

/** The synthetic blank slide that leads every song in presentation contexts. */
function blankSlide(): ResolvedSlide {
  return {
    id: "blank",
    sourceId: "blank",
    section: BLANK_SECTION,
    order_in_song: 0,
    order_in_section: 0,
    lyrics: "",
    words: [],
  };
}

/** Options controlling how a slide sequence is resolved for playback. */
export interface ResolveOptions {
  /**
   * Prepend a synthetic blank slide as slide 0 of the song (the audience sees
   * black until the operator advances into the first lyric slide). Used by
   * presentation + AI-matcher contexts so their slide indices stay aligned;
   * editors leave it off and operate on real, storable slides only.
   */
  leadingBlank?: boolean;
}

/**
 * Parse a slide-order spec (`"V1, V2, C, V3, C"`) into section references.
 * Tokens are split on commas and whitespace; each is run through the same
 * `parseSectionTag` the body parser uses, so `V`/`C`/`B`/… aliases and trailing
 * numbers resolve identically. Empty tokens are ignored.
 */
export function parseSlideOrder(spec: string | null | undefined): ParsedSection[] {
  if (!spec) return [];
  return spec
    .split(/[,\s]+/)
    .map((t) => t.trim())
    .filter((t) => t.length > 0)
    .map((t) => parseSectionTag(t));
}

/**
 * Render a list of sections into the compact spec string. Used to show the
 * derived default ("auto") order in the editor. The number is dropped only when
 * it's 1 *and* there's a single section of that type — so multiple verses stay
 * `V1, V2, …` while a lone chorus is just `C`.
 */
export function formatSlideOrder(sections: ParsedSection[]): string {
  const typeCounts = new Map<SectionType, number>();
  for (const s of sections) {
    typeCounts.set(s.type, (typeCounts.get(s.type) ?? 0) + 1);
  }
  return sections
    .map((s) => {
      const abbrev = SECTION_ABBREV[s.type];
      const omitNumber = s.number === 1 && (typeCounts.get(s.type) ?? 0) <= 1;
      return omitNumber ? abbrev : `${abbrev}${s.number}`;
    })
    .join(", ");
}

/**
 * Expand `body_text` + an optional slide order into the concrete play sequence.
 *
 * - Empty/missing order → each section once in document order (legacy behaviour).
 * - Order tokens that reference a section with no slides (typos) are skipped.
 * - Each resolved slide gets a unique `id` (`<sourceId>#<seqIndex>`) so repeated
 *   slides remain distinct React keys, plus a resequenced `order_in_song`.
 */
export function resolveSlideSequence(
  body: string,
  slideOrder?: string | null,
  override?: OverrideSlide[] | null,
  opts?: ResolveOptions,
): ResolvedSlide[] {
  const out = resolveBase(body, slideOrder, override);
  if (opts?.leadingBlank) {
    // Prepend the blank, then re-sequence `order_in_song` so the real slides
    // shift to 1, 2, … and indices line up with what the operator/audience see.
    const shifted = out.map((s, i) => ({ ...s, order_in_song: i + 1 }));
    return [blankSlide(), ...shifted];
  }
  return out;
}

function resolveBase(
  body: string,
  slideOrder?: string | null,
  override?: OverrideSlide[] | null,
): ResolvedSlide[] {
  // A per-setlist override is already a concrete, ordered slide list — use it
  // verbatim, deriving tokens the same way the body parser would.
  if (override && override.length > 0) {
    const counts = new Map<string, number>();
    return override.map((s, i) => {
      const key = sectionKey(s.section);
      const order_in_section = counts.get(key) ?? 0;
      counts.set(key, order_in_section + 1);
      return {
        id: `override_${i}`,
        sourceId: `override_${i}`,
        section: s.section,
        order_in_song: i,
        order_in_section,
        lyrics: s.lyrics,
        words: tokenize(s.lyrics),
      };
    });
  }

  const { slides } = parseSong(body);

  // Group parsed slides by section key, preserving their in-section order.
  const bySection = new Map<string, ParsedSlide[]>();
  for (const slide of slides) {
    const key = sectionKey(slide.section);
    const bucket = bySection.get(key);
    if (bucket) bucket.push(slide);
    else bySection.set(key, [slide]);
  }

  const order = parseSlideOrder(slideOrder);
  // No order given: each block once, as written. Walking `section_order`
  // through the buckets would emit a section written twice (two [Chorus]
  // blocks) twice over — every chorus slide at each of the two places.
  if (order.length === 0) {
    return slides.map((slide, seqIndex) => ({
      ...slide,
      id: `${slide.id}#${seqIndex}`,
      sourceId: slide.id,
      order_in_song: seqIndex,
    }));
  }

  const out: ResolvedSlide[] = [];
  for (const ref of order) {
    const bucket = bySection.get(sectionKey(ref));
    if (!bucket) continue; // unknown / typo'd reference — skip gracefully
    for (const slide of bucket) {
      const seqIndex = out.length;
      out.push({
        ...slide,
        id: `${slide.id}#${seqIndex}`,
        sourceId: slide.id,
        order_in_song: seqIndex,
      });
    }
  }
  return out;
}

/**
 * Parse the JSON stored in `setlist_items.slides_override`. Returns null for
 * absent / empty / malformed input so callers fall back to the library song.
 */
export function parseSlidesOverride(json: string | null | undefined): OverrideSlide[] | null {
  if (!json) return null;
  try {
    const parsed = JSON.parse(json);
    if (!Array.isArray(parsed) || parsed.length === 0) return null;
    return parsed
      .filter(
        (s): s is OverrideSlide =>
          s &&
          typeof s.lyrics === "string" &&
          s.section &&
          typeof s.section.type === "string" &&
          typeof s.section.number === "number",
      )
      .map((s) => ({
        section: { type: s.section.type, number: s.section.number },
        lyrics: s.lyrics,
      }));
  } catch {
    return null;
  }
}

/**
 * The first few lines of a song's first verse (or, failing a verse, its first
 * slide), for search-result previews. Strips section headers and blank lines.
 */
export function firstVerseLines(body: string, maxLines = 2): string {
  const { slides } = parseSong(body);
  if (slides.length === 0) return "";
  const verse = slides.find((s) => s.section.type === "verse") ?? slides[0]!;
  return verse.lyrics
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0)
    .slice(0, maxLines)
    .join(" / ");
}

/** Serialize an edited slide list for storage in `slides_override`. */
export function serializeSlidesOverride(slides: OverrideSlide[]): string {
  return JSON.stringify(
    slides.map((s) => ({ section: s.section, lyrics: s.lyrics })),
  );
}

/**
 * Render a flat slide list back into `body_text` for "save to library".
 * Consecutive slides of the same section share one `[section]` header (each as
 * a blank-line-separated stanza); a section change emits a new header. The
 * result re-parses to the same slide list, so the library song matches the
 * setlist arrangement exactly. (slide_order is set to null by the caller —
 * any repeats are written literally as repeated stanzas.)
 */
export function slidesToBody(slides: OverrideSlide[]): string {
  const blocks: string[] = [];
  let prevKey: string | null = null;
  let current: string[] = [];

  const flush = () => {
    if (current.length > 0) blocks.push(current.join("\n\n"));
    current = [];
  };

  for (const slide of slides) {
    const key = sectionKey(slide.section);
    if (key !== prevKey) {
      flush();
      current.push(`[${slide.section.type} ${slide.section.number}]`);
      prevKey = key;
    }
    current.push(slide.lyrics);
  }
  flush();
  return blocks.join("\n\n") + "\n";
}
