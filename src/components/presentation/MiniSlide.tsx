/**
 * A slide card's words, as a true miniature of the projector screen.
 *
 * `SlideStage` sizes the lyrics to the screen's width (5% of it, up to 5rem,
 * with 48px/64px margins) and cuts off whatever runs past the bottom. A card
 * drawn at the same proportions as a 1080p screen therefore runs out of room
 * exactly where the screen does — so when a card's words don't fit, they
 * won't fit in the room either, and the card says so ("Too long for the
 * screen") instead of a preview that looks fine while the room loses lines.
 */

import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { inkStyle } from "@/components/presentation/StageSurface";
import { useContainerUnits } from "@/lib/containerUnits";
import { SIZE_SCALE, type DisplayLook } from "@/lib/look";

// At 1920 wide: the text box is 1920 − 2×48 = 1824 wide; the font is
// min(5% of 1824, 80px) = 80px; the words wrap at 90% of the text box.
const SCREEN_W = 1920;
const FONT_CQW = (80 / SCREEN_W) * 100;
const PAD_X_CQW = (48 / SCREEN_W) * 100;
const PAD_Y_CQW = (64 / SCREEN_W) * 100;
const WRAP_CQW = ((0.9 * (SCREEN_W - 96)) / SCREEN_W) * 100;

/** The miniature's sizes for a look: spread `text` on the words and `pad`
 * on the box around them; `ref` goes on the card-wide element that holds
 * both (its width is the screen's). */
export function useMiniStage(look: DisplayLook) {
  const { ref, cqw } = useContainerUnits<HTMLDivElement>();
  const k = SIZE_SCALE[look.size];
  const container: CSSProperties = { containerType: "inline-size" };
  const pad: CSSProperties = { padding: `${cqw(PAD_Y_CQW)} ${cqw(PAD_X_CQW)}` };
  const text: CSSProperties = { ...inkStyle(look), fontSize: cqw(+(FONT_CQW * k).toFixed(3)), maxWidth: cqw(WRAP_CQW) };
  return { ref, container, pad, text };
}

/** Whether the words run past the bottom, rechecked as the card resizes. */
function useOverflows(box: React.RefObject<HTMLElement | null>, deps: unknown[]): boolean {
  const [over, setOver] = useState(false);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const check = () => setOver(el.scrollHeight > el.clientHeight + 1);
    check();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return over;
}

export function MiniSlide({
  lyrics,
  look,
  empty,
  onOverflow,
}: {
  lyrics: string;
  look: DisplayLook;
  /** Shown in place of empty words. */
  empty: ReactNode;
  /** Told whether the words fit, so the card can offer a split. */
  onOverflow?: (overflows: boolean) => void;
}) {
  const mini = useMiniStage(look);
  const box = useRef<HTMLDivElement | null>(null);
  const over = useOverflows(box, [lyrics, look.size, look.font]);
  const told = useRef<boolean | null>(null);
  useLayoutEffect(() => {
    if (told.current === over) return;
    told.current = over;
    onOverflow?.(over);
  }, [over, onOverflow]);

  return (
    <div ref={mini.ref} style={mini.container} className="absolute inset-0">
      <div
        ref={box}
        style={mini.pad}
        // Too long: start at the top, so it's the end that's cut, as on the screen
        // people would see the first lines and lose the last.
        className={`flex h-full w-full justify-center overflow-hidden ${over ? "items-start" : "items-center"}`}
      >
        <div className="whitespace-pre-wrap text-center font-bold leading-tight" style={mini.text} data-testid="mini-slide-words">
          {lyrics || <span className="font-sans text-xs font-normal italic opacity-50">{empty}</span>}
        </div>
      </div>
      {over && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex h-1/3 items-end justify-center bg-gradient-to-t from-black/85 to-transparent pb-1.5">
          <span className="rounded bg-amber-500/90 px-1.5 py-0.5 font-sans text-[10px] font-semibold text-zinc-950" data-testid="too-long">
            Too long for the screen
          </span>
        </div>
      )}
    </div>
  );
}
