/**
 * "Bring your songs": read the library and setlists of another presentation
 * app on this computer, for the same preview-then-import as song files.
 *
 * `detectOtherApps` is prompt-free (it looks at installed apps, never inside
 * Documents); `readOtherApp` reads the app's own files and runs only after
 * the person chose to import. The reading itself is `src-tauri/src/other_apps.rs`.
 */

import { invoke } from "@/lib/invoke";
import { fromFreeShow, type FreeShowData } from "@/lib/otherApps/freeshow";
import { fromOpenLp, type OpenLpData } from "@/lib/otherApps/openlp";
import { fromOpenSong, type OpenSongData } from "@/lib/otherApps/opensong";
import { fromProPresenter, type ProPresenterData } from "@/lib/otherApps/propresenter";
import type { OtherAppId, OtherAppImport } from "@/lib/otherApps/types";

export { splitRecentServices } from "@/lib/otherApps/types";
export type { ImportedSetlist, OtherAppId, OtherAppImport } from "@/lib/otherApps/types";

export interface DetectedApp {
  id: OtherAppId;
  name: string;
}

export function detectOtherApps(): Promise<DetectedApp[]> {
  return invoke<DetectedApp[]>("detect_other_apps");
}

export async function readOtherApp(app: OtherAppId): Promise<OtherAppImport> {
  const data = await invoke<unknown>("read_other_app", { app });
  switch (app) {
    case "propresenter":
      return fromProPresenter(data as ProPresenterData);
    case "freeshow":
      return fromFreeShow(data as FreeShowData);
    case "openlp":
      return fromOpenLp(data as OpenLpData);
    case "opensong":
      return fromOpenSong(data as OpenSongData);
  }
}
