"""Generate a solid-color 512x512 PNG for use as a Tauri placeholder icon.

Stdlib-only — no Pillow required. Replace with a real icon before release via
`pnpm tauri icon path/to/source.png`.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def make_solid_png(size: int, rgba: tuple[int, int, int, int]) -> bytes:
    color = bytes(rgba)
    scanline = b"\x00" + color * size
    raw = scanline * size
    idat = zlib.compress(raw, 9)

    def chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("src-tauri/icons/icon.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    # zinc-900 #18181b with full alpha
    out.write_bytes(make_solid_png(512, (24, 24, 27, 255)))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
