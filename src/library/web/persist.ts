/**
 * Where the web app keeps its copy of a library between visits: one
 * IndexedDB database per server and organization, holding key → value
 * pairs. `memoryPersist` is the same thing in memory, for tests and for
 * browsers with storage turned off (the library is then downloaded again on
 * every visit, and changes not yet sent are lost when the tab closes).
 */

export interface Persist {
  load(): Promise<[string, unknown][]>;
  /** Write several keys in one go; `null` removes a key. */
  put(entries: [string, unknown][]): Promise<void>;
  /** Forget everything (signing out). */
  clear(): Promise<void>;
  /** Let go of the database, so it can be deleted. */
  close?(): void;
}

export function memoryPersist(): Persist {
  const map = new Map<string, unknown>();
  return {
    load: async () => [...map.entries()].map(([k, v]) => [k, structuredClone(v)]),
    put: async (entries) => {
      for (const [k, v] of entries) {
        if (v === null) map.delete(k);
        else map.set(k, structuredClone(v));
      }
    },
    clear: async () => map.clear(),
  };
}

const STORE = "kv";

function request<T>(r: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

function openDb(name: string): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(name, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE);
    r.onsuccess = () => {
      const db = r.result;
      // Another tab is deleting it (signing out) or upgrading it: let go, or
      // it waits on this tab forever.
      db.onversionchange = () => db.close();
      resolve(db);
    };
    r.onerror = () => reject(r.error);
    r.onblocked = () => reject(new Error("The library is open in an older tab."));
  });
}

/** Delete a database. Resolves once it's gone, or once it's queued behind a
 * tab that still has it open (it goes when that tab lets go), so signing out
 * never waits on another tab. */
function deleteDb(name: string): Promise<void> {
  return new Promise((resolve) => {
    const r = indexedDB.deleteDatabase(name);
    r.onsuccess = () => resolve();
    r.onerror = () => resolve();
    r.onblocked = () => resolve();
  });
}

/** The database name for one organization's library on one server. */
export function libraryDbName(server: string, orgId: string): string {
  return `church-slides.library.${new URL(server).host}.${orgId}`;
}

/** IndexedDB, falling back to memory where it can't be opened. */
export async function browserPersist(name: string): Promise<Persist> {
  let db: IDBDatabase;
  try {
    db = await openDb(name);
  } catch {
    return memoryPersist();
  }
  return {
    async load() {
      const tx = db.transaction(STORE, "readonly");
      const store = tx.objectStore(STORE);
      const [keys, values] = await Promise.all([request(store.getAllKeys()), request(store.getAll())]);
      return keys.map((k, i) => [String(k), values[i]]);
    },
    put(entries) {
      if (entries.length === 0) return Promise.resolve();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, "readwrite");
        const store = tx.objectStore(STORE);
        for (const [k, v] of entries) {
          if (v === null) store.delete(k);
          else store.put(v, k);
        }
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
      });
    },
    async clear() {
      db.close();
      await deleteDb(name);
    },
    close() {
      db.close();
    },
  };
}

/** Forget every library this browser keeps (signing out). `known` are names
 * to delete even where the browser can't list its databases. */
export async function clearAllLibraries(known: string[] = []): Promise<void> {
  if (typeof indexedDB === "undefined") return;
  const listed = indexedDB.databases ? await indexedDB.databases().catch(() => []) : [];
  const names = new Set([...known, ...listed.map((d) => d.name ?? "")]);
  await Promise.all([...names].filter((n) => n.startsWith("church-slides.library.")).map(deleteDb));
}
