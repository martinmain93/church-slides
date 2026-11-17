/**
 * Read the files out of a ZIP: ProPresenter `.proplaylist` and OpenLP `.osz`.
 *
 * Walks the local file headers front to back instead of trusting the central
 * directory — ProPresenter writes ZIP64 archives whose end-of-directory
 * offset is wrong (research/spikes/competitor_import/README.md). Stored and
 * deflated entries; anything else is skipped.
 */

import { inflateSync } from "fflate";

const LOCAL_HEADER = 0x04034b50;

export function isZip(bytes: Uint8Array): boolean {
  return bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x03 && bytes[3] === 0x04;
}

export function readZip(bytes: Uint8Array): Map<string, Uint8Array> {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Map<string, Uint8Array>();
  let i = 0;
  while (i + 30 <= bytes.length && view.getUint32(i, true) === LOCAL_HEADER) {
    const flags = view.getUint16(i + 6, true);
    const method = view.getUint16(i + 8, true);
    let size = view.getUint32(i + 18, true);
    const nameLen = view.getUint16(i + 26, true);
    const extraLen = view.getUint16(i + 28, true);
    const name = new TextDecoder().decode(bytes.subarray(i + 30, i + 30 + nameLen));
    const extra = i + 30 + nameLen;
    if (size === 0xffffffff) size = zip64Size(view, extra, extraLen) ?? size;
    const start = extra + extraLen;
    if (flags & 0x08 && size === 0) {
      // Sizes only in a trailing descriptor: can't walk on from here.
      throw new Error("Unsupported ZIP layout");
    }
    const data = bytes.subarray(start, start + size);
    if (!name.endsWith("/")) {
      if (method === 0) out.set(name, data);
      else if (method === 8) out.set(name, inflateSync(data));
    }
    i = start + size;
    if (flags & 0x08) i += view.getUint32(i, true) === 0x08074b50 ? 16 : 12;
  }
  return out;
}

/** The compressed size from a ZIP64 extra field (id 0x0001: original, then compressed). */
function zip64Size(view: DataView, at: number, len: number): number | null {
  for (let p = at; p + 4 <= at + len; ) {
    const id = view.getUint16(p, true);
    const size = view.getUint16(p + 2, true);
    if (id === 0x0001 && size >= 16) return Number(view.getBigUint64(p + 12, true));
    p += 4 + size;
  }
  return null;
}
