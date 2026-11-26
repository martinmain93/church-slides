/**
 * A surprising AI jump to another song, held for the band leader to confirm.
 *
 * With nobody at the laptop (single-screen view) and a leader's screen
 * unlocked, an AI move to a different song waits `JUMP_HOLD_MS`. The leader's
 * screen shows "Going to <song>" with the words that set it off, and "Go now"
 * or "Stop". When the time runs out, it goes. The ordinary move — the next
 * song in the set, from the last slide of this one — never waits.
 *
 * The service never stops: if the leader's screen goes away mid-countdown,
 * the jump happens at once, as it would have without this.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export const JUMP_HOLD_MS = 3000;
/** How many heard words the leader sees. */
export const JUMP_HOLD_WORDS = 6;

/** What the leader's screen is told. Serialisable. */
export interface JumpHoldNotice {
  /** Changes for every new hold, so a stale "Stop" can't cancel a later one. */
  id: number;
  title: string;
  /** A few of the words heard just before the decision. */
  words: string;
  durationMs: number;
}

export interface JumpHoldRequest {
  /** Same key = the same destination song; a repeat updates it in place. */
  key: string;
  title: string;
  words: string;
  /** Make the jump. */
  go: () => void;
  /** Called when the leader says no: put the AI back where the screen is. */
  stop: () => void;
}

export interface JumpHoldApi {
  /** Shown on the leader's screen while a jump waits; null otherwise. */
  notice: JumpHoldNotice | null;
  /** Whether a jump would wait now (a leader's screen is unlocked). */
  active: () => boolean;
  /** Hold a jump. Returns false when it should just happen. */
  offer: (req: JumpHoldRequest) => boolean;
  /** The leader's answer, for the hold with this id. */
  answer: (id: number, go: boolean) => void;
  /** Forget a waiting jump without making it (something else moved the screen). */
  drop: () => void;
  /** How many leader screens are unlocked. Dropping to none lets a waiting jump go. */
  setLeaders: (count: number) => void;
}

/**
 * Whether an AI move from `from` to `toItemIdx` is a jump to check with the
 * leader. Staying in the song isn't; nor is the next song once the current
 * one is on its last slide.
 */
export function isSurprisingSongJump(
  from: { itemIdx: number; slideIdx: number },
  toItemIdx: number,
  slideCount: number,
): boolean {
  if (toItemIdx === from.itemIdx) return false;
  const atEnd = from.slideIdx >= slideCount - 1;
  return !(toItemIdx === from.itemIdx + 1 && atEnd);
}

/** The tail of what was heard, as one short line. */
export function heardLine(words: readonly string[], n = JUMP_HOLD_WORDS): string {
  return words
    .slice(-n)
    .map((w) => w.trim())
    .filter(Boolean)
    .join(" ");
}

export function useJumpHold(holdMs = JUMP_HOLD_MS): JumpHoldApi {
  const [notice, setNotice] = useState<JumpHoldNotice | null>(null);
  const leaders = useRef(0);
  const pending = useRef<(JumpHoldRequest & { id: number }) | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nextId = useRef(1);

  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const held = pending.current;
    pending.current = null;
    setNotice(null);
    return held;
  }, []);

  const finish = useCallback(
    (go: boolean) => {
      const held = clear();
      if (!held) return;
      if (go) held.go();
      else held.stop();
    },
    [clear],
  );

  const active = useCallback(() => leaders.current > 0, []);

  const offer = useCallback(
    (req: JumpHoldRequest) => {
      if (leaders.current <= 0) return false;
      const held = pending.current;
      if (held && held.key === req.key) {
        // The AI is still sure, and maybe a slide further in: go there when it goes.
        pending.current = { ...req, id: held.id };
        return true;
      }
      // A different song (or the first): a fresh countdown for this one.
      clear();
      const id = nextId.current++;
      pending.current = { ...req, id };
      setNotice({ id, title: req.title, words: req.words, durationMs: holdMs });
      timer.current = setTimeout(() => finish(true), holdMs);
      return true;
    },
    [clear, finish, holdMs],
  );

  const answer = useCallback(
    (id: number, go: boolean) => {
      if (pending.current?.id !== id) return;
      finish(go);
    },
    [finish],
  );

  const setLeaders = useCallback(
    (count: number) => {
      leaders.current = count;
      if (count <= 0 && pending.current) finish(true);
    },
    [finish],
  );

  const drop = useCallback(() => {
    clear();
  }, [clear]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return useMemo(
    () => ({ notice, active, offer, answer, drop, setLeaders }),
    [notice, active, offer, answer, drop, setLeaders],
  );
}
