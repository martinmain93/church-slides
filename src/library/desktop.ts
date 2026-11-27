/**
 * The desktop app's library: its own database, through Tauri. Each method is
 * the command the app already uses, so going through `LibrarySource` changes
 * nothing about how the desktop behaves. Each is looked up when it's called,
 * so a test that replaces a command (`vi.spyOn(tauri, …)`) reaches it here.
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

  listSongs: (sort) => (sort ? tauri.listSongsSorted(sort) : tauri.listSongs()),
  getSong: (...a) => tauri.getSong(...a),
  createSong: (...a) => tauri.createSong(...a),
  updateSong: (id, input) => tauri.updateSong(id, input),
  deleteSong: (...a) => tauri.deleteSong(...a),
  getChart: async (songId) => (await tauri.getSongCharts([songId]))[songId] ?? null,
  listChartKeys: (...a) => tauri.listChartKeys(...a),

  listSetlists: (...a) => tauri.listSetlists(...a),
  getSetlist: (...a) => tauri.getSetlist(...a),
  createSetlist: (...a) => tauri.createSetlist(...a),
  renameSetlist: (...a) => tauri.updateSetlistName(...a),
  setServiceDate: (...a) => tauri.updateSetlistDate(...a),
  deleteSetlist: (...a) => tauri.deleteSetlist(...a),
  copySetlist: (...a) => tauri.duplicateSetlist(...a),

  addSong: (...a) => tauri.addSetlistItem(...a),
  addServiceItem: (...a) => tauri.addServiceItem(...a),
  removeItem: (...a) => tauri.removeSetlistItem(...a),
  reorderItems: (...a) => tauri.reorderSetlist(...a),
  setItemKey: (...a) => tauri.setSetlistItemKey(...a),
  setItemSlides: (...a) => tauri.setSetlistItemSlides(...a),
  setItemContent: async (setlistId, itemId, content, slidesOverride) => {
    await tauri.setSetlistItemContent(setlistId, itemId, content);
    if (slidesOverride !== undefined) await tauri.setSetlistItemSlides(setlistId, itemId, slidesOverride);
  },

  listReadings: (...a) => tauri.listReadings(...a),
  saveReading: (...a) => tauri.saveReading(...a),
  deleteReading: (...a) => tauri.deleteReading(...a),

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
