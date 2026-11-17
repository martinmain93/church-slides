"""Download the public sung-music and speech-only sets used for engine research.

Every file is pinned in `public_worship_manifest.json` (beside this script) by URL,
licence and SHA-256, so two developers get byte-identical data. Files already on
disk with the right checksum are skipped, so rerunning is cheap and resumes a
broken download.

    python research/data/fetch_public_worship.py                 # everything (~930 MB)
    python research/data/fetch_public_worship.py --sets speech    # just the talk-only set
    python research/data/fetch_public_worship.py --out /data/pw --list

Standard library only: no project environment needed. See README.md here for what
each set is for and the licence terms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "public_worship_manifest.json"
# Kept outside the repo (it's ~1 GB): a sibling folder of the checkout.
DEFAULT_OUT = HERE.parents[2] / "auto-presenter-research" / "public_worship"
SETS = ("librivox", "jamendo", "jamendolyrics", "speech")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(entry: dict, out: Path) -> str:
    dest = out / entry["path"]
    if dest.exists() and sha256(dest) == entry["sha256"]:
        return "ok"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(entry["url"], headers={"User-Agent": "church-slides-research-fetch"})
    with urllib.request.urlopen(req, timeout=300) as r, tmp.open("wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    got = sha256(tmp)
    if got != entry["sha256"]:
        tmp.unlink()
        raise RuntimeError(f"checksum mismatch (upstream file changed?): {got[:12]} != {entry['sha256'][:12]}")
    tmp.replace(dest)
    return "downloaded"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"Where to put the data (default {DEFAULT_OUT}).")
    ap.add_argument("--sets", nargs="+", choices=SETS, default=list(SETS))
    ap.add_argument("--jobs", type=int, default=6, help="Parallel downloads.")
    ap.add_argument("--list", action="store_true", help="Show what would be fetched and exit.")
    args = ap.parse_args()

    entries = [e for e in json.loads(MANIFEST.read_text()) if e["set"] in args.sets]
    total = sum(e["bytes"] for e in entries)
    print(f"{len(entries)} files, {total / 1e6:.0f} MB -> {args.out}")
    if args.list:
        for e in entries:
            print(f"  [{e['set']}] {e['path']}  ({e['license']})")
        return 0

    failed = []
    done = 0
    with ThreadPoolExecutor(args.jobs) as pool:
        futures = {pool.submit(fetch, e, args.out): e for e in entries}
        for fut in as_completed(futures):
            e = futures[fut]
            done += 1
            try:
                status = fut.result()
            except Exception as exc:  # report and keep going; rerun resumes
                failed.append((e["path"], exc))
                status = f"FAILED: {exc}"
            if status != "ok":
                print(f"[{done}/{len(entries)}] {e['path']}: {status}", flush=True)

    readme = HERE / "README.md"
    if readme.exists():
        (args.out / "README.md").write_text(readme.read_text())
    if failed:
        print(f"\n{len(failed)} failed; rerun to retry:", file=sys.stderr)
        for path, exc in failed:
            print(f"  {path}: {exc}", file=sys.stderr)
        return 1
    print("all files present and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
