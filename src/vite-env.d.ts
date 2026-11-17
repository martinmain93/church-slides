/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The stats Worker; set only for official release builds (see `src/lib/usage.ts`). */
  readonly VITE_CHURCH_SLIDES_STATS_URL?: string;
}
