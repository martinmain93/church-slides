/**
 * A service's date and the name it starts with.
 *
 * A new service is dated for the one it's most likely for (today if it's
 * Sunday, otherwise the coming Sunday) and named after that date, e.g.
 * "Sunday 5 Oct". A list of setlists all called "New setlist" can't be told
 * apart; dates can. The name and the date are each editable on their own;
 * a name still as it was made follows the date when the date changes.
 * A second service for the same day gets "(2)", and so on, so no two
 * services share a name.
 *
 * Dates are `YYYY-MM-DD` in the planner's own time zone, with no time: a
 * service on Sunday is on Sunday wherever its slides computer is.
 */

/** `YYYY-MM-DD` for a local date. */
export function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** The local date `YYYY-MM-DD` stands for (at midday, so no DST edge moves
 * it), or null when malformed. */
export function fromIsoDate(s: string | null | undefined): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s ?? "");
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12);
  return d.getMonth() === Number(m[2]) - 1 ? d : null;
}

/** Today if it's Sunday, otherwise the coming Sunday. */
export function nextServiceDate(now: Date = new Date()): string {
  const sunday = new Date(now);
  sunday.setDate(now.getDate() + ((7 - now.getDay()) % 7));
  return isoDate(sunday);
}

/** The name a service on `date` starts with: "Sunday 5 Oct". */
export function serviceDateName(date: string): string {
  const d = fromIsoDate(date);
  if (!d) return "Service";
  const weekday = d.toLocaleDateString("en-GB", { weekday: "long" });
  const month = d.toLocaleDateString("en-GB", { month: "short" });
  return `${weekday} ${d.getDate()} ${month}`;
}

/** `base`, or `base (2)`, `base (3)`… whichever isn't taken. */
export function uniqueName(base: string, taken: Iterable<string>): string {
  const names = new Set([...taken].map((n) => n.trim().toLowerCase()));
  if (!names.has(base.toLowerCase())) return base;
  for (let n = 2; ; n++) {
    const next = `${base} (${n})`;
    if (!names.has(next.toLowerCase())) return next;
  }
}

/** Whether `name` is still the one made from `date` (perhaps with "(2)"),
 * so it should follow the date when the date changes. */
export function isDateName(name: string, date: string | null | undefined): boolean {
  if (!date) return false;
  const base = serviceDateName(date);
  return name === base || new RegExp(`^${base.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\(\\d+\\)$`).test(name);
}

/** A new service's date and name, unique among `taken`. */
export function newService(taken: Iterable<string>, now: Date = new Date()): { name: string; date: string } {
  const date = nextServiceDate(now);
  return { date, name: uniqueName(serviceDateName(date), taken) };
}

/** The name a service gets when its date changes: a name made from the old
 * date follows the new one; a name someone typed stays. */
export function nameForDate(
  name: string,
  oldDate: string | null | undefined,
  newDate: string,
  taken: Iterable<string>,
): string {
  return isDateName(name, oldDate) ? uniqueName(serviceDateName(newDate), taken) : name;
}

/** "Sun 5 Oct 2026" — a service's date in a list. */
export function shortServiceDate(date: string | null | undefined, now: Date = new Date()): string | null {
  const d = fromIsoDate(date);
  if (!d) return null;
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
}

/** The coming service's name, before checking it's unique. */
export function defaultSetlistName(now: Date = new Date()): string {
  return serviceDateName(nextServiceDate(now));
}
