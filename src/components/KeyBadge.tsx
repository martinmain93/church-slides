import { keyColor } from "@/lib/chords/keyColor";
import { cn } from "@/lib/utils";

/** A song's key as white text in a circle of that key's colour (see `keyColor`). */
export function KeyBadge({ songKey, className = "" }: { songKey: string; className?: string }) {
  return (
    <span
      title={`Key of ${songKey}`}
      data-testid="key-badge"
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold tracking-tight text-white",
        className,
      )}
      style={{ backgroundColor: keyColor(songKey) }}
    >
      {songKey}
    </span>
  );
}
