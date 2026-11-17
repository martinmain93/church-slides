/**
 * Import a church's own Bible: choose a file, review what was found, install.
 *
 * Two steps on purpose. The sidecar reads the file and reports — format, books,
 * anything missing or odd — and nothing is installed until the operator has
 * seen that report and pressed Install. A half-read Bible is a missing verse in
 * the middle of a service; this screen is where it should show up instead.
 *
 * The licence question is the church's, not the app's. The checkbox is
 * required, and the copy says plainly what it means: the text stays on this
 * computer, and whether the church may use it is between the church and the
 * publisher. See `docs/done/BIBLE_INTEGRATION.md`, "Importing a church's own Bible".
 */

import { useCallback, useState } from "react";
import { FileUp, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { pickFiles } from "@/lib/files";
import { commitBibleImport, previewBibleImport } from "@/lib/scripture/client";
import type { ScriptureImportBook, ScriptureImportReport } from "@/types/generated";

const FORMATS =
  "USFM (including eBible.org zips), USX, USFX, OSIS, Zefania, OpenSong, " +
  "verse-per-line text, CSV/TSV and JSON";

const MATCHED_BY: Record<string, string> = {
  code: "Book code",
  number: "Book number",
  name: "Name",
  shape: "Chapter layout",
  "": "Not recognised",
};

type Phase =
  | { kind: "pick"; error?: string }
  | { kind: "reading"; fileName: string }
  | { kind: "review"; fileName: string; token: string; report: ScriptureImportReport }
  | { kind: "installing"; fileName: string; token: string; report: ScriptureImportReport };

export function ScriptureImportDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (translation: string, name: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "pick" });
  const [name, setName] = useState("");
  const [abbreviation, setAbbreviation] = useState("");
  const [attribution, setAttribution] = useState("");
  const [rights, setRights] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const read = useCallback(async (file: File) => {
    setPhase({ kind: "reading", fileName: file.name });
    setError(null);
    const result = await previewBibleImport(file);
    if (!result.ok) {
      setPhase({ kind: "pick", error: result.error });
      return;
    }
    const suggested = suggestDetails(result.report, file.name);
    setName(suggested.name);
    setAbbreviation(suggested.abbreviation);
    setAttribution(suggested.attribution);
    setRights(false);
    setPhase({ kind: "review", fileName: file.name, token: result.token, report: result.report });
  }, []);

  const choose = useCallback(async () => {
    // No `accept` filter: OpenSong Bibles usually have no extension at all.
    const [file] = await pickFiles("", false);
    if (file) void read(file);
  }, [read]);

  const install = useCallback(async () => {
    if (phase.kind !== "review") return;
    setPhase({ ...phase, kind: "installing" });
    setError(null);
    const result = await commitBibleImport(phase.token, {
      name: name.trim(),
      abbreviation: abbreviation.trim(),
      attribution: attribution.trim(),
      rightsConfirmed: rights,
    });
    if (result.translation) {
      onImported(result.translation, name.trim());
      return;
    }
    setError(result.error ?? "Couldn't install it.");
    // An expired token can't be retried: go back to choosing the file.
    if (result.error?.includes("choose the file again")) {
      setPhase({ kind: "pick", error: result.error });
    } else {
      setPhase({ ...phase, kind: "review" });
    }
  }, [phase, name, abbreviation, attribution, rights, onImported]);

  const busy = phase.kind === "reading" || phase.kind === "installing";
  const ready =
    phase.kind === "review" &&
    name.trim() !== "" &&
    abbreviation.trim() !== "" &&
    attribution.trim() !== "" &&
    rights;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Import a Bible"
      onDragOver={(e) => {
        e.preventDefault();
        if (phase.kind === "pick") setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const file = e.dataTransfer.files?.[0];
        if (file && phase.kind === "pick") void read(file);
      }}
    >
      <div className="flex max-h-full w-full max-w-2xl flex-col rounded-lg border border-border bg-background shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <div>
            <div className="font-semibold">Import a Bible</div>
            <div className="text-xs text-muted-foreground">
              {phase.kind === "pick" || phase.kind === "reading"
                ? "Use a translation your church already has."
                : `${phase.fileName} — nothing is installed until you press Install.`}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={phase.kind === "installing"}
            aria-label="Close"
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="size-5" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {(phase.kind === "pick" || phase.kind === "reading") && (
            <div className="space-y-4">
              <button
                type="button"
                onClick={() => void choose()}
                disabled={busy}
                className={`flex w-full flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${
                  dragging ? "border-emerald-500 bg-emerald-500/10" : "border-border hover:bg-muted/40"
                }`}
              >
                {phase.kind === "reading" ? (
                  <>
                    <Loader2 className="size-6 animate-spin text-muted-foreground" />
                    <span className="text-sm">Reading {phase.fileName}…</span>
                  </>
                ) : (
                  <>
                    <FileUp className="size-6 text-muted-foreground" />
                    <span className="text-sm font-medium">Choose a Bible file, or drop it here</span>
                    <span className="text-xs text-muted-foreground">{FORMATS}</span>
                  </>
                )}
              </button>
              {phase.kind === "pick" && phase.error && (
                <p role="alert" className="text-sm text-destructive">
                  {phase.error}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                Bibles from ProPresenter, EasyWorship and SWORD modules can&rsquo;t be read.
                If your translation came with one of those, ask its publisher for one of
                the formats above. Free, public-domain translations are under
                &ldquo;Download&rdquo; in Settings, with no file needed.
              </p>
            </div>
          )}

          {(phase.kind === "review" || phase.kind === "installing") && (
            <Review
              report={phase.report}
              name={name}
              abbreviation={abbreviation}
              attribution={attribution}
              rights={rights}
              disabled={phase.kind === "installing"}
              onName={setName}
              onAbbreviation={setAbbreviation}
              onAttribution={setAttribution}
              onRights={setRights}
            />
          )}
          {error && phase.kind === "review" && (
            <p role="alert" className="mt-3 text-sm text-destructive">
              {error}
            </p>
          )}
        </div>

        <footer className="flex justify-end gap-2 border-t border-border px-5 py-3">
          <Button type="button" variant="ghost" onClick={onClose} disabled={phase.kind === "installing"}>
            Cancel
          </Button>
          {(phase.kind === "review" || phase.kind === "installing") && (
            <Button type="button" onClick={() => void install()} disabled={!ready}>
              {phase.kind === "installing" && <Loader2 className="mr-1.5 size-4 animate-spin" />}
              Install
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}

function Review({
  report,
  name,
  abbreviation,
  attribution,
  rights,
  disabled,
  onName,
  onAbbreviation,
  onAttribution,
  onRights,
}: {
  report: ScriptureImportReport;
  name: string;
  abbreviation: string;
  attribution: string;
  rights: boolean;
  disabled: boolean;
  onName: (v: string) => void;
  onAbbreviation: (v: string) => void;
  onAttribution: (v: string) => void;
  onRights: (v: boolean) => void;
}) {
  const sections = new Set(report.books.map((b) => b.section));
  const deuterocanon = report.deuterocanon ?? [];
  const missing = report.missing ?? [];
  const incomplete = report.incomplete ?? [];
  const unmatched = report.unmatched ?? [];
  const contents = [
    sections.has("ot") && "Old Testament",
    sections.has("nt") && "New Testament",
    deuterocanon.length > 0 && `${deuterocanon.length} deuterocanonical books`,
  ].filter(Boolean);
  const problems = [
    ...(report.warnings ?? []),
    ...(missing.length > 0 ? [`Missing: ${listOf(missing)}.`] : []),
    ...(incomplete.length > 0 ? [`Much shorter than expected — ${incomplete.join("; ")}.`] : []),
    ...(unmatched.length > 0
      ? [
          `Not recognised: ${listOf(unmatched)}. Kept under the file's own name, so ` +
            "they can still be looked up by that name.",
        ]
      : []),
  ];

  return (
    <div className="space-y-4">
      <div>
        <div className="text-sm">
          <span className="font-medium">{report.verse_count.toLocaleString()} verses</span> in{" "}
          {report.books.length} books · {report.format}
        </div>
        {contents.length > 0 && (
          <div className="text-xs text-muted-foreground">{contents.join(" · ")}</div>
        )}
      </div>

      {problems.length > 0 && (
        <ul className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}

      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">Books found</summary>
        <BookTable books={report.books} />
      </details>

      <div className="grid grid-cols-[1fr_8rem] gap-3">
        <Field label="Name">
          <input
            aria-label="Name"
            value={name}
            disabled={disabled}
            onChange={(e) => onName(e.target.value)}
            className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
            placeholder="New International Version"
          />
        </Field>
        <Field label="Short name">
          <input
            aria-label="Short name"
            value={abbreviation}
            disabled={disabled}
            maxLength={12}
            onChange={(e) => onAbbreviation(e.target.value)}
            className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
            placeholder="NIV"
          />
        </Field>
      </div>
      <Field
        label="Credit line"
        hint="The copyright notice the publisher asks for. Most also ask for the short name beside each verse, which “Show translation” in Settings does."
      >
        <textarea
          aria-label="Credit line"
          value={attribution}
          disabled={disabled}
          onChange={(e) => onAttribution(e.target.value)}
          rows={2}
          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          placeholder="Scripture taken from … Used by permission. All rights reserved."
        />
      </Field>

      <label className="flex items-start gap-2 rounded-md border border-border px-3 py-2 text-sm">
        <input
          type="checkbox"
          aria-label="Our church has the right to use this translation"
          checked={rights}
          disabled={disabled}
          onChange={(e) => onRights(e.target.checked)}
          className="mt-0.5 size-4 accent-emerald-500"
        />
        <span>
          <span className="font-medium">Our church has the right to use this translation.</span>
          <span className="mt-0.5 block text-xs text-muted-foreground">
            Public-domain Bibles are free to use. Most modern translations (NIV, ESV, NLT…)
            are licensed, and the publisher&rsquo;s terms decide how a church may show them.
            This Bible stays on this computer: it isn&rsquo;t uploaded or shared with other
            devices.
          </span>
        </span>
      </label>
    </div>
  );
}

function BookTable({ books }: { books: ScriptureImportBook[] }) {
  return (
    <table className="mt-2 w-full">
      <thead className="text-left text-muted-foreground">
        <tr>
          <th className="py-1 pr-2 font-medium">Book</th>
          <th className="py-1 pr-2 font-medium">In the file</th>
          <th className="py-1 pr-2 text-right font-medium">Chapters</th>
          <th className="py-1 pr-2 text-right font-medium">Verses</th>
          <th className="py-1 font-medium">Recognised by</th>
        </tr>
      </thead>
      <tbody>
        {books.map((b) => (
          <tr key={`${b.code}-${b.source_name}`} className="border-t border-border/50">
            <td className="py-1 pr-2">{b.name}</td>
            <td className="py-1 pr-2 text-muted-foreground">
              {b.source_name !== b.name ? b.source_name : ""}
            </td>
            <td className="py-1 pr-2 text-right tabular-nums">{b.chapters}</td>
            <td className="py-1 pr-2 text-right tabular-nums">{b.verses.toLocaleString()}</td>
            <td
              className={
                !b.matched_by
                  ? "py-1 text-destructive"
                  : b.matched_by === "shape"
                    ? "py-1 text-amber-500"
                    : "py-1 text-muted-foreground"
              }
            >
              {MATCHED_BY[b.matched_by ?? ""] ?? b.matched_by}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium">{label}</div>
      {children}
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

function listOf(items: string[], max = 8): string {
  if (items.length <= max) return items.join(", ");
  return `${items.slice(0, max).join(", ")} and ${items.length - max} more`;
}

/** Fill the form from what the file says about itself. Every field is editable. */
export function suggestDetails(
  report: ScriptureImportReport,
  fileName: string,
): { name: string; abbreviation: string; attribution: string } {
  const fromFile = fileName.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
  const name = (report.title ?? "").trim() || fromFile;
  const abbreviation =
    (report.abbreviation ?? "").trim().slice(0, 12).toUpperCase() ||
    name
      .split(/\s+/)
      .filter((w) => /^[A-Za-z]/.test(w) && !/^(of|the|and)$/i.test(w))
      .map((w) => w.charAt(0).toUpperCase())
      .join("")
      .slice(0, 6);
  const rights = (report.rights ?? "").trim();
  const attribution = !rights
    ? ""
    : /^public domain$/i.test(rights)
      ? `${name} (public domain)`
      : rights;
  return { name, abbreviation, attribution };
}
