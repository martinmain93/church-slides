/**
 * A service's date and the name it starts with — the same rules on the
 * desktop and the web.
 *
 * Every service has a date. A new one is for the first Sunday from today
 * that has no service yet, and is named after it, e.g. "Sunday 6 Dec". A
 * copy is for the week after the service it copies (or the next free Sunday
 * if that's past or taken). The name and the date are each editable; a name
 * that is still a date name follows the date when it changes, and a name
 * someone typed stays. If a day does hold two services, the second is
 * "(2)", so no two share a name.
 *
 * Dates are `YYYY-MM-DD` in the planner's own time zone, with no time: a
 * service on Sunday is on Sunday wherever its slides computer is.
 */

/** Something with a name and (maybe) a date: a service in a list. */
export interface DatedName {
  name: string;
  service_date?: string | null;
}

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

function addDays(date: string, days: number): string {
  const d = fromIsoDate(date)!;
  d.setDate(d.getDate() + days);
  return isoDate(d);
}

/** Today if it's Sunday, otherwise the coming Sunday. */
export function nextServiceDate(now: Date = new Date()): string {
  const sunday = new Date(now);
  sunday.setDate(now.getDate() + ((7 - now.getDay()) % 7));
  return isoDate(sunday);
}

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** The name a service on `date` starts with: "Sunday 5 Oct". */
export function serviceDateName(date: string): string {
  const d = fromIsoDate(date);
  if (!d) return "Service";
  return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
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

const DATE_NAME = new RegExp(`^(${WEEKDAYS.join("|")}) \\d{1,2} (${MONTHS.join("|")})( \\(\\d+\\))?$`);

/** Whether `name` is still a date name ("Sunday 6 Dec", perhaps with
 * "(2)"), so it follows the date when the date changes. Judged from the
 * name alone, so it holds however the date got where it is. */
export function isDateName(name: string): boolean {
  return DATE_NAME.test(name.trim());
}

/** `from`, or the same weekday a week later, and so on: the first one no
 * service in `existing` is on. */
export function freeDate(existing: readonly DatedName[], from: string): string {
  const taken = new Set(existing.map((s) => s.service_date).filter(Boolean));
  let d = from;
  while (taken.has(d)) d = addDays(d, 7);
  return d;
}

/** A new service: the first free Sunday from today, named after it. */
export function newService(existing: readonly DatedName[], now: Date = new Date()): { name: string; date: string } {
  const date = freeDate(existing, nextServiceDate(now));
  return { date, name: uniqueName(serviceDateName(date), existing.map((s) => s.name)) };
}

/** A copy of `source`: the week after it (or the next free Sunday when
 * that's past or taken), named after its date unless the source was
 * named by hand. */
export function copiedService(
  source: DatedName,
  existing: readonly DatedName[],
  now: Date = new Date(),
): { name: string; date: string } {
  const soonest = nextServiceDate(now);
  const weekAfter = source.service_date ? addDays(source.service_date, 7) : soonest;
  // A copy of an evening service stays on its weekday; one already past
  // goes to the coming Sunday.
  const date = freeDate(existing, weekAfter >= soonest ? weekAfter : soonest);
  const base = isDateName(source.name) || !source.name.trim() ? serviceDateName(date) : source.name.trim();
  return { date, name: uniqueName(base, existing.map((s) => s.name)) };
}

/** The name a service gets when its date changes: a date name follows the
 * new date; a name someone typed stays. */
export function nameForDate(name: string, newDate: string, taken: Iterable<string>): string {
  return isDateName(name) ? uniqueName(serviceDateName(newDate), taken) : name;
}

/** "Sun 5 Oct" (with the year when it isn't this year) — a service's date
 * in a list, in the same style as its name. */
export function shortServiceDate(date: string | null | undefined, now: Date = new Date()): string | null {
  const d = fromIsoDate(date);
  if (!d) return null;
  const year = d.getFullYear() === now.getFullYear() ? "" : ` ${d.getFullYear()}`;
  return `${WEEKDAYS[d.getDay()]!.slice(0, 3)} ${d.getDate()} ${MONTHS[d.getMonth()]}${year}`;
}

/** The date to show beside a service's name in a list, or null when the
 * name already says it. */
export function rowDate(s: DatedName, now: Date = new Date()): string | null {
  if (!s.service_date) return null;
  if (isDateName(s.name) && s.name.startsWith(serviceDateName(s.service_date))) {
    // Only the year is news.
    const d = fromIsoDate(s.service_date)!;
    return d.getFullYear() === now.getFullYear() ? null : String(d.getFullYear());
  }
  return shortServiceDate(s.service_date, now);
}

/** A service's day: its date, or for one made before services had dates,
 * the day its name says ("Sunday 22 Nov", in the year it was made). */
export function serviceDay(s: DatedName & { created_at: number }): string | null {
  if (s.service_date) return s.service_date;
  const m = /^(?:\w+) (\d{1,2}) (\w{3})(?: \(\d+\))?$/.exec(s.name.trim());
  if (!m || !isDateName(s.name)) return null;
  const made = new Date(s.created_at);
  const month = MONTHS.indexOf(m[2]!);
  // Named for a day near when it was made: December's service made in
  // January belongs to the year before, January's made in December to the next.
  let year = made.getFullYear();
  if (month - made.getMonth() > 6) year--;
  if (made.getMonth() - month > 6) year++;
  return isoDate(new Date(year, month, Number(m[1]), 12));
}

/** How long an undated, never-run service counts as coming up. */
const UNDATED_FRESH_MS = 14 * 86_400_000;

/** Services as both apps list them: coming ones first, soonest at the
 * top; then past ones, latest first. One with no date at all (made before
 * services had one) is coming up until it's run, or two weeks old. */
export function groupServices<S extends DatedName & { created_at: number; last_used_at: number | null }>(
  services: S[],
  today: string = isoDate(new Date()),
): { upcoming: S[]; past: S[] } {
  const ran = (s: S) => (s.last_used_at ? isoDate(new Date(s.last_used_at)) : null);
  const todayMs = fromIsoDate(today)!.getTime();
  const isPast = (s: S) => {
    const day = serviceDay(s);
    if (day) return day < today;
    return ran(s) !== null || todayMs - s.created_at > UNDATED_FRESH_MS;
  };
  const soon = (s: S) => serviceDay(s) ?? "9999";
  const upcoming = services
    .filter((s) => !isPast(s))
    .sort((a, b) => soon(a).localeCompare(soon(b)) || b.created_at - a.created_at);
  const day = (s: S) => serviceDay(s) ?? ran(s) ?? isoDate(new Date(s.created_at));
  const past = services.filter(isPast).sort((a, b) => day(b).localeCompare(day(a)) || b.created_at - a.created_at);
  return { upcoming, past };
}

/** The service to copy for "Copy last service": the latest one on or
 * before today (by date, else when it was run), so a draft planned for
 * next month isn't it. */
export function lastService<S extends DatedName & { created_at: number; last_used_at: number | null }>(
  services: S[],
  today: string = isoDate(new Date()),
): S | null {
  const { past, upcoming } = groupServices(services, today);
  const todays = upcoming.find((s) => s.service_date === today);
  return todays ?? past[0] ?? null;
}

/** The coming service's name, before checking it's unique. */
export function defaultSetlistName(now: Date = new Date()): string {
  return serviceDateName(nextServiceDate(now));
}
