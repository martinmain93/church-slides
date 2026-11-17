import { X } from "lucide-react";

import type { QueryPart } from "@/lib/smartSearch/parseQuery";
import type { SmartResult } from "@/lib/smartSearch/search";

/**
 * What the search read from the query, as chips that remove themselves from
 * the text — and a word when topic search is still getting ready or a
 * "not played" filter has no service history to go on.
 */
export function SearchUnderstood({
  result,
  onRemove,
  className = "px-6",
}: {
  result: SmartResult;
  onRemove: (part: QueryPart) => void;
  /** Horizontal padding, to line up with the list below it. */
  className?: string;
}) {
  const { parts, played } = result.parsed;
  const notes: string[] = [];
  if (result.meaning === "indexing") notes.push("Getting topic search ready…");
  if (played && result.historySince === null) notes.push("No services recorded yet");
  else if (played && result.historySince !== null)
    notes.push(`Services recorded since ${new Date(result.historySince).toLocaleDateString()}`);
  notes.push(...result.notes);
  if (parts.length === 0 && notes.length === 0) return null;
  return (
    <div className={`flex flex-wrap items-center gap-2 border-b border-border py-2 text-xs ${className}`}>
      {parts.map((part) => (
        <span
          key={`${part.kind}-${part.start}`}
          className="inline-flex items-center gap-1 rounded-full bg-accent px-2.5 py-0.5 font-medium"
        >
          {part.label}
          <button
            type="button"
            onClick={() => onRemove(part)}
            aria-label={`Remove ${part.label}`}
            className="rounded-full text-muted-foreground hover:text-foreground"
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      {notes.map((n) => (
        <span key={n} className="text-muted-foreground">
          {n}
        </span>
      ))}
    </div>
  );
}
