/**
 * The desktop app's library: its own database, through Tauri. Each method is
 * the command the app already uses, so going through `LibrarySource` changes
 * nothing about how the desktop behaves.
 */

import { LIBRARY_CHANGED_EVENT } from "@/lib/assistantConnect";
import { isInTauri } from "@/lib/invoke";
import * as tauri from "@/lib/tauri";
import type { LibrarySource } from "@/library/source";

export const desktopLibrary: LibrarySource = {
  capabilities: {
    canPresent: true,
    canImportFiles: true,
    canEditCharts: true,
    hasBibles: true,
    canSearchByMeaning: true,
  },

  listSongs: tauri.listSongs,
  getSong: tauri.getSong,
  createSong: tauri.createSong,
  updateSong: (id, input) => tauri.updateSong(id, input),
  deleteSong: tauri.deleteSong,
  getChart: async (songId) => (await tauri.getSongCharts([songId]))[songId] ?? null,

  listSetlists: tauri.listSetlists,
  getSetlist: tauri.getSetlist,
  createSetlist: tauri.createSetlist,
  renameSetlist: tauri.updateSetlistName,
  setServiceDate: tauri.updateSetlistDate,
  deleteSetlist: tauri.deleteSetlist,
  copySetlist: tauri.duplicateSetlist,

  addSong: tauri.addSetlistItem,
  addServiceItem: (setlistId, kind, content, afterItemId) => tauri.addServiceItem(setlistId, kind, content, { afterItemId }),
  removeItem: tauri.removeSetlistItem,
  reorderItems: tauri.reorderSetlist,
  setItemKey: tauri.setSetlistItemKey,
  setItemSlides: tauri.setSetlistItemSlides,
  setItemContent: async (setlistId, itemId, content, slidesOverride) => {
    await tauri.setSetlistItemContent(setlistId, itemId, content);
    if (slidesOverride !== undefined) await tauri.setSetlistItemSlides(setlistId, itemId, slidesOverride);
  },

  listReadings: tauri.listReadings,
  saveReading: tauri.saveReading,
  deleteReading: tauri.deleteReading,

  onChange(listener) {
    if (!isInTauri()) return () => undefined;
    let off: (() => void) | undefined;
    let stopped = false;
    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      const un = await listen(LIBRARY_CHANGED_EVENT, () => listener());
      if (stopped) un();
      else off = un;
    });
    return () => {
      stopped = true;
      off?.();
    };
  },
};
