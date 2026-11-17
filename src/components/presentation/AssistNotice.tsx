/**
 * AI assist's bottom notice: "Sounds like they're singing X" with Go, ×, and
 * "Something else" (other places the band might be).
 *
 * Shown only for a "place" suggestion whose tile isn't visible in the slide
 * grid — when it is, the tile's yellow pulse says it better and the notice
 * would only cover slides. A "next" suggestion never gets a notice.
 *
 * It must not get in the operator's way:
 *  - it never takes keyboard focus (buttons blur on click), so arrows and B
 *    keep driving the service;
 *  - while the pointer is over it, or "Something else" is open, it freezes:
 *    a suggestion that moves or clears doesn't reorder or vanish under a
 *    reaching hand. It catches up when the pointer leaves;
 *  - pointing at a candidate lights its tile, when that tile is in view.
 *
 * See docs/done/AI_ASSISTED_MODE.md.
 */

import { useEffect, useState, type RefObject } from "react";
import { ChevronDown, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AssistSuggestion } from "@/lib/assist";
import { cn } from "@/lib/utils";

export interface PlaceDescription {
  title: string;
  section: string;
  firstLine: string;
}

/** The tile for a slide in the operator grid. */
export function slideTile(root: HTMLElement | null, itemIdx: number, slideIdx: number) {
  return root?.querySelector<HTMLElement>(`[data-slide-key="${itemIdx}:${slideIdx}"]`) ?? null;
}

/** Whether a slide's tile is (mostly) visible in the scrolling grid. */
function useTileVisible(
  rootRef: RefObject<HTMLElement | null>,
  target: { itemIdx: number; slideIdx: number } | null,
): boolean {
  const [visible, setVisible] = useState(false);
  const itemIdx = target?.itemIdx;
  const slideIdx = target?.slideIdx;
  useEffect(() => {
    setVisible(false);
    if (itemIdx === undefined || slideIdx === undefined) return;
    const el = slideTile(rootRef.current, itemIdx, slideIdx);
    if (!el || typeof IntersectionObserver === "undefined") return;
    // The ratio, not `isIntersecting`: that's true while any sliver shows,
    // and the observer only reports crossings of these thresholds — so a tile
    // scrolled away through 0.6 would stay "visible" for good.
    const io = new IntersectionObserver(
      ([entry]) => setVisible((entry?.intersectionRatio ?? 0) >= VISIBLE_RATIO),
      { root: rootRef.current, threshold: [0, VISIBLE_RATIO, 1] },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [rootRef, itemIdx, slideIdx]);
  return visible;
}

const LIT = ["ring-2", "ring-yellow-300"];
/** How much of a tile must show for the pulse alone to be enough. */
const VISIBLE_RATIO = 0.6;

export function AssistNotice({
  suggestion,
  gridRef,
  describe,
  onGo,
  onDismiss,
}: {
  suggestion: AssistSuggestion | null;
  /** The scrolling slide grid, to tell whether the tile is in view. */
  gridRef: RefObject<HTMLElement | null>;
  describe: (itemIdx: number, slideIdx: number) => PlaceDescription | null;
  onGo: (itemIdx: number, slideIdx: number) => void;
  onDismiss: (id: number) => void;
}) {
  const [held, setHeld] = useState(false);
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState<AssistSuggestion | null>(suggestion);
  const frozen = held || open;
  useEffect(() => {
    if (!frozen) setShown(suggestion);
  }, [suggestion, frozen]);
  // A fresh suggestion starts with the list closed.
  const shownId = shown?.id;
  useEffect(() => setOpen(false), [shownId]);

  const tileVisible = useTileVisible(gridRef, shown?.tier === "place" ? shown : null);
  const here = shown ? describe(shown.itemIdx, shown.slideIdx) : null;
  if (!shown || shown.tier !== "place" || !here || (tileVisible && !frozen)) return null;

  const alternatives = shown.alternatives
    .map((a) => ({ ...a, place: describe(a.itemIdx, a.slideIdx) }))
    .filter((a) => a.place !== null);

  const light = (itemIdx: number, slideIdx: number, on: boolean) => {
    const el = slideTile(gridRef.current, itemIdx, slideIdx);
    if (on) el?.classList.add(...LIT);
    else el?.classList.remove(...LIT);
  };
  const act = (e: React.MouseEvent<HTMLButtonElement>, run: () => void) => {
    // Hand focus back: a focused button would swallow the arrow keys.
    e.currentTarget.blur();
    setHeld(false);
    setOpen(false);
    run();
  };

  return (
    <div
      className="pointer-events-none absolute inset-x-0 bottom-4 z-30 flex justify-center px-6"
      data-testid="assist-notice"
    >
      <div
        className="pointer-events-auto w-full max-w-xl rounded-lg border border-yellow-400/50 bg-background/95 p-3 text-sm shadow-2xl backdrop-blur"
        onMouseEnter={() => setHeld(true)}
        onMouseLeave={() => setHeld(false)}
        role="status"
        aria-live="polite"
      >
        <div className="flex items-start gap-3">
          <span className="mt-1.5 size-2 shrink-0 rounded-full bg-yellow-300" aria-hidden />
          <div className="min-w-0 flex-1">
            <div>
              Sounds like they&rsquo;re singing <span className="font-semibold">{here.title}</span>
              {here.section && <span className="text-muted-foreground">, {here.section}</span>}
            </div>
            {here.firstLine && (
              <div className="truncate text-xs italic text-muted-foreground">
                &ldquo;{here.firstLine}&hellip;&rdquo;
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <Button
              size="sm"
              className="h-8 bg-yellow-400 text-black hover:bg-yellow-300"
              onClick={(e) => act(e, () => onGo(shown.itemIdx, shown.slideIdx))}
            >
              Go
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8"
              title="The screen is right — dismiss"
              aria-label="Dismiss suggestion"
              onClick={(e) => act(e, () => onDismiss(shown.id))}
            >
              <X className="size-4" />
            </Button>
            {alternatives.length > 0 && (
              <Button
                size="sm"
                variant="outline"
                className="h-8 gap-1"
                aria-expanded={open}
                onClick={(e) => {
                  e.currentTarget.blur();
                  setOpen((v) => !v);
                }}
              >
                Something else
                <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
              </Button>
            )}
          </div>
        </div>
        {open && alternatives.length > 0 && (
          <div className="mt-2 border-t border-border pt-2">
            <ul className="flex flex-col gap-1">
              {alternatives.map((a) => (
                <li
                  key={`${a.itemIdx}:${a.slideIdx}`}
                  className="flex items-center gap-2 rounded-md px-2 py-1 hover:bg-accent/50"
                  onMouseEnter={() => light(a.itemIdx, a.slideIdx, true)}
                  onMouseLeave={() => light(a.itemIdx, a.slideIdx, false)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate">
                      <span className="font-medium">{a.place!.title}</span>
                      {a.place!.section && (
                        <span className="text-muted-foreground">, {a.place!.section}</span>
                      )}
                    </div>
                    {a.place!.firstLine && (
                      <div className="truncate text-xs italic text-muted-foreground">
                        &ldquo;{a.place!.firstLine}&hellip;&rdquo;
                      </div>
                    )}
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7"
                    onClick={(e) =>
                      act(e, () => {
                        light(a.itemIdx, a.slideIdx, false);
                        onGo(a.itemIdx, a.slideIdx);
                      })
                    }
                  >
                    Go
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
