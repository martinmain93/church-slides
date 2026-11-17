/**
 * First run: if another presentation app is on this computer and the library
 * is still (nearly) empty, offer once to bring its songs and services across.
 * Whatever the answer, it isn't offered again — Library → Import → From
 * another app does the same any time.
 *
 * Held back while another first-run screen is up and during a service.
 */

import { useEffect, useState } from "react";

import { BringSongsDialog } from "@/components/library/BringSongsDialog";
import { detectOtherApps, type DetectedApp } from "@/lib/otherApps";
import { listSongs } from "@/lib/tauri";

const OFFERED_KEY = "church-slides:bring-songs-offered";

/** More songs than the example hymns: the library is already in use. */
const LIBRARY_IN_USE = 10;

function alreadyOffered(): boolean {
  try {
    return localStorage.getItem(OFFERED_KEY) === "1";
  } catch {
    return true; // no storage: don't risk asking every launch
  }
}

function markOffered() {
  try {
    localStorage.setItem(OFFERED_KEY, "1");
  } catch {
    // nothing to do
  }
}

export function BringSongsPrompt({ held, onImported }: { held: boolean; onImported: (message: string) => void }) {
  const [apps, setApps] = useState<DetectedApp[] | null>(null);

  useEffect(() => {
    if (alreadyOffered()) return;
    let cancelled = false;
    Promise.all([detectOtherApps(), listSongs()])
      .then(([found, songs]) => {
        if (cancelled) return;
        if (found.length > 0 && songs.length <= LIBRARY_IN_USE) setApps(found);
        else markOffered();
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (!apps || held) return null;
  const done = () => {
    markOffered();
    setApps(null);
  };
  return (
    <BringSongsDialog
      apps={apps}
      onClose={done}
      onImported={(message) => {
        done();
        onImported(message);
      }}
    />
  );
}
