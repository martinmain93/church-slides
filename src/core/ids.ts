/**
 * Record ids made by a client, in the desktop's shape: `song_<ulid>`,
 * `setlist_<ulid>`, `item_<ulid>` (lower case). Sortable by time, and within
 * the protocol's rule (1–100 of `A-Z a-z 0-9 _ -`).
 */

const ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"; // Crockford base 32, lower case

export function ulid(now: number = Date.now()): string {
  let time = "";
  let t = Math.max(0, Math.floor(now));
  for (let i = 0; i < 10; i++) {
    time = ALPHABET[t % 32]! + time;
    t = Math.floor(t / 32);
  }
  const random = crypto.getRandomValues(new Uint8Array(16));
  let tail = "";
  for (const b of random) tail += ALPHABET[b % 32];
  return time + tail;
}

export const newId = (prefix: "song" | "setlist" | "item" | "reading" | "op") => `${prefix}_${ulid()}`;
