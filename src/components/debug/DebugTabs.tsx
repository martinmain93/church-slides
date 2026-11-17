/**
 * The tab strip and its contents — everything the debug surface *is*, minus
 * the container it sits in.
 *
 * Extracted because that surface now has two homes: the floating card docked
 * over the operator screen (`DebugPanel`) and a separate window on a second
 * monitor (`DebugWindow`). Duplicating the tab list across them is how a tab
 * ends up existing in one and not the other — this file already carries a
 * scar from the Audio tab appearing twice in one list and not at all in the
 * other.
 *
 * The selected tab persists to localStorage, which is per-origin and so
 * shared between the windows: detaching lands on the tab you were reading.
 */

import { useState, type ReactElement } from "react";

import { BeliefStateTab } from "@/components/debug/BeliefStateTab";
import { LearningTab } from "@/components/debug/LearningTab";
import { MusicTab } from "@/components/debug/MusicTab";
import { PerfTab } from "@/components/debug/PerfTab";
import { SpokenInstructionsTab } from "@/components/debug/SpokenInstructionsTab";
import { TranscriptTab } from "@/components/debug/TranscriptTab";
import { TunablesTab } from "@/components/debug/TunablesTab";
import { FEATURES } from "@/lib/features";
import { cn } from "@/lib/utils";

export const TABS = [
  "Transcript",
  "Belief state",
  "Music",
  "Instructions",
  "Tunables",
  "Learning",
  "Perf",
] as const;
export type Tab = (typeof TABS)[number];

/** The tabs this build shows: Instructions holds the spoken-requests switch. */
export const VISIBLE_TABS: readonly Tab[] = TABS.filter(
  (t) => t !== "Instructions" || FEATURES.spokenInstructions,
);

const STORAGE_KEY = "church_slides_debug_tab_v1";

export function loadTab(): Tab {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw && (VISIBLE_TABS as readonly string[]).includes(raw)) return raw as Tab;
  } catch {
    // ignore
  }
  return "Transcript";
}

function renderTab(tab: Tab): ReactElement {
  switch (tab) {
    case "Transcript":
      return <TranscriptTab />;
    case "Belief state":
      return <BeliefStateTab />;
    case "Music":
      return <MusicTab />;
    case "Instructions":
      return <SpokenInstructionsTab />;
    case "Tunables":
      return <TunablesTab />;
    case "Learning":
      return <LearningTab />;
    case "Perf":
      return <PerfTab />;
  }
}

export function DebugTabs() {
  const [tab, setTab] = useState<Tab>(loadTab);

  const select = (next: Tab) => {
    setTab(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore
    }
  };

  return (
    <>
      <nav className="flex border-b border-white/10 text-xs">
        {VISIBLE_TABS.map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => select(name)}
            className={cn(
              "flex-1 whitespace-nowrap px-2 py-2 text-[11px] transition-colors",
              tab === name
                ? "border-b-2 border-emerald-400 text-zinc-100"
                : "text-zinc-500 hover:text-zinc-300",
            )}
          >
            {name}
          </button>
        ))}
      </nav>
      <div className="flex-1 overflow-y-auto text-sm">{renderTab(tab)}</div>
    </>
  );
}
