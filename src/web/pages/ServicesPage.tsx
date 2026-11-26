import { useState } from "react";
import { Plus } from "lucide-react";

import type { SetlistRow } from "@/core/library";
import { useLibrary } from "@/library/context";
import { isoDate, newService, shortServiceDate } from "@/lib/setlistName";
import { go, href } from "@/web/route";
import { Empty, ErrorLine, PageHeader, errorText, iconButtonClass, useLibraryData, useTitle } from "@/web/ui";
import { cn } from "@/lib/utils";

/** Coming services first, soonest at the top; then past ones, latest
 * first. A service with no date (made before services had one) is coming
 * until it has been run. */
export function groupServices(services: SetlistRow[], today: string = isoDate(new Date())): { upcoming: SetlistRow[]; past: SetlistRow[] } {
  const ran = (s: SetlistRow) => (s.last_used_at ? isoDate(new Date(s.last_used_at)) : null);
  const isPast = (s: SetlistRow) => (s.service_date ? s.service_date < today : ran(s) !== null);
  // Undated ones after the dated, newest first.
  const soon = (s: SetlistRow) => s.service_date ?? "9999";
  const upcoming = services
    .filter((s) => !isPast(s))
    .sort((a, b) => soon(a).localeCompare(soon(b)) || b.created_at - a.created_at);
  const day = (s: SetlistRow) => s.service_date ?? ran(s) ?? "";
  const past = services.filter(isPast).sort((a, b) => day(b).localeCompare(day(a)) || b.created_at - a.created_at);
  return { upcoming, past };
}

export function ServicesPage({ canEdit }: { canEdit: boolean }) {
  const lib = useLibrary();
  const { data: services, error: loadError } = useLibraryData((l) => l.listSetlists(), []);
  const [error, setError] = useState<string | null>(null);
  useTitle("Services");

  const create = async () => {
    setError(null);
    try {
      const { name, date } = newService((services ?? []).map((s) => s.name));
      const id = await lib.createSetlist(name, date);
      go({ page: "service", id, created: true });
    } catch (err) {
      setError(errorText(err));
    }
  };

  const groups = services ? groupServices(services) : null;

  return (
    <>
      <PageHeader
        title="Services"
        action={
          canEdit && (
            <button type="button" className={iconButtonClass} aria-label="New service" onClick={() => void create()} data-testid="new-service">
              <Plus className="size-5" />
            </button>
          )
        }
      />
      {(error ?? loadError) && <ErrorLine>{error ?? loadError}</ErrorLine>}
      {!groups ? null : groups.upcoming.length + groups.past.length === 0 ? (
        <Empty>No services yet. {canEdit ? "Plan one here and it's ready on the slides computer." : ""}</Empty>
      ) : (
        <>
          <ServiceList title="Upcoming" services={groups.upcoming} />
          <ServiceList title="Past" services={groups.past} past />
        </>
      )}
    </>
  );
}

function ServiceList({ title, services, past = false }: { title: string; services: SetlistRow[]; past?: boolean }) {
  if (services.length === 0) return null;
  return (
    <section aria-label={title}>
      <h2 className="px-4 pb-1 pt-5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
      <ul className="divide-y divide-border border-y border-border">
        {services.map((s) => (
          <li key={s.id}>
            <a href={href({ page: "service", id: s.id })} className="block min-h-14 px-4 py-2.5 hover:bg-muted/50">
              <div className="flex items-baseline justify-between gap-3">
                <span className={cn("truncate font-medium", past && "text-muted-foreground")}>{s.name || "Untitled service"}</span>
                {s.service_date && <span className="shrink-0 text-xs text-muted-foreground">{shortServiceDate(s.service_date)}</span>}
              </div>
              <div className="truncate text-sm text-muted-foreground">
                {s.song_titles && s.song_titles.length > 0 ? s.song_titles.join(" · ") : "Nothing in it yet"}
              </div>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
