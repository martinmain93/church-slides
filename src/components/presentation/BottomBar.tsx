/**
 * Auto-hide context bar — the only chrome on the single-screen presentation,
 * so the screen shows nothing but the lyrics until someone asks.
 *
 * Opens only when the mouse comes within 100 px of the bottom edge, and
 * stays open while the pointer is over it (so the AI pill in its toolbar can
 * be clicked). Closes 2 s after the pointer leaves both. Key presses and
 * song changes deliberately don't open it: the congregation is looking at
 * this screen.
 *
 * Both columns hold the whole service — every slide, every song — and
 * scroll, keeping the one on screen at the top so what's next is in view.
 */

import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";

import type { ParsedSlide } from "@/lib/parseSong";
import type { PresentationItem } from "@/lib/presentation/state";
import { isBlankSlide } from "@/lib/slideSequence";
import type { SongRow } from "@/lib/tauri";
import { cn } from "@/lib/utils";

interface Props {
  items: PresentationItem[];
  /** Where the service is: the song and slide on screen. */
  currentItemIdx: number;
  currentSlideIdx: number;
  /** Go to a slide: clicking one of the slides or songs. */
  onJump: (itemIdx: number, slideIdx: number) => void;
  /** A row above the slide/song columns — AI status, blank, exit. */
  toolbar?: ReactNode;
  /** Above the toolbar: something the operator should know (the AI's
   * problems), seen only when the bar is up. */
  notice?: ReactNode;
}

const HIDE_AFTER_MS = 2000;
const HOVER_ZONE_PX = 100;

export function BottomBar({
  items,
  currentItemIdx,
  currentSlideIdx,
  onJump,
  toolbar,
  notice,
}: Props) {
  const [shown, setShown] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const slidesRef = useRef<HTMLDivElement | null>(null);
  const songsRef = useRef<HTMLDivElement | null>(null);

  // Keep the current row at the top of each column (with one row of what
  // came before), so the next few are always in view. The bar only slides
  // out of sight when hidden, so this works before it opens too.
  useEffect(() => {
    for (const col of [slidesRef.current, songsRef.current]) {
      const row = col?.querySelector<HTMLElement>("[data-current]");
      if (!col || !row) continue;
      col.scrollTop = Math.max(0, row.offsetTop - row.offsetHeight);
    }
  }, [currentItemIdx, currentSlideIdx, items]);

  useEffect(() => {
    let hideTimer: ReturnType<typeof setTimeout> | null = null;

    function hold() {
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = null;
      setShown(true);
    }

    function release() {
      if (hideTimer) return;
      hideTimer = setTimeout(() => {
        hideTimer = null;
        setShown(false);
      }, HIDE_AFTER_MS);
    }

    function onMouseMove(ev: MouseEvent) {
      const overPanel =
        ev.target instanceof Node && !!panelRef.current?.contains(ev.target);
      if (overPanel || window.innerHeight - ev.clientY <= HOVER_ZONE_PX) hold();
      else release();
    }

    // Pointer left the window (e.g. off the bottom edge onto another screen).
    function onMouseOut(ev: MouseEvent) {
      if (!ev.relatedTarget) release();
    }

    window.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseout", onMouseOut);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseout", onMouseOut);
      if (hideTimer) clearTimeout(hideTimer);
    };
  }, []);

  return (
    <div
      data-testid="bottom-bar"
      data-shown={shown}
      className={cn(
        "pointer-events-none absolute inset-x-0 bottom-0 z-20",
        // Fade as well as slide: a popover opened from the toolbar (band
        // screens) sticks up further than the bar is tall, and sliding alone
        // left it floating over the lyrics.
        "transition-[transform,opacity] duration-200 ease-out",
        shown ? "translate-y-0 opacity-100" : "translate-y-full opacity-0",
      )}
    >
      <div
        ref={panelRef}
        className={cn(
          "mx-4 mb-4 rounded-lg border border-white/10 bg-black/70 p-4 text-sm text-white shadow-lg backdrop-blur",
          shown && "pointer-events-auto",
        )}
      >
        {notice}
        {toolbar && <div className="mb-3 flex items-center gap-2">{toolbar}</div>}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Column title="Slides" scrollRef={slidesRef}>
            {items.map((it, itemIdx) => (
              <Fragment key={it.itemId}>
                {/* Which song the slides below belong to, once they run on. */}
                <div className="truncate px-2 pt-1.5 text-[10px] font-medium text-white/40 first:pt-0">
                  {it.song.title}
                </div>
                {it.slides.map((slide, slideIdx) => {
                  const active = itemIdx === currentItemIdx && slideIdx === currentSlideIdx;
                  return (
                    <SlidePill
                      key={slideIdx}
                      slide={slide}
                      song={it.song}
                      active={active}
                      onClick={active ? undefined : () => onJump(itemIdx, slideIdx)}
                    />
                  );
                })}
              </Fragment>
            ))}
          </Column>
          <Column title="Songs" scrollRef={songsRef}>
            {items.map((it, itemIdx) => {
              const active = itemIdx === currentItemIdx;
              return (
                <SongPill
                  key={it.itemId}
                  song={it.song}
                  active={active}
                  onClick={active ? undefined : () => onJump(itemIdx, 0)}
                />
              );
            })}
          </Column>
        </div>
      </div>
    </div>
  );
}

function Column({
  title,
  scrollRef,
  children,
}: {
  title: string;
  scrollRef: React.Ref<HTMLDivElement>;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <div className="text-[10px] uppercase tracking-wider text-white/50">{title}</div>
      {/* `relative` so a row's offsetTop is measured from the column. */}
      <div ref={scrollRef} className="relative flex max-h-40 flex-col gap-1 overflow-y-auto pr-1">
        {children}
      </div>
    </div>
  );
}

/** A row in the drawer: the current one is a label, the rest jump on click. */
function Pill({
  active,
  onClick,
  children,
}: {
  active?: boolean;
  onClick?: () => void;
  children: ReactNode;
}) {
  const className = cn(
    "block w-full shrink-0 truncate rounded-md px-2 py-1 text-left text-xs",
    active ? "bg-white/15 text-white" : "text-white/70",
  );
  if (!onClick) {
    return (
      <div className={className} data-current={active ? "" : undefined}>
        {children}
      </div>
    );
  }
  return (
    <button
      type="button"
      className={cn(className, "transition-colors hover:bg-white/10 hover:text-white")}
      onClick={(e) => {
        // Give the arrow keys back to the service.
        e.currentTarget.blur();
        onClick();
      }}
    >
      {children}
    </button>
  );
}

function SlidePill({
  slide,
  song,
  active,
  onClick,
}: {
  slide: ParsedSlide | undefined;
  song: SongRow | undefined;
  active?: boolean;
  onClick?: () => void;
}) {
  if (!slide || !song) {
    return <div className="text-xs text-white/40">—</div>;
  }
  const blank = isBlankSlide(slide);
  return (
    <Pill active={active} onClick={onClick}>
      <span className="text-[10px] uppercase tracking-wider text-white/40">
        {blank ? "Blank" : sectionLabel(slide.section.type, slide.section.number)}
      </span>{" "}
      {blank ? (
        <span className="text-white/50">before {song.title}</span>
      ) : (
        <span>{firstLineOf(slide.lyrics)}</span>
      )}
    </Pill>
  );
}

function SongPill({
  song,
  active,
  onClick,
}: {
  song: SongRow | undefined;
  active?: boolean;
  onClick?: () => void;
}) {
  if (!song) return <div className="text-xs text-white/40">—</div>;
  return (
    <Pill active={active} onClick={onClick}>
      <span className="font-medium">{song.title}</span>
    </Pill>
  );
}

function sectionLabel(type: string, number: number): string {
  const t = type.replace("-", " ");
  if (type === "verse" || number > 1) return `${t} ${number}`;
  return t;
}

function firstLineOf(text: string): string {
  const idx = text.indexOf("\n");
  return idx < 0 ? text : text.slice(0, idx);
}
