/**
 * The settings sub-pages, in sidebar order. The sidebar lists them under
 * "Settings" while settings are open; `SettingsPage` renders one at a time.
 *
 * Each page is short enough to read without collapsing anything — that is
 * the point of splitting them. The one exception is Developer tools, folded
 * at the bottom of "Other": few people need it, and its recordings list is
 * long.
 */

import { FEATURES } from "@/lib/features";

export type SettingsPageId = "audio" | "ai" | "display" | "scripture" | "other";

export interface SettingsPageInfo {
  id: SettingsPageId;
  label: string;
  /** One line under the page title. */
  description: string;
}

const ALL: SettingsPageInfo[] = [
  { id: "audio", label: "Audio", description: "What the AI listens to." },
  { id: "ai", label: "AI", description: "Following the songs, and AI assistants." },
  { id: "display", label: "Display", description: "How lyrics look on the projector." },
  { id: "scripture", label: "Scripture", description: "Bible translations and how passages look." },
  { id: "other", label: "Other", description: "Privacy, updates and developer tools." },
];

export function settingsPages(): SettingsPageInfo[] {
  return ALL.filter((p) => p.id !== "scripture" || FEATURES.liveScripture);
}

export const DEFAULT_SETTINGS_PAGE: SettingsPageId = "audio";

export function settingsPageInfo(id: SettingsPageId): SettingsPageInfo {
  return ALL.find((p) => p.id === id) ?? ALL[0]!;
}
