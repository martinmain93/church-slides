/**
 * A service's date box, saved once the date is settled rather than on every
 * keystroke. A date input reports each valid partial date while the year is
 * typed (0002, 0020, 0202, 2027…), and each save can rename the service, so
 * saving them all would rename it four times. Saves wait for a pause or for
 * leaving the box, skip dates no service is for, and run one at a time, each
 * told the date the one before it saved.
 */

import { useEffect, useRef, useState, type ChangeEvent } from "react";

import { fromIsoDate } from "@/lib/setlistName";

const SETTLE_MS = 800;

/** A date a service could be on: well-formed, and not a half-typed year. */
export function plausibleServiceDate(date: string): boolean {
  const d = fromIsoDate(date);
  return !!d && d.getFullYear() >= 2000 && d.getFullYear() <= 2100;
}

/** `commit(next, previous)` saves the new date (null clears it). */
export function useServiceDateField(
  saved: string | null | undefined,
  commit: (next: string | null, previous: string | null) => Promise<void>,
) {
  const [draft, setDraft] = useState(saved ?? "");
  const savedRef = useRef(saved ?? null);
  const commitRef = useRef(commit);
  commitRef.current = commit;
  const chain = useRef<Promise<void>>(Promise.resolve());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Changed elsewhere (another device, a reload): show it.
  useEffect(() => {
    savedRef.current = saved ?? null;
    setDraft(saved ?? "");
  }, [saved]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const flush = (value: string) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const next = value || null;
    if (next && !plausibleServiceDate(next)) return;
    chain.current = chain.current.then(async () => {
      const previous = savedRef.current;
      if (next === previous) return;
      await commitRef.current(next, previous);
      savedRef.current = next;
    }).catch(() => undefined);
  };

  return {
    value: draft,
    onChange: (e: ChangeEvent<HTMLInputElement>) => {
      const v = e.target.value;
      setDraft(v);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => flush(v), SETTLE_MS);
    },
    onBlur: () => flush(draft),
  };
}
