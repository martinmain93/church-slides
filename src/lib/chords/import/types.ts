import type { ChordChart } from "@/lib/chords/chart";

export type ImportFormat =
  | "chordpro"
  | "onsong"
  | "chords-over-lyrics"
  | "ultimate-guitar"
  | "plain-lyrics"
  | "openlyrics"
  | "opensong"
  | "songselect-usr"
  | "songselect-text"
  | "pdf"
  | "propresenter"
  | "freeshow"
  | "openlp";

export const FORMAT_LABELS: Record<ImportFormat, string> = {
  chordpro: "ChordPro",
  onsong: "OnSong",
  "chords-over-lyrics": "Chords over lyrics",
  "ultimate-guitar": "Ultimate Guitar",
  "plain-lyrics": "Lyrics only",
  openlyrics: "OpenLyrics (OpenLP)",
  opensong: "OpenSong",
  "songselect-usr": "SongSelect (.usr)",
  "songselect-text": "SongSelect chord sheet",
  pdf: "PDF chord sheet",
  propresenter: "ProPresenter",
  freeshow: "FreeShow",
  openlp: "OpenLP",
};

/** One song found in an imported file. */
export interface ImportedChartSong {
  title: string;
  artist: string | null;
  ccli: string | null;
  copyright: string | null;
  /**
   * The church's CCLI licence number printed on the sheet (SongSelect adds
   * it to every download). Not the song's — kept for licence reporting.
   */
  ccliLicense?: string | null;
  chart: ChordChart;
  /**
   * The arrangement the source spelled out, as section labels in play order
   * ("Verse 1", "Chorus", "Verse 2", "Chorus"). Null when the source gave
   * none and the chart's document order *is* the arrangement.
   */
  order: string[] | null;
  format: ImportFormat;
  /** Things the importer had to guess or drop, for the preview. */
  warnings: string[];
}

/**
 * A lead sheet: notation (melody, chords, lyrics) read by the engine. Not a
 * chart and not slides — it becomes a *reference* on its song, which the
 * score follower compares the live band against.
 */
export interface ImportedLeadSheet {
  title: string | null;
  ccli: string | null;
  key: string | null;
  copyright: string | null;
  barCount: number;
  /** Bars whose notes don't add up to the metre (kept; the rest still place). */
  problemBars: number;
  /** Section labels printed on the sheet, in order ("VERSE 1", "CHORUS"…). */
  sections: string[];
  readerVersion: number;
  /** The engine's parsed score, stored verbatim as the reference. */
  scoreJson: string;
}

export interface ImportFileResult {
  fileName: string;
  songs: ImportedChartSong[];
  /** Set when the file is a lead sheet rather than a chart (then `songs` is empty). */
  leadSheet?: ImportedLeadSheet | null;
  /** Set when the file couldn't be read as any known format. */
  error: string | null;
  /** For an unreadable file: its shape, never its contents (`fileSignature`). */
  signature?: string;
}
