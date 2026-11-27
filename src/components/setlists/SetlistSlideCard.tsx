/**
 * One slide in a setlist's grid — the same card before the service (select
 * and edit) and during it (click to put it on screen).
 *
 * A 16:9 thumbnail in the display's theme with its section label. Drag the
 * card itself to reorder (a click still selects: the drag needs a few pixels
 * of movement). Hovering shows edit and ⋯ in the corner; insert, duplicate
 * and delete live in the ⋯ menu (also a right click), so a click meant to
 * put a slide on screen mid-service can't land on Delete. A double click or
 * Enter on the selected card edits the words in place; Escape or clicking
 * away finishes. Where `deleteOnKey` is set, Delete or Backspace on the
 * selected card deletes it.
 */

import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Copy, MoreHorizontal, Pencil, Plus, Scissors, Trash2 } from "lucide-react";

import { stageThumbnailStyle } from "@/components/presentation/StageSurface";
import { Textarea, growToFit } from "@/components/ui/textarea";
import { type DisplayLook } from "@/lib/look";
import { sectionLabelClass, type SectionType } from "@/lib/parseSong";
import { isTypingTarget } from "@/lib/slideKeys";
import { cn } from "@/lib/utils";
import { canSplit, isCrowded, offerSplit } from "@/lib/slideSplit";
import { MiniSlide, useMiniStage } from "@/components/presentation/MiniSlide";

const ACTION =
  "flex size-6 items-center justify-center rounded-md border border-white/15 bg-zinc-900/90 text-zinc-100 shadow-sm outline-none transition-colors duration-150 focus-visible:ring-1 focus-visible:ring-white/40";

interface Props {
  /** The drag-and-drop id. */
  sortableId: string;
  look: DisplayLook;
  /** Empty for a passage or text item, which has no sections. */
  sectionLabel: string;
  sectionType: SectionType;
  lyrics: string;
  /** Shown in place of empty words. */
  emptyText: string;
  selected: boolean;
  /** Border and ring when not selected (the AI's suggestion glow live). */
  idleClassName?: string;
  editing: boolean;
  /** "Select slide" before the service; "Go to slide" during it. */
  selectLabel: string;
  onSelect: () => void;
  onStartEdit: () => void;
  /** Editing finished; `lyrics` is what the box held. */
  onStopEdit: (lyrics: string) => void;
  /** Every keystroke, for a view that saves as it goes. */
  onChangeLyrics?: (lyrics: string) => void;
  /** Add an empty slide straight after this one and start typing in it. */
  onInsertAfter: () => void;
  onDuplicate: () => void;
  /** Cut the slide in two near its middle; without it there's no split button. */
  onSplit?: () => void;
  /** Whether a split may cut one long line (not in a song with chords). */
  splitMidLine?: boolean;
  onDelete: () => void;
  /** The card grows while its words are being typed (prep, not the live grid). */
  growWhenSelected?: boolean;
  /** `data-*` attributes and the test id, which differ between the views. */
  data: Record<string, string | undefined>;
  /** "Delete" before the service, "Remove" during it. */
  deleteLabel?: string;
  /** Delete or Backspace on the selected card deletes it (prep only, not live). */
  deleteOnKey?: boolean;
}

export function SetlistSlideCard({
  sortableId,
  look,
  sectionLabel,
  sectionType,
  lyrics,
  emptyText,
  selected,
  idleClassName = "border-border hover:border-primary/50 hover:ring-1 hover:ring-primary/50",
  editing,
  selectLabel,
  onSelect,
  onStartEdit,
  onStopEdit,
  onChangeLyrics,
  onInsertAfter,
  onDuplicate,
  onSplit,
  splitMidLine = true,
  onDelete,
  growWhenSelected = false,
  data,
  deleteLabel = "Delete",
  deleteOnKey = false,
}: Props) {
  const [menu, setMenu] = useState(false);
  // A true-scale 16:9 picture of the screen; only the box being typed in grows.
  const expanded = growWhenSelected && editing;
  const mini = useMiniStage(look);
  const [overflows, setOverflows] = useState(false);
  // The whole card is the drag handle — except while typing in it, where a
  // drag selects text.
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: sortableId,
    disabled: editing,
  });
  const dndTransform = CSS.Transform.toString(transform);
  const style: CSSProperties = {
    ...(dndTransform ? { transform: dndTransform } : {}),
    ...(transition ? { transition } : {}),
    opacity: isDragging ? 0.6 : 1,
  };

  useEffect(() => {
    if (!menu) return;
    function onKeyDown(ev: KeyboardEvent) {
      if (ev.key === "Escape") setMenu(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [menu]);

  useEffect(() => {
    if (!selected || editing) return;
    function onKeyDown(ev: KeyboardEvent) {
      if (ev.key !== "Enter" || ev.metaKey || ev.ctrlKey || ev.altKey || ev.shiftKey) return;
      if (isTypingTarget(ev.target)) return;
      ev.preventDefault();
      onStartEdit();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selected, editing, onStartEdit]);

  useEffect(() => {
    if (!deleteOnKey || !selected || editing) return;
    function onKeyDown(ev: KeyboardEvent) {
      if (ev.key !== "Delete" && ev.key !== "Backspace") return;
      if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.shiftKey) return;
      if (isTypingTarget(ev.target)) return;
      ev.preventDefault();
      setMenu(false);
      onDelete();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleteOnKey, selected, editing, onDelete]);

  return (
    // The outer box moves when dragged and holds the menu; the inner one is
    // the clipped 16:9 picture, so the menu can hang outside it.
    <div
      ref={setNodeRef}
      style={style}
      {...data}
      {...(editing ? {} : listeners)}
      onContextMenu={(e) => {
        e.preventDefault();
        setMenu(true);
      }}
      className="group relative w-full scroll-mt-12 scroll-mb-4"
    >
      <div
        style={stageThumbnailStyle(look)}
        data-expanded={expanded ? "true" : undefined}
        className={`relative ${expanded ? "grid grid-cols-1" : "aspect-video"} w-full overflow-hidden rounded-md border shadow-xs transition-[border-color,box-shadow] duration-250 ease-out ${
          selected ? "border-primary ring-2 ring-primary" : idleClassName
        }`}
      >
        {expanded && <div aria-hidden className="col-start-1 row-start-1 aspect-video w-full" />}
        {sectionLabel && (
          <span
            className={`pointer-events-none absolute left-2.5 top-2 z-30 truncate rounded bg-black/65 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider ${sectionLabelClass(sectionType)}`}
          >
            {sectionLabel}
          </span>
        )}

        {editing ? (
          <div
            className={`${expanded ? "relative col-start-1 row-start-1 pb-7" : "absolute inset-0 pb-3"} flex items-center justify-center px-3 pt-7`}
          >
            <div ref={mini.ref} style={mini.container} className={expanded ? "w-full" : "h-full w-full"}>
            <Textarea
              autoFocus
              ref={expanded ? growToFit : undefined}
              defaultValue={lyrics}
              onChange={(e) => {
                onChangeLyrics?.(e.target.value);
                if (expanded) growToFit(e.target);
              }}
              onBlur={(e) => onStopEdit(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  e.currentTarget.blur();
                }
              }}
              className={`${expanded ? "min-h-0" : "h-full"} w-full resize-none rounded-sm border-none bg-transparent p-0 text-center font-bold leading-tight focus-visible:ring-0`}
              // The same size as the card shows it: editing doesn't jump.
              style={{ ...mini.text, maxWidth: undefined }}
            />
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={(e) => {
              e.currentTarget.blur();
              onSelect();
            }}
            onDoubleClick={onStartEdit}
            aria-label={selectLabel}
            className="absolute inset-0 cursor-pointer overflow-hidden text-center"
          >
            <MiniSlide lyrics={lyrics} look={look} empty={emptyText} onOverflow={setOverflows} />
          </button>
        )}

        {!editing && (
          <div
            // Hidden, the buttons take no taps: one stray tap mustn't open a menu or edit.
            className={`absolute right-1.5 top-1.5 z-30 flex items-center gap-1 transition-[opacity,visibility] duration-200 ease-out group-focus-within:visible group-focus-within:opacity-100 [@media(hover:hover)]:group-hover:visible [@media(hover:hover)]:group-hover:opacity-100 ${
              selected ? "visible opacity-100" : "invisible opacity-0"
            }`}
          >
            <ActionButton label="Edit slide text" className="hover:border-white/40 hover:bg-zinc-700" onClick={onStartEdit}>
              <Pencil className="size-3.5" />
            </ActionButton>
            {onSplit && (offerSplit(lyrics, { midLine: splitMidLine }) || (overflows && canSplit(lyrics, { midLine: splitMidLine }))) && (
              <ActionButton
                label="Split slide in two"
                className={
                  overflows || isCrowded(lyrics)
                    ? "border-amber-400/70 text-amber-300 hover:border-amber-400 hover:bg-amber-500 hover:text-zinc-950"
                    : "hover:border-white/40 hover:bg-zinc-700"
                }
                onClick={onSplit}
              >
                <Scissors className="size-3.5" />
              </ActionButton>
            )}
            <ActionButton label="More slide actions" className="hover:border-white/40 hover:bg-zinc-700" onClick={() => setMenu(true)}>
              <MoreHorizontal className="size-3.5" />
            </ActionButton>
          </div>
        )}
      </div>

      {menu && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setMenu(false)} />
          <div
            role="menu"
            className="absolute right-1.5 top-8 z-50 min-w-32 rounded-md border border-border bg-popover p-1 text-sm shadow-md"
          >
            <MenuItem
              icon={<Plus className="size-3.5" />}
              label="Insert slide"
              onClick={() => {
                setMenu(false);
                onInsertAfter();
              }}
            />
            <MenuItem
              icon={<Copy className="size-3.5" />}
              label="Duplicate"
              onClick={() => {
                setMenu(false);
                onDuplicate();
              }}
            />
            <MenuItem
              icon={<Trash2 className="size-3.5" />}
              label={deleteLabel}
              destructive
              onClick={() => {
                setMenu(false);
                onDelete();
              }}
            />
          </div>
        </>
      )}
    </div>
  );
}

function ActionButton({
  label,
  className,
  onClick,
  children,
}: {
  label: string;
  className: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      aria-label={label}
      title={label}
      className={cn(ACTION, className)}
    >
      {children}
    </button>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  destructive,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={`flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-accent ${
        destructive ? "text-destructive" : ""
      }`}
    >
      {icon}
      {label}
    </button>
  );
}
