/**
 * The scripture settings section: translations, and whether a passage on
 * screen has a style of its own. By default it looks like everything else
 * (the display look); "Override default style" opens the same style modal for
 * scripture alone, and "Use default style" drops that override.
 *
 * Self-contained — it reads and writes the settings blob itself rather than
 * joining `SettingsPage`'s form state, so mounting it costs that page one line
 * and no new fields. Every control except the switch is hidden while the
 * feature is off, so an operator who does not use scripture sees one toggle.
 *
 * The translation list comes from the sidecar, which is also the only thing
 * that can install one. Nothing here is usable until a translation is on disk,
 * so the installed list and its two ways in come first after the switch:
 * "Find a translation" (eBible.org's public-domain Bibles,
 * `TranslationBrowserDialog`) and "Import a Bible file" (a church's own,
 * `ScriptureImportDialog`). The setting here is only the *default*: the
 * operator can pick another for a service, in the Scripture tab.
 */

import { useCallback, useEffect, useState } from "react";
import { FileUp, Search, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DisplayLookDialog } from "@/components/presentation/DisplayLookDialog";
import { ScriptureImportDialog } from "@/components/scripture/ScriptureImportDialog";
import { TranslationBrowserDialog } from "@/components/scripture/TranslationBrowserDialog";
import { listTranslations, removeTranslation } from "@/lib/scripture/client";
import { saveScriptureLook, useDisplayLook, useScriptureLook } from "@/lib/displayLook";
import { withScriptureDefaults, type ScriptureSettings } from "@/lib/scripture/settings";
import { getSettings, updateSettings, type AppSettings } from "@/lib/tauri";
import { useToast } from "@/lib/toast";
import type { ScriptureTranslationInfo } from "@/types/generated";

export function ScriptureSettingsPanel() {
  const toast = useToast();
  const [app, setApp] = useState<AppSettings | null>(null);
  const [settings, setSettings] = useState<ScriptureSettings | null>(null);
  const [catalog, setCatalog] = useState<ScriptureTranslationInfo[]>([]);
  const [importing, setImporting] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  // Remove is two clicks: the first arms it, the second removes.
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const defaultLook = useDisplayLook();
  const scriptureLook = useScriptureLook();
  const [styling, setStyling] = useState(false);
  // The engine didn't answer: say so rather than "None installed".
  const [engineError, setEngineError] = useState<string | null>(null);

  useEffect(() => {
    getSettings()
      .then((loaded) => {
        setApp(loaded);
        setSettings(withScriptureDefaults(loaded.scripture));
      })
      .catch(() => undefined);
    listTranslations()
      .then((c) => {
        setCatalog(c.translations);
        setEngineError(c.error);
      })
      .catch(() => undefined);
  }, []);

  const patch = useCallback(
    async (changes: Partial<ScriptureSettings>) => {
      if (!app || !settings) return;
      const next = { ...settings, ...changes };
      setSettings(next);
      try {
        await updateSettings({ ...app, scripture: next });
      } catch (err) {
        toast.error(`Could not save: ${err}`);
      }
    },
    [app, settings, toast],
  );

  const refresh = useCallback(async () => {
    setCatalog(await listTranslations().then((c) => c.translations));
  }, []);

  const remove = useCallback(
    async (id: string) => {
      setConfirmRemove(null);
      const result = await removeTranslation(id);
      if (!result.removed) {
        toast.error(result.error ?? "Couldn't remove it");
        return;
      }
      toast.success("Removed");
      const next = await listTranslations();
      setCatalog(next.translations);
      // The display must never point at a translation that isn't there: fall
      // back to another one that is, or to the default (which offers itself
      // for download wherever it's needed).
      if (settings?.translation === id) {
        const fallback = next.translations.find((t) => t.installed)?.id ?? next.defaultTranslation;
        await patch({ translation: fallback || "web" });
      }
    },
    [toast, settings, patch],
  );

  if (!settings) return null;

  const installed = catalog.filter((t) => t.installed);

  return (
    <div className="space-y-4">
      <Row
        label="Default translation"
        hint="The operator can pick another for a service."
      >
        {installed.length > 0 ? (
          <Select
            value={settings.translation}
            onChange={(v) => void patch({ translation: v })}
            options={installed.map((t) => ({
              value: t.id,
              label: t.abbreviation ? `${t.name} (${t.abbreviation})` : t.name,
            }))}
            ariaLabel="Default translation"
          />
        ) : (
          <span className="text-xs text-muted-foreground">
            {engineError ? "Can't check — the app's engine isn't answering" : "None installed"}
          </span>
        )}
      </Row>

      <div className="space-y-1.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs text-muted-foreground">
            Installed translations work offline.
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() => setBrowsing(true)}
            >
              <Search className="size-3.5" />
              Find a translation…
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() => setImporting(true)}
              title="Your church's own Bible — for a licensed translation like the NIV or ESV"
            >
              <FileUp className="size-3.5" />
              Import a Bible file…
            </Button>
          </div>
        </div>
        {installed.length > 0 && (
          <ul className="divide-y divide-border rounded-md border border-border">
            {installed.map((t) => (
              <li key={t.id} className="flex items-center gap-3 px-3 py-1.5">
                <div className="min-w-0 flex-1">
                  <div className="text-sm">
                    {t.name}{" "}
                    <span className="text-xs text-muted-foreground">{t.abbreviation}</span>
                    {t.id === settings.translation && (
                      <span className="ml-1.5 text-xs text-emerald-500">default</span>
                    )}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {t.imported ? `Imported · ${t.attribution}` : t.attribution}
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className={`h-7 gap-1.5 text-xs ${
                    confirmRemove === t.id ? "text-destructive" : ""
                  }`}
                  onClick={() =>
                    confirmRemove === t.id ? void remove(t.id) : setConfirmRemove(t.id)
                  }
                  onBlur={() => setConfirmRemove((c) => (c === t.id ? null : c))}
                  aria-label={
                    confirmRemove === t.id ? `Confirm removing ${t.name}` : `Remove ${t.name}`
                  }
                >
                  <Trash2 className="size-3.5" />
                  {confirmRemove === t.id ? "Remove?" : "Remove"}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {browsing && (
        <TranslationBrowserDialog
          onClose={() => setBrowsing(false)}
          onInstalled={(id, name) => {
            toast.success(`${name} downloaded`);
            void refresh();
            // The first translation a church downloads is its default.
            if (installed.length === 0) void patch({ translation: id });
          }}
        />
      )}

      {styling && (
        <DisplayLookDialog
          onClose={() => setStyling(false)}
          override={{
            title: "Scripture look",
            description:
              "How a Bible passage looks on screen, in every service on this computer. A song's or passage's own style in a service still wins.",
            value: scriptureLook ?? defaultLook,
            onChange: saveScriptureLook,
            onUseDefault: scriptureLook
              ? () => {
                  saveScriptureLook(null);
                  setStyling(false);
                }
              : undefined,
          }}
        />
      )}

      {importing && (
        <ScriptureImportDialog
          onClose={() => setImporting(false)}
          onImported={(id, name) => {
            setImporting(false);
            toast.success(`${name} installed`);
            void refresh();
            // Importing a Bible is choosing it: make it the default.
            void patch({ translation: id });
          }}
        />
      )}

      <Row
        label="Style"
        hint={scriptureLook ? "Scripture has its own style." : "The same style as the lyrics."}
      >
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => setStyling(true)}>
            {scriptureLook ? "Change style" : "Override default style"}
          </Button>
          {scriptureLook && (
            <Button type="button" variant="ghost" size="sm" onClick={() => saveScriptureLook(null)}>
              Use default style
            </Button>
          )}
        </div>
      </Row>

      <Row label="Show reference" hint="e.g. “Psalm 103:1–5”.">
        <Check
          value={settings.show_reference}
          onChange={(v) => void patch({ show_reference: v })}
          ariaLabel="Show reference"
        />
      </Row>

      <Row label="Show translation" hint="e.g. “WEB”, beside the reference.">
        <Check
          value={settings.show_translation}
          onChange={(v) => void patch({ show_translation: v })}
          ariaLabel="Show translation"
        />
      </Row>
    </div>
  );
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-x-6 gap-y-1">
      <div>
        <div className="text-sm font-medium">{label}</div>
        {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Select({
  value,
  onChange,
  options,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  ariaLabel: string;
}) {
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-8 rounded-md border border-input bg-background px-2 text-sm"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function Check({
  value,
  onChange,
  ariaLabel,
}: {
  value: boolean;
  onChange: (value: boolean) => void;
  ariaLabel: string;
}) {
  return (
    <input
      type="checkbox"
      aria-label={ariaLabel}
      checked={value}
      onChange={(e) => onChange(e.target.checked)}
      className="size-4 accent-emerald-500"
    />
  );
}
