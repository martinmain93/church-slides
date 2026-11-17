/**
 * Keyboard moves over a setlist's slide grid, shared by the prep and live
 * views so both answer the arrow keys and Enter the same way.
 */

/** Typing in a field: the key belongs to the field, not the slide grid. */
export function isTypingTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable === true)
  );
}

export interface SlidePos {
  itemId: string;
  slideIdx: number;
}

/**
 * Where an arrow key goes. Right/Left step one slide and cross into the
 * next or previous song; Down jumps to the next song's first slide; Up goes
 * to this song's first slide, or the previous song's when already there.
 * Null when the key isn't an arrow or there's nowhere to go.
 */
export function slideAfterArrow(
  key: string,
  songs: { id: string; slideCount: number }[],
  cur: SlidePos,
): SlidePos | null {
  if (songs.length === 0) return null;
  const i = Math.max(0, songs.findIndex((song) => song.id === cur.itemId));
  const song = songs[i]!;
  const prev = songs[i - 1];
  const next = songs[i + 1];
  switch (key) {
    case "ArrowRight":
      if (cur.slideIdx + 1 < song.slideCount) return { itemId: song.id, slideIdx: cur.slideIdx + 1 };
      return next ? { itemId: next.id, slideIdx: 0 } : null;
    case "ArrowLeft":
      if (cur.slideIdx > 0) return { itemId: song.id, slideIdx: cur.slideIdx - 1 };
      return prev ? { itemId: prev.id, slideIdx: Math.max(0, prev.slideCount - 1) } : null;
    case "ArrowDown":
      return next ? { itemId: next.id, slideIdx: 0 } : null;
    case "ArrowUp":
      if (cur.slideIdx > 0) return { itemId: song.id, slideIdx: 0 };
      return prev ? { itemId: prev.id, slideIdx: 0 } : null;
    default:
      return null;
  }
}
