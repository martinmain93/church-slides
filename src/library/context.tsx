/** The library a view works on: `useLibrary()` inside a `LibraryProvider`. */

import { createContext, useContext, type ReactNode } from "react";

import type { LibrarySource } from "@/library/source";

const LibraryContext = createContext<LibrarySource | null>(null);

export function LibraryProvider({ source, children }: { source: LibrarySource; children: ReactNode }) {
  return <LibraryContext.Provider value={source}>{children}</LibraryContext.Provider>;
}

export function useLibrary(): LibrarySource {
  const source = useContext(LibraryContext);
  if (!source) throw new Error("useLibrary() needs a LibraryProvider");
  return source;
}
