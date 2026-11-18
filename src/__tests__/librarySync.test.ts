import { describe, expect, it } from "vitest";

import { syncNote } from "@/lib/librarySync";
import type { LibrarySyncStatus } from "@/lib/tauri";

const s = (p: Partial<LibrarySyncStatus>): LibrarySyncStatus => ({ org_id: "o1", state: "ok", waiting: 0, message: null, ...p });

describe("the library sync note", () => {
  it("says nothing when all's well, or for this computer's own library", () => {
    expect(syncNote(s({}))).toBeNull();
    expect(syncNote(s({ state: "syncing", waiting: 2 }))).toBeNull();
    expect(syncNote(s({ org_id: null, state: "off" }))).toBeNull();
  });

  it("counts what's waiting while offline, and shows what went wrong", () => {
    expect(syncNote(s({ state: "offline", waiting: 3 }))?.text).toBe("Offline · 3 changes waiting to go up");
    expect(syncNote(s({ state: "offline", waiting: 1 }))?.text).toBe("Offline · 1 change waiting to go up");
    expect(syncNote(s({ state: "ok", message: "Couldn't save a change: your role can't edit that in Grace." }))?.tone).toBe("warn");
    expect(syncNote(s({ state: "signed_out" }))?.text).toMatch(/sign in/);
  });
});
