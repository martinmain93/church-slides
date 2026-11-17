/**
 * Everything scripture asks of the sidecar: what a reference says, search,
 * and the translations (catalogue, install, import, remove).
 *
 * Every call degrades to an empty result rather than throwing. The sidecar
 * may be down (browser dev mode) or the translation may not be installed yet,
 * and neither is a reason for a projection surface to break.
 */

import { requestSidecar } from "@/lib/sidecarClient";
import type {
  ScriptureCatalogEntry,
  ScriptureImportReport,
  ScripturePassage,
  ScriptureTranslationInfo,
} from "@/types/generated";

// --- Sidecar side: text, search, translations ------------------------------

export interface ResolvedPassages {
  /** Keyed by the reference that was asked for. */
  byReference: Map<string, ScripturePassage>;
  /** Reference -> why it could not be resolved. */
  errors: Map<string, string>;
}

const EMPTY_RESOLVED: ResolvedPassages = {
  byReference: new Map(),
  errors: new Map(),
};

/**
 * Look up the text for a batch of references.
 *
 * Batched because the caller is usually a whole setlist loading at once, and
 * because a service starting is the one moment worth not spending N round
 * trips on. One unresolvable reference costs only itself — it lands in
 * `errors` while the rest resolve.
 */
export async function resolvePassages(
  references: string[],
  translation = "",
): Promise<ResolvedPassages> {
  if (references.length === 0) return EMPTY_RESOLVED;
  try {
    const reply = await requestSidecar({
      kind: "resolve_passages",
      references,
      translation,
    });
    // Keyed by what was asked *and* by the canonical form. A passage comes
    // back canonical ("Jeremiah 29:11", en dash in ranges), which is rarely
    // how it was typed ("jer 29:11", "1-3"); keyed only canonically, a typed
    // reference resolved fine and then was never found, so it neither showed
    // nor appeared among the errors.
    const canonical = new Map(reply.passages.map((p) => [p.reference, p]));
    const byReference = new Map(canonical);
    for (const [asked, ref] of Object.entries(reply.resolved ?? {})) {
      const passage = canonical.get(ref);
      if (passage) byReference.set(asked, passage);
    }
    return {
      byReference,
      errors: new Map(Object.entries(reply.errors ?? {})),
    };
  } catch {
    // Sidecar down. The pane shows nothing, which is the correct degradation:
    // scripture is an annotation, and the lyrics do not depend on it.
    return EMPTY_RESOLVED;
  }
}

export interface FoundReferences {
  passages: ScripturePassage[];
  /** References a model named that the installed Bible doesn't hold. */
  unresolved: string[];
}

/**
 * The passages in a message the pastor sent ("Sermon: John 3:1-21, also Rom
 * 5:8"), each checked against the installed Bible (`scripture.extract`).
 */
export async function findScriptureReferences(text: string, translation = ""): Promise<FoundReferences> {
  if (!text.trim()) return { passages: [], unresolved: [] };
  try {
    const reply = await requestSidecar({ kind: "find_scripture_references", text, translation });
    // An engine that failed answers with an error, not passages: nothing found.
    return { passages: reply.passages ?? [], unresolved: reply.unresolved ?? [] };
  } catch {
    return { passages: [], unresolved: [] };
  }
}

/**
 * The passage around one verse, narrowest first: to the end of its section,
 * the whole section (with its heading), the whole chapter. Empty for anything
 * but a single verse, or with the engine down.
 */
export async function passageContexts(reference: string, translation = ""): Promise<ScripturePassage[]> {
  try {
    const reply = await requestSidecar({ kind: "passage_contexts", reference, translation });
    return reply.passages ?? [];
  } catch {
    return [];
  }
}

/** BM25 over the verse text — the editor's "where is this line from?" box. */
export async function searchScripture(
  text: string,
  translation = "",
  limit = 8,
): Promise<ScripturePassage[]> {
  if (!text.trim()) return [];
  try {
    const reply = await requestSidecar({
      kind: "search_scripture",
      text,
      translation,
      limit,
    });
    return reply.passages;
  } catch {
    return [];
  }
}

export interface TranslationCatalog {
  translations: ScriptureTranslationInfo[];
  defaultTranslation: string;
  /** Set when the engine couldn't be asked: "nothing installed" would be a lie. */
  error: string | null;
}

/**
 * The translation offered by name when none is installed: the King James, the
 * most widely recognised public-domain Bible (a built-in id, so it installs
 * under "kjv"). The NASB, ESV and NIV are licensed, so they can only be
 * imported from a church's own file.
 */
export const RECOMMENDED_TRANSLATION = {
  id: "kjv",
  abbreviation: "KJV",
  name: "King James Version",
} as const;

export async function listTranslations(): Promise<TranslationCatalog> {
  try {
    const reply = await requestSidecar({ kind: "list_scripture_translations" });
    return {
      translations: reply.translations,
      defaultTranslation: reply.default_translation,
      error: null,
    };
  } catch (err) {
    return {
      translations: [],
      defaultTranslation: "",
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

/**
 * Download and build a public-domain translation: a built-in id ("web") or an
 * eBible.org catalogue id ("engbsb"). Seconds, not milliseconds. `translation`
 * in the result is the id it was installed under — use that one.
 */
export async function installTranslation(
  translation: string,
): Promise<{ installed: boolean; translation: string; error: string | null }> {
  try {
    const reply = await requestSidecar(
      { kind: "install_scripture_translation", translation },
      { timeoutMs: 300_000 },
    );
    return {
      installed: reply.installed ?? false,
      translation: reply.translation || translation,
      error: reply.error ?? null,
    };
  } catch (err) {
    return { installed: false, translation, error: String(err) };
  }
}

/** Search eBible.org's public-domain translations (cached weekly). */
export async function searchTranslationCatalog(
  query: string,
): Promise<{ entries: ScriptureCatalogEntry[]; error: string | null }> {
  try {
    const reply = await requestSidecar(
      { kind: "search_scripture_catalog", query },
      { timeoutMs: 60_000 },
    );
    return { entries: reply.entries ?? [], error: reply.error ?? null };
  } catch (err) {
    return { entries: [], error: String(err) };
  }
}

/** Delete a translation from this machine. */
export async function removeTranslation(
  translation: string,
): Promise<{ removed: boolean; error: string | null }> {
  try {
    const reply = await requestSidecar({ kind: "remove_scripture_translation", translation });
    return { removed: reply.removed ?? false, error: reply.error ?? null };
  } catch (err) {
    return { removed: false, error: String(err) };
  }
}

// --- Importing a church's own Bible -----------------------------------------

export type BibleImportPreview =
  | { ok: true; token: string; report: ScriptureImportReport }
  | { ok: false; error: string };

/** Bigger than any real Bible; refused here so it never crosses the socket. */
const MAX_IMPORT_BYTES = 80 * 1024 * 1024;

function toBase64(bytes: Uint8Array): string {
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

/**
 * Read a Bible file and get back the sidecar's report on it. Installs nothing:
 * the operator reviews the report first, then `commitBibleImport` installs it.
 * The file is read once — the sidecar holds the parsed result under `token`.
 */
export async function previewBibleImport(file: File): Promise<BibleImportPreview> {
  if (file.size > MAX_IMPORT_BYTES) {
    return { ok: false, error: "This file is too large to be a Bible." };
  }
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const reply = await requestSidecar(
      { kind: "preview_scripture_import", file_name: file.name, data_base64: toBase64(bytes) },
      { timeoutMs: 120_000 },
    );
    if (reply.error || !reply.report) {
      return { ok: false, error: reply.error ?? "The file couldn't be read." };
    }
    return { ok: true, token: reply.token ?? "", report: reply.report };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export interface BibleImportDetails {
  name: string;
  abbreviation: string;
  attribution: string;
  rightsConfirmed: boolean;
}

/** Install a previewed Bible. Resolves to the new translation's id. */
export async function commitBibleImport(
  token: string,
  details: BibleImportDetails,
): Promise<{ translation: string | null; error: string | null }> {
  try {
    const reply = await requestSidecar(
      {
        kind: "commit_scripture_import",
        token,
        name: details.name,
        abbreviation: details.abbreviation,
        attribution: details.attribution,
        rights_confirmed: details.rightsConfirmed,
      },
      { timeoutMs: 120_000 },
    );
    return { translation: reply.translation ?? null, error: reply.error ?? null };
  } catch (err) {
    return { translation: null, error: String(err) };
  }
}
