/**
 * Read the scripture display settings.
 *
 * A hook rather than a store because these change rarely (an operator toggling
 * a preference between services, not during one) and because a hook keeps the
 * dependency one-directional: views import from `lib/scripture`, and nothing
 * in `lib/scripture` is imported by the settings layer.
 *
 * While loading, and if the settings call fails, it returns the defaults.
 */

import { useEffect, useState } from "react";

import { getSettings } from "@/lib/tauri";
import {
  DEFAULT_SCRIPTURE_SETTINGS,
  withScriptureDefaults,
  type ScriptureSettings,
} from "@/lib/scripture/settings";

export function useScriptureSettings(): ScriptureSettings {
  const [settings, setSettings] = useState<ScriptureSettings>(
    DEFAULT_SCRIPTURE_SETTINGS,
  );

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((app) => {
        if (cancelled) return;
        setSettings(withScriptureDefaults(app.scripture));
      })
      .catch(() => {
        // Keep the defaults.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return settings;
}
