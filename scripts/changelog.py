#!/usr/bin/env python3
"""Read CHANGELOG.md for the release script and the download site.

    scripts/changelog.py notes 0.0.4        the entry's bullets, as plain text
    scripts/changelog.py has 0.0.4          exit 0 if 0.0.4 has an entry
    scripts/changelog.py page site/changelog.html [v0.0.5,v0.0.4 [PERF_DIR [PUBLISHED]]]
                                            fill the page's release list in place;
                                            the tags have a Windows installer,
                                            PERF_DIR/<tag>/perf.json (from the
                                            release) becomes a performance card,
                                            and only the PUBLISHED tags (a comma
                                            list) get download links
    scripts/changelog.py windows site/index.html v0.0.5,v0.0.4
                                            point the Windows download at the
                                            first tag, or remove it if none
    scripts/changelog.py add-platform latest.json windows-x86_64 URL SIGFILE
                                            add a platform to an update manifest
    scripts/changelog.py manifest OUT 0.0.4 BASE_URL
                                            write OUT/latest.json for the in-app
                                            updater, from OUT/sigs/<mac>.sig
    scripts/changelog.py release 0.0.4      rename "## Unreleased" to the version

Entries look like:

    ## 0.0.4 — 2 October 2026

    - One line per change. **Bold** and `code` work.

"## Unreleased" is skipped everywhere: the page lists released versions only.
"""

from __future__ import annotations

import datetime
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASES = "https://github.com/martinmain93/church-slides-releases/releases"
# The site's download buttons go through the stats Worker (stats/), which
# counts the download and redirects to the file in RELEASES.
DOWNLOADS = "https://church-slides-stats.martinmain93.workers.dev/dl"
INSTALLERS = [("Apple silicon", "apple-silicon"), ("Intel", "intel")]

HEADING = re.compile(r"^##\s+(\d+\.\d+\.\d+)\s*(?:[—–-]\s*(.+?))?\s*$")
START, END = "<!-- releases:start -->", "<!-- releases:end -->"


def entries(text: str) -> list[tuple[str, str, list[str]]]:
    """(version, date, bullets), newest first, as written in the file."""
    out: list[tuple[str, str, list[str]]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            m = HEADING.match(line)
            current = None
            if m:
                current = []
                out.append((m.group(1), m.group(2) or "", current))
        elif current is not None:
            if re.match(r"^\s*[-*]\s+", line):
                current.append(re.sub(r"^\s*[-*]\s+", "", line).strip())
            elif line.startswith("  ") and current:  # a wrapped bullet
                current[-1] += " " + line.strip()
    return out


def find(version: str) -> tuple[str, str, list[str]] | None:
    return next((e for e in entries(CHANGELOG.read_text()) if e[0] == version), None)


def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", out)


#: Platforms in a release's perf.json (scripts/perf_report.py), as the card names them.
PERF_PLATFORMS = {
    "macos-apple-silicon": "Mac (Apple silicon)",
    "macos-intel-rosetta": "Mac (Intel)",
    "windows-x64-ci": "Windows",
}


def _num(v: float | None, unit: str, digits: int = 0) -> str:
    return "–" if v is None else f"{v:.{digits}f}{unit}"


def perf_card(perf: dict) -> str:
    """The release's engine performance, measured by its build on the
    fixture service: a few headline numbers per platform."""
    rows = []
    for key, name in PERF_PLATFORMS.items():
        p = perf.get(key)
        if not p:
            continue
        s = p.get("stats") or {}
        # "Darwin 25.4.0 · Apple M1 · 8 cores · 16 GB": the chip when it has a
        # readable name (Windows reports "AMD64 Family 25 Model 17 ..."), then
        # cores and memory.
        parts = (p.get("machine") or "").split(" · ")
        named = [parts[1]] if len(parts) > 1 and "Family" not in parts[1] else []
        machine = html.escape(" · ".join(named + parts[2:4]))
        if p.get("error"):
            cells = '<td colspan="5">the test run failed</td>'
        elif p.get("quick"):
            cells = (
                '<td colspan="4">smoke test (crashes and leaks only)</td>'
                f"<td>{_num(s.get('soak.speed_x_realtime'), '×', 1)}</td>"
            )
        else:
            on_time = p.get("slides_on_time")
            cells = (
                f"<td>{_num(s.get('live.cpu_percent_mean'), '%')}</td>"
                f"<td>{_num(s.get('live.private_mb_peak'), ' MB')}</td>"
                f"<td>{_num(s.get('live.asr_ms_p95'), ' ms')}</td>"
                f"<td>{f'{on_time[0]}/{on_time[1]}' if on_time else '–'}</td>"
                f"<td>{_num(s.get('soak.speed_x_realtime'), '×', 1)}</td>"
            )
        warns = p.get("warnings") or []
        flag = ""
        if warns:
            plural = "s" if len(warns) != 1 else ""
            flag = (
                f' <span class="warn" title="{html.escape(", ".join(warns))}">'
                f"{len(warns)} warning{plural}</span>"
            )
        rows.append(
            f'          <tr><th>{name}{flag}<span class="machine">{machine}</span></th>{cells}</tr>'
        )
    if not rows:
        return ""
    return (
        '      <details class="perf">\n'
        "        <summary>Performance</summary>\n"
        "        <table>\n"
        "          <tr><th></th><th>CPU</th><th>Memory</th><th>Speech pass</th>"
        "<th>Slides on time</th><th>Speed</th></tr>\n" + "\n".join(rows) + "\n        </table>\n"
        '        <p class="perf-note">Measured by the build: the AI engine following a 100-second '
        "recorded song in a four-song setlist. CPU is a share of one core; memory is the peak; "
        "speech pass is the slowest 5%; speed is how much faster than real time it can go.</p>\n"
        "      </details>\n"
    )


def render(
    items: list[tuple[str, str, list[str]]],
    windows: set[str] = frozenset(),
    perf_dir: Path | None = None,
    published: set[str] | None = None,
) -> str:
    """`published`: the tags still on the releases repo. A version that was
    taken down keeps its notes but loses its links; None links every one."""
    blocks = []
    for i, (version, date, bullets) in enumerate(items):
        tag = f"v{version}"
        latest = ' <span class="badge">Latest</span>' if i == 0 else ""
        when = f'<span class="date">{html.escape(date)}</span>' if date else ""
        links = " ".join(
            f'<a class="dl" href="{RELEASES}/download/{tag}/Church-Slides-{slug}.dmg">{label}</a>'
            for label, slug in INSTALLERS
        )
        if tag in windows:
            links += (
                f' <a class="dl" href="{RELEASES}/download/{tag}/Church-Slides-windows-setup.exe">Windows</a>'
                '<span class="exp">experimental</span>'
            )
        if published is None or tag in published:
            downloads = f'      <p class="downloads">Download {version}: {links}</p>\n'
        else:
            downloads = f'      <p class="downloads">{version} is no longer available to download.</p>\n'
        lis = "\n".join(f"        <li>{inline(b)}</li>" for b in bullets)
        perf_file = perf_dir / tag / "perf.json" if perf_dir else None
        card = ""
        if perf_file and perf_file.exists():
            card = perf_card(json.loads(perf_file.read_text(encoding="utf-8")))
        blocks.append(
            f'    <section class="release" id="{tag}">\n'
            f'      <div class="release-head"><h2>{version}{latest}</h2>{when}</div>\n'
            f"      <ul>\n{lis}\n      </ul>\n"
            f"{card}"
            f"{downloads}"
            f"    </section>"
        )
    return "\n".join(blocks)


def fill_page(
    path: Path,
    windows: set[str] = frozenset(),
    perf_dir: Path | None = None,
    published: set[str] | None = None,
) -> None:
    page = path.read_text()
    a, b = page.index(START) + len(START), page.index(END)
    body = render(entries(CHANGELOG.read_text()), windows, perf_dir, published)
    path.write_text(page[:a] + "\n" + body + "\n    " + page[b:])


def stamp_windows(path: Path, tags: list[str]) -> None:
    """Point index.html's Windows download at tags[0], or drop it."""
    page = path.read_text()
    a, b = (
        page.index("<!-- windows:start -->"),
        page.index("<!-- windows:end -->") + len("<!-- windows:end -->"),
    )
    if not tags:
        path.write_text(page[:a] + page[b:])
        return
    block = re.sub(
        r'(id="windows-link" href=")[^"]*',
        rf"\g<1>{DOWNLOADS}/{tags[0]}/Church-Slides-windows-setup.exe",
        page[a:b],
    )
    block = re.sub(r'(<span id="windows-version">)[^<]*', rf"\g<1>{tags[0][1:]}", block)
    path.write_text(page[:a] + block + page[b:])


def add_platform(manifest: Path, platform: str, url: str, sig: Path) -> None:
    data = json.loads(manifest.read_text())
    data["platforms"][platform] = {"signature": sig.read_text().strip(), "url": url}
    manifest.write_text(json.dumps(data, indent=2) + "\n")


def tag_list(arg: str) -> list[str]:
    """Newest first, as the GitHub API lists releases."""
    return [t for t in arg.split(",") if t]


def write_manifest(out: Path, version: str, base_url: str) -> None:
    """The updater's latest.json (Tauri's static-JSON format)."""
    e = find(version)
    platforms = {
        platform: {
            "signature": (out / "sigs" / f"{slug}.sig").read_text().strip(),
            "url": f"{base_url}/Church-Slides-{slug}.app.tar.gz",
        }
        for platform, slug in [("darwin-aarch64", "apple-silicon"), ("darwin-x86_64", "intel")]
    }
    manifest = {
        "version": version,
        "notes": "\n".join(f"- {b}" for b in e[2]) if e else "",
        "pub_date": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }
    (out / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def name_release(version: str, date: str) -> bool:
    """Turn the Unreleased heading into this version's. False if there isn't one."""
    text = CHANGELOG.read_text()
    new, n = re.subn(
        r"^## Unreleased[ \t]*\n+",
        f"## Unreleased\n\n## {version} — {date}\n\n",
        text,
        count=1,
        flags=re.M,
    )
    if n:
        CHANGELOG.write_text(new)
    return bool(n)


def main(argv: list[str]) -> int:
    match argv:
        case ["notes", version]:
            e = find(version)
            if not e:
                return 1
            print("\n".join(f"- {b}" for b in e[2]))
        case ["has", version]:
            return 0 if find(version) and find(version)[2] else 1
        case ["page", path]:
            fill_page(Path(path))
        case ["page", path, tags]:
            fill_page(Path(path), set(tag_list(tags)))
        case ["page", path, tags, perf_dir]:
            fill_page(Path(path), set(tag_list(tags)), Path(perf_dir))
        case ["page", path, tags, perf_dir, published]:
            fill_page(Path(path), set(tag_list(tags)), Path(perf_dir), set(tag_list(published)))
        case ["windows", path, tags]:
            stamp_windows(Path(path), tag_list(tags))
        case ["add-platform", manifest, platform, url, sig]:
            add_platform(Path(manifest), platform, url, Path(sig))
        case ["manifest", out, version, base_url]:
            write_manifest(Path(out), version, base_url)
        case ["release", version]:
            if find(version):
                return 0
            date = datetime.date.today().strftime("%-d %B %Y")
            if not name_release(version, date):
                print("CHANGELOG.md has no '## Unreleased' section to release", file=sys.stderr)
                return 1
        case _:
            print(__doc__, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
