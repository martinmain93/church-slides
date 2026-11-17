"""Write THIRD-PARTY-NOTICES.txt: the licence of everything the app ships.

    uv run --no-project python scripts/third_party_notices.py [--target TRIPLE]

MIT, BSD and Apache-2.0 all ask for their copyright and licence text (and
Apache for NOTICE files) to travel with binaries, so the app bundles this file
as a Tauri resource and Settings opens it ("Open-source licences"). It covers:

  1. Models and data: the chord, pitch and library search models, the pronouncing dictionary,
     Silero VAD, and the Whisper weights the app downloads on first use.
  2. The Rust crates linked into the app, for the target being built
     (`cargo metadata`, following normal dependencies from the app crate).
  3. The npm packages bundled into the interface and the band page, as the
     production build recorded them (scripts/vite-plugin-bundled-packages.mjs).
  4. The AI engine: every Python distribution with a file in the frozen
     engine, its CPython runtime and PyInstaller's bootloader, as
     scripts/build_sidecar.py recorded them (`write_engine_notices`). Left
     out, and said so, when no engine is staged (the no-AI build).

Licence texts come from the packages themselves (LICENSE*, COPYING*, NOTICE*
and the like). A package that ships none gets the standard text for its
declared licence from scripts/notices/licenses, with its authors as the
copyright holder. Identical texts are printed once, listing their packages.

`pnpm tauri build` runs this after `pnpm build` (tauri.conf.json's
beforeBuildCommand), so every installer carries notices for exactly what it
bundles. Dev builds get a placeholder from src-tauri/build.rs.

Licences that could stop the app being distributed as it is — GPL, LGPL,
AGPL, non-commercial, proprietary — are flagged. Each flagged package must
appear in scripts/notices/reviewed.json: "accepted" with the reason, or "open"
(printed as a warning on every build until someone decides). A flagged
package that isn't listed fails the build.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICES = ROOT / "scripts" / "notices"
OUT = ROOT / "src-tauri" / "resources" / "THIRD-PARTY-NOTICES.txt"
JS_RECORDS = ROOT / "build" / "notices"
ENGINE_RECORD = ROOT / "sidecar" / "build" / "engine-notices.json"
STAGED_ENGINE = ROOT / "src-tauri" / "resources" / "sidecar"
MODEL_LICENSES = ROOT / "sidecar" / "packaging" / "model_licenses"

#: Licence files, by name, in a package's folder (or a wheel's dist-info).
LICENSE_FILE = re.compile(
    r"^(licen[cs]e|copying|notice|copyright|unlicense|thirdpartynotices|third[-_]party[-_]notices)"
    r"([-_.].*)?$",
    re.I,
)
#: Preference among the alternatives of an `A OR B` licence: the shortest
#: text that still names the copyright holder.
PREFERRED = ["MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Zlib", "Apache-2.0", "MPL-2.0"]
#: Licence names (as packages declare them, SPDX or not) that need a decision.
COPYLEFT = re.compile(
    r"\b(A?GPL|LGPL|GNU (Lesser |Library |Affero )?General Public|SSPL|BUSL|Commons[- ]Clause|"
    r"CC-BY-NC|NonCommercial|non-commercial|proprietary|EULA)",
    re.I,
)
#: The same, in a licence's text. Only the title lines, so a permissive
#: licence that merely mentions the GPL doesn't trip it.
COPYLEFT_TEXT = re.compile(
    r"GNU (LESSER |LIBRARY |AFFERO )?GENERAL PUBLIC LICENSE\s*\n\s*Version|"
    r"Attribution-NonCommercial|for non-commercial (use|purposes) only",
    re.I,
)


@dataclass
class Package:
    ecosystem: str  # "rust", "npm", "python", "runtime" or "model"
    name: str
    version: str = ""
    license: str = ""
    texts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    url: str = ""

    @property
    def key(self) -> str:
        return f"{self.ecosystem}:{self.name}"


# --- licence texts -------------------------------------------------------------


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def normalize(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n") + "\n"


def same_text_key(text: str) -> str:
    """Texts that differ only in layout are printed once."""
    return " ".join(text.split()).lower()


def license_files(folder: Path, recursive: bool = False) -> list[Path]:
    if not folder.is_dir():
        return []
    found = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(
        p for p in found
        if p.is_file() and LICENSE_FILE.match(p.name)
        and p.suffix.lower() not in (".py", ".pyc", ".pyi", ".rs", ".js", ".ts", ".html", ".json")
    )


#: Which licence a file is, from its name (LICENSE-MIT, LICENSE.APACHE, ...).
_FILE_LICENSE = [
    (re.compile(r"apache", re.I), "Apache-2.0"),
    (re.compile(r"(^|[-_.])mit([-_.]|$)", re.I), "MIT"),
    (re.compile(r"(^|[-_.])bsd", re.I), "BSD"),
    (re.compile(r"zlib", re.I), "Zlib"),
    (re.compile(r"(^|[-_.])isc([-_.]|$)", re.I), "ISC"),
    (re.compile(r"unlicense", re.I), "Unlicense"),
    (re.compile(r"boost|bsl", re.I), "BSL-1.0"),
    (re.compile(r"(^|[-_.])l?gpl", re.I), "GPL"),
]


def elected_files(files: list[Path], expression: str) -> list[Path]:
    """For `A OR B`, drop the files that are clearly the alternative we don't
    take (LICENSE-APACHE when we go with MIT). Unnamed files and NOTICEs stay."""
    chosen = spdx_ids(expression)
    if len(chosen) != 1 or " OR " not in expression.replace("/", " OR "):
        return files
    keep = []
    for f in files:
        kind = next((lic for pat, lic in _FILE_LICENSE if pat.search(f.name)), None)
        if kind is None or chosen[0].startswith(kind) or re.match(r"notice", f.name, re.I):
            keep.append(f)
    return keep or files


def spdx_ids(expression: str) -> list[str]:
    """The licences to reproduce for an expression: one alternative of an
    `OR` (the preferred one), every licence of an `AND`."""
    expr = expression.replace("/", " OR ").strip()
    if not expr:
        return []
    if "(" in expr:
        alternatives = [expr]
    else:
        alternatives = [a.strip(" ()") for a in re.split(r"\s+OR\s+", expr)]
    for want in PREFERRED:
        if want in alternatives:
            return [want]
    return re.findall(r"[A-Za-z0-9.+-]+", alternatives[0].replace("WITH", " ").replace("AND", " "))


def standard_text(spdx: str, holders: str) -> str | None:
    path = NOTICES / "licenses" / f"{spdx}.txt"
    if not path.is_file():
        return None
    text = read_text(path)
    copyright_line = f"Copyright (c) {holders}" if holders else "Copyright (c) the authors"
    if "{copyright}" in text:
        return text.replace("{copyright}", copyright_line)
    return f"{copyright_line}\n\n{text}"


def fill_missing(pkg: Package, holders: str) -> None:
    """A package without licence files gets its licence's standard text."""
    if pkg.texts:
        return
    for spdx in spdx_ids(pkg.license):
        text = standard_text(spdx, holders)
        if text:
            pkg.texts.append(text)
        else:
            pkg.notes.append(f"no licence text in the package, and none on file for {spdx}")
    if pkg.texts:
        pkg.notes.append("licence text: the standard text for its declared licence "
                         "(the package ships none)")


def flagged(pkg: Package) -> str | None:
    """Why a package needs a decision, or None."""
    expr = pkg.license.replace("/", " OR ").strip()
    alternatives = re.split(r"\s+OR\s+", expr) if expr and "(" not in expr else [expr]
    copyleft = [bool(COPYLEFT.search(a)) for a in alternatives]
    if expr and all(copyleft):
        return f"declared licence: {pkg.license}"
    if any(copyleft):
        return None  # we take the permissive alternative; its GPL text can stay
    for text in pkg.texts:
        hit = COPYLEFT_TEXT.search(text)
        if hit:
            return f"licence text includes {' '.join(hit.group(0).split())!r}"
    return None


# --- Rust ----------------------------------------------------------------------


def host_triple() -> str:
    out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return next(ln.split(": ", 1)[1] for ln in out.splitlines() if ln.startswith("host: "))


def rust_packages(target: str) -> list[Package]:
    """Crates linked into the app: normal dependencies, followed from the app
    crate, for `target`. Build-only and dev dependencies don't ship."""
    meta = json.loads(subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--locked",
         "--manifest-path", str(ROOT / "src-tauri" / "Cargo.toml"), "--filter-platform", target],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout)
    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = meta["resolve"]["root"]
    seen, stack = set(), [root]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for dep in nodes[pid]["deps"]:
            if any(kind["kind"] is None for kind in dep["dep_kinds"]):
                stack.append(dep["pkg"])
    seen.discard(root)

    out = []
    for pid in seen:
        p = packages[pid]
        folder = Path(p["manifest_path"]).parent
        files = license_files(folder)
        if p.get("license_file"):
            extra = (folder / p["license_file"]).resolve()
            if extra.is_file() and extra not in files:
                files.append(extra)
        pkg = Package(
            "rust", p["name"], p["version"], p.get("license") or "",
            texts=[read_text(f) for f in elected_files(files, p.get("license") or "")],
            url=p.get("repository") or "",
        )
        authors = ", ".join(re.sub(r"\s*<[^>]*>", "", a) for a in p.get("authors") or [])
        fill_missing(pkg, authors or f"the {p['name']} authors")
        out.append(pkg)
    return sorted(out, key=lambda pkg: (pkg.name, pkg.version))


# --- npm -----------------------------------------------------------------------

#: In the bundle without being imported: Tailwind writes its preflight (from
#: modern-normalize) into the CSS. Package folder names under node_modules.
JS_EXTRA = ["tailwindcss"]


def npm_packages() -> list[Package]:
    records = sorted(JS_RECORDS.glob("js-*.json"))
    if not records:
        raise SystemExit(
            f"no record of the bundled npm packages in {JS_RECORDS}: run `pnpm build` first"
        )
    folders = {Path(f) for rec in records for f in json.loads(rec.read_text(encoding="utf-8"))}
    for name in JS_EXTRA:
        folder = (ROOT / "node_modules" / name).resolve()
        if folder.is_dir():
            folders.add(folder)
    out: dict[tuple[str, str], Package] = {}
    for folder in folders:
        manifest = folder / "package.json"
        if not manifest.is_file():
            continue
        meta = json.loads(read_text(manifest))
        lic = meta.get("license") or ""
        if isinstance(lic, dict):
            lic = lic.get("type", "")
        repo = meta.get("repository") or ""
        pkg = Package(
            "npm", meta.get("name", folder.name), meta.get("version", ""), lic,
            texts=[read_text(f) for f in elected_files(license_files(folder), lic)],
            url=repo.get("url", "") if isinstance(repo, dict) else repo,
        )
        author = meta.get("author") or ""
        if isinstance(author, dict):
            author = author.get("name", "")
        fill_missing(pkg, re.sub(r"\s*[<(].*$", "", author) or f"the {pkg.name} authors")
        out[(pkg.name, pkg.version)] = pkg
    return sorted(out.values(), key=lambda pkg: (pkg.name, pkg.version))


# --- the AI engine (Python) ------------------------------------------------------


def _metadata(dist_info: Path) -> dict[str, list[str]]:
    head = read_text(dist_info / "METADATA").split("\n\n", 1)[0]
    fields: dict[str, list[str]] = {}
    key = ""
    for line in head.splitlines():
        if line.startswith((" ", "\t")) and key:
            fields[key][-1] += " " + line.strip()
            continue
        key, _, value = line.partition(": ")
        fields.setdefault(key, []).append(value.strip())
    return fields


def _declared_license(fields: dict[str, list[str]]) -> str:
    if fields.get("License-Expression"):
        return fields["License-Expression"][0]
    classifiers = [
        c.split(" :: ")[-1] for c in fields.get("Classifier", []) if c.startswith("License ::")
    ]
    lic = (fields.get("License") or [""])[0]
    if lic and len(lic) <= 60 and "\n" not in lic and lic.upper() != "UNKNOWN":
        return lic
    return " OR ".join(_CLASSIFIER_SPDX.get(c, c) for c in classifiers) or lic[:60]


_CLASSIFIER_SPDX = {
    "MIT License": "MIT",
    "BSD License": "BSD-3-Clause",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
}
_SHORT_SPDX = {"MIT License": "MIT", "Apache 2.0": "Apache-2.0", "BSD": "BSD-3-Clause",
               "ISC License": "ISC", "3-Clause BSD License": "BSD-3-Clause"}


def _site_packages(python: Path) -> tuple[Path, Path]:
    out = subprocess.run(
        [str(python), "-c",
         "import json, sys, sysconfig; "
         "print(json.dumps([sysconfig.get_paths()['purelib'], sys.base_prefix]))"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    purelib, base = json.loads(out)
    return Path(purelib), Path(base)


def _toc_sources(work: Path) -> list[tuple[str, str]]:
    """(bundled name, source path) of everything PyInstaller put in the engine."""
    entries: list[tuple[str, str]] = []
    pyz = ast.literal_eval(read_text(work / "PYZ-00.toc"))
    entries += [(name, src) for name, src, _kind in pyz[1]]
    collect = ast.literal_eval(read_text(work / "COLLECT-00.toc"))
    toc = collect[0] if isinstance(collect, tuple) else collect
    entries += [(name, src) for name, src, kind in toc if kind != "SYMLINK" and src]
    return entries


#: Statically linked into python-build-standalone's CPython, which the build
#: venv uses and PyInstaller copies (libpython / python3.dll and the
#: extension modules). On macOS, zlib, libedit and ncurses come from the OS.
RUNTIME_COMPONENTS = [
    ("OpenSSL", "Apache-2.0", "The OpenSSL Project Authors", None),
    ("SQLite", "blessing", "", "sqlite"),
    ("libffi", "MIT", "", "libffi.txt"),
    ("bzip2", "bzip2-1.0.6", "", "bzip2.txt"),
    ("xz (liblzma)", "0BSD", "", "xz.txt"),
    ("Expat", "MIT", "", "expat.txt"),
    ("mpdecimal (libmpdec)", "BSD-2-Clause", "2008-2024 Stefan Krah", None),
    ("Tcl/Tk", "TCL", "", "tcl-tk.txt"),
    ("zlib (Windows engine)", "Zlib", "", "zlib.txt"),
]
_SQLITE = (
    "The author disclaims copyright to this source code. In place of a legal\n"
    "notice, here is a blessing:\n\n"
    "    May you do good and not evil.\n"
    "    May you find forgiveness for yourself and forgive others.\n"
    "    May you share freely, never taking more than you give.\n"
)


#: Notices a distribution's own files don't carry. Keyed by distribution name.
PYTHON_SUPPLEMENTS: dict[str, list[tuple[str, str]]] = {
    # The wheel ships no licence file. On x86_64 (Intel Macs, Windows) it also
    # statically links Intel MKL and oneDNN and ships Intel's OpenMP runtime
    # (libiomp5); on Apple silicon it uses Accelerate. spdlog (with fmt) is in
    # every build.
    "ctranslate2": [
        ("text", "ctranslate2.txt"),
        ("text", "spdlog.txt"),
        ("note", "spdlog bundles {fmt}, also MIT: Copyright (c) 2012 - present, Victor Zverovich."),
        ("x86_64", "onednn-third-party.txt"),
        ("x86_64-note", "Intel and Windows engines: links oneDNN (Apache-2.0, with its third-party "
                        "programs file above) and Intel oneMKL; see the Intel runtime entry."),
    ],
    # PortAudio, which sounddevice loads, is in sounddevice's wheel with only a README.
    "sounddevice": [("text", "portaudio.txt")],
    # mlx-whisper ships no licence file (it comes from ml-explore/mlx-examples).
    "mlx-whisper": [("text", "mlx-examples.txt"), ("text", "whisper.txt")],
    "faster-whisper": [("note", "Includes the Silero VAD model; see Models and data.")],
    # The macOS wheels' GCC runtime (libquadmath is LGPL-2.1+): where its
    # source is, and how to replace it — what the LGPL asks for.
    "numpy": [("text", "gcc-runtime-libraries.txt")],
    "scipy": [("text", "gcc-runtime-libraries.txt")],
}

#: Distributions whose files are ours, or that aren't really in the engine.
PYTHON_SKIP = {"auto-presenter-sidecar"}


def write_engine_notices(python: Path, work: Path, arch: str) -> list[Package]:
    """Record the licences of what PyInstaller froze into the engine.

    Called by scripts/build_sidecar.py after each build. `work` is
    PyInstaller's work folder for sidecar.spec (holding PYZ-00.toc and
    COLLECT-00.toc), whose source paths say exactly which installed files went
    in; each is traced to its distribution through the venv's RECORD files.
    """
    purelib, base = _site_packages(python)
    owner: dict[str, Path] = {}
    for dist_info in purelib.glob("*.dist-info"):
        record = dist_info / "RECORD"
        if not record.is_file():
            continue
        for line in read_text(record).splitlines():
            rel = line.rsplit(",", 2)[0]
            if rel:
                owner[os.path.normcase(os.path.normpath(purelib / rel))] = dist_info

    used: dict[Path, set[str]] = {}
    unowned: list[str] = []
    bundled_names: list[str] = []
    site = os.path.normcase(str(purelib))
    for name, src in _toc_sources(work):
        bundled_names.append(name)
        path = os.path.normcase(os.path.normpath(src))
        if path in owner:
            used.setdefault(owner[path], set()).add(name)
        elif path.startswith(site):
            unowned.append(src)
    if unowned:
        raise SystemExit(
            f"{len(unowned)} engine files belong to no installed distribution, so their licence "
            f"is unknown: {unowned[:5]}"
        )

    packages: list[Package] = []
    for dist_info in used:
        fields = _metadata(dist_info)
        name = fields["Name"][0]
        if name.lower() in PYTHON_SKIP:
            continue
        lic = _declared_license(fields)
        lic = _SHORT_SPDX.get(lic, lic)
        record_files = [
            Path(line.rsplit(",", 2)[0]) for line in read_text(dist_info / "RECORD").splitlines()
        ]
        texts = [read_text(f) for f in license_files(dist_info, recursive=True)]
        # Packages that keep their licences beside their code (onnxruntime's
        # ThirdPartyNotices.txt, for one), or vendor other packages with
        # theirs; a vendored package's only counts if it was bundled.
        components = {c for n in used[dist_info] for c in re.split(r"[./\\]", n.lower())}
        for rel in record_files:
            full = purelib / rel
            if (dist_info.name in rel.parts or not LICENSE_FILE.match(rel.name)
                    or not full.is_file() or rel.suffix.lower() in (".py", ".pyc")):
                continue
            vendored = next((p for p in rel.parts[1:] if p.endswith(".dist-info")), None)
            if vendored and vendored.split("-")[0].lower().replace("-", "_") not in components:
                continue
            texts.append(read_text(full))
        pkg = Package(
            "python", name, fields.get("Version", [""])[0], lic, texts=texts,
            url=next((u.split(", ", 1)[-1] for u in fields.get("Project-URL", [])
                      if re.match(r"(source|repository|homepage|code)", u, re.I)), "")
            or (fields.get("Home-page") or [""])[0],
        )
        for kind, value in PYTHON_SUPPLEMENTS.get(name.lower(), PYTHON_SUPPLEMENTS.get(name, [])):
            x86 = arch == "x86_64"
            if kind == "text" or (kind == "x86_64" and x86):
                pkg.texts.append(read_text(NOTICES / "supplements" / value))
            elif kind == "note" or (kind == "x86_64-note" and x86):
                pkg.notes.append(value)
        holder = ", ".join(fields.get("Author", []) + fields.get("Maintainer", [])) \
            or re.sub(r"\s*<[^>]*>", "", ", ".join(fields.get("Author-email", [])))
        fill_missing(pkg, holder or f"the {name} authors")
        packages.append(pkg)

    packages += _runtime_packages(python, purelib, base, bundled_names)
    ENGINE_RECORD.parent.mkdir(parents=True, exist_ok=True)
    ENGINE_RECORD.write_text(json.dumps({
        "arch": arch,
        "platform": sys.platform,
        "packages": [
            asdict(p) for p in sorted(packages, key=lambda p: (p.ecosystem, p.name.lower()))
        ],
    }, indent=1) + "\n", encoding="utf-8")
    return packages


def _runtime_packages(python: Path, purelib: Path, base: Path, bundled: list[str]) -> list[Package]:
    version = subprocess.run(
        [str(python), "-c", "import platform; print(platform.python_version())"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()
    cpython_license = next(
        (p for p in (base / "lib" / f"python{version.rsplit('.', 1)[0]}" / "LICENSE.txt",
                     base / "LICENSE.txt", base / "Lib" / "LICENSE.txt") if p.is_file()),
        None,
    )
    if cpython_license is None:
        raise SystemExit(f"no CPython LICENSE.txt under {base}")
    runtime = [Package(
        "runtime", "CPython", version, "PSF-2.0", texts=[read_text(cpython_license)],
        notes=["The engine's Python interpreter and standard library (python-build-standalone)."],
        url="https://www.python.org",
    )]
    for name, lic, holder, source in RUNTIME_COMPONENTS:
        if name.startswith("zlib") and sys.platform != "win32":
            continue
        if source == "sqlite":
            text = _SQLITE
        elif source:
            text = read_text(NOTICES / "supplements" / source)
        else:
            text = standard_text(lic, holder) or ""
        runtime.append(Package(
            "runtime", name, "", lic, texts=[text],
            notes=["Statically linked into the engine's Python runtime."],
        ))

    # The bootloader (the engine's executable) is PyInstaller's: GPL with an
    # exception for exactly this use. PyInstaller itself doesn't ship.
    pyinstaller = next(purelib.glob("pyinstaller-*.dist-info"), None) \
        or next(purelib.glob("PyInstaller-*.dist-info"), None)
    if pyinstaller is not None:
        fields = _metadata(pyinstaller)
        runtime.append(Package(
            "runtime", "PyInstaller bootloader", fields.get("Version", [""])[0],
            "GPL-2.0-or-later WITH Bootloader-exception",
            texts=[read_text(f) for f in license_files(pyinstaller, recursive=True)],
            notes=["The engine's executable is PyInstaller's bootloader, which its licence's "
                   "exception allows to be distributed with any program."],
            url="https://pyinstaller.org",
        ))
    # x86_64: CTranslate2's wheels statically link Intel oneMKL and ship
    # Intel's OpenMP runtime, under Intel's licences rather than open-source ones.
    iomp = sorted({Path(n).name for n in bundled if re.match(r"libiomp5", Path(n).name, re.I)})
    if iomp:
        runtime.append(Package(
            "runtime", "Intel oneMKL and OpenMP runtime (in CTranslate2)", "",
            "Intel End User License Agreement for Developer Tools (proprietary, redistributable)",
            # Intel's own text, from its intel-openmp package (2023.2.0). It
            # allows redistribution inside a product under a licence that
            # forbids reverse engineering these components: the Terms of use
            # (site/terms.html) say so.
            texts=[read_text(NOTICES / "supplements" / "intel-developer-tools-eula.txt")],
            notes=[f"oneMKL is statically linked into CTranslate2; the OpenMP runtime ships as "
                   f"{', '.join(iomp)}. Both come from CTranslate2's PyPI wheels. You may not "
                   "reverse-engineer, decompile or disassemble them (see the Terms of use).",
                   "https://www.intel.com/content/www/us/en/developer/articles/license/onemkl-license-faq.html"],
        ))
    # Windows: sounddevice's wheel carries a PortAudio DLL with Steinberg's ASIO
    # SDK compiled in (proprietary or GPL-3), and collect_dynamic_libs takes it.
    asio = sorted({Path(n).name for n in bundled if re.search(r"asio", Path(n).name, re.I)})
    if asio:
        runtime.append(Package(
            "runtime", "Steinberg ASIO SDK (in PortAudio)", "",
            "Steinberg ASIO SDK licence OR GPL-3.0", texts=[],
            notes=[f"Compiled into {', '.join(asio)} from the sounddevice wheel."],
        ))
    return runtime


def engine_packages() -> tuple[list[Package], str | None]:
    """The staged engine's packages and its arch, or ([], None) without one."""
    exe = "auto-presenter-sidecar" + (".exe" if sys.platform == "win32" else "")
    if not (STAGED_ENGINE / exe).is_file():
        return [], None
    if not ENGINE_RECORD.is_file():
        raise SystemExit(
            f"an engine is staged in {STAGED_ENGINE} but there's no record of its licences "
            f"({ENGINE_RECORD}): rebuild it with scripts/build_sidecar.py"
        )
    # Not the executable's time: signing rewrites it after the record is made.
    built = STAGED_ENGINE / "_internal" / "base_library.zip"
    if built.is_file() and ENGINE_RECORD.stat().st_mtime + 1 < built.stat().st_mtime:
        raise SystemExit(
            f"{ENGINE_RECORD} is older than the staged engine: "
            "rebuild it with scripts/build_sidecar.py"
        )
    record = json.loads(ENGINE_RECORD.read_text(encoding="utf-8"))
    return [Package(**p) for p in record["packages"]], record["arch"]


# --- models and data -------------------------------------------------------------


def model_packages(with_engine: bool, engine_arch: str = "") -> list[Package]:
    data = ROOT / "sidecar" / "auto_presenter" / "index" / "data" / "cmudict.LICENSE"
    out = [
        Package(
            "model", "CMU Pronouncing Dictionary", "", "BSD-2-Clause",
            texts=[read_text(data)],
            notes=["Its data ships in the engine; the GPL-3 `cmudict` Python wrapper doesn't."],
            url="https://github.com/cmusphinx/cmudict",
        ),
        Package(
            "model", "Whisper speech models (OpenAI)", "", "MIT",
            texts=[read_text(NOTICES / "supplements" / "whisper.txt")],
            notes=["Not in the installer: downloaded on first use, as converted by Systran "
                   "(faster-whisper, CTranslate2 format) or mlx-community (MLX format)."],
            url="https://github.com/openai/whisper",
        ),
    ]
    if with_engine:
        out += [
            Package(
                "model", "BTC chord recognition model", "", "MIT",
                texts=[read_text(MODEL_LICENSES / "BTC-ISMIR19-LICENSE.txt")],
                notes=["Exported to ONNX from the published BTC-ISMIR19 checkpoint."],
                url="https://github.com/jayg996/BTC-ISMIR19",
            ),
            Package(
                "model", "e5-small-v2 sentence embedding model", "", "MIT",
                texts=[read_text(MODEL_LICENSES / "e5-small-v2-LICENSE.txt")],
                notes=["Library search. The int8 ONNX export by Xenova of intfloat/e5-small-v2."],
                url="https://huggingface.co/intfloat/e5-small-v2",
            ),
            Package(
                "model", "Silero VAD", "", "MIT",
                texts=[read_text(NOTICES / "supplements" / "silero-vad.txt")],
                notes=["Bundled with faster-whisper."],
                url="https://github.com/snakers4/silero-vad",
            ),
        ]
    if engine_arch in ("arm64", "aarch64"):  # bundled with the MLX engine only
        out.append(
            Package(
                "model", "Next-line phone model", "", "Own work; trained on Free Music Archive audio",
                texts=[read_text(NOTICES / "supplements" / "next-line-model.txt")],
                notes=["Apple-silicon engine only."],
                url="https://github.com/mdeff/fma",
            )
        )
    return out


# --- review --------------------------------------------------------------------


def review(packages: list[Package]) -> tuple[list[str], list[str]]:
    """(open warnings, unreviewed errors) for the flagged packages."""
    decisions = json.loads((NOTICES / "reviewed.json").read_text(encoding="utf-8"))["packages"]
    warnings, errors = [], []
    for pkg in packages:
        why = flagged(pkg)
        if not why:
            continue
        decision = decisions.get(pkg.key)
        label = f"{pkg.key} {pkg.version}".strip() + f" ({why})"
        if decision is None:
            errors.append(label)
        elif decision["status"] == "open":
            warnings.append(f"{label}: {decision['note']}")
    return warnings, errors


def report_review(packages: list[Package]) -> None:
    warnings, errors = review(packages)
    for w in warnings:
        print(f"licence warning (open in scripts/notices/reviewed.json): {w}")
    if errors:
        for e in errors:
            print(f"  unreviewed: {e}")
        raise SystemExit(
            f"{len(errors)} packages have licences that need a decision before they ship: "
            "add each to scripts/notices/reviewed.json"
        )


# --- output --------------------------------------------------------------------

SECTIONS = [
    ("model", "Models and data"),
    ("rust", "Rust crates (the app)"),
    ("npm", "JavaScript packages (the interface and band page)"),
    ("python", "Python packages (the AI engine)"),
    ("runtime", "The AI engine's Python runtime"),
]
RULE = "=" * 78


def render(packages: list[Package], target: str, version: str, engine_arch: str | None) -> str:
    numbers: dict[str, int] = {}  # same_text_key -> [n]
    texts: dict[int, str] = {}
    users: dict[int, list[str]] = {}
    for pkg in packages:
        for text in pkg.texts:
            n = numbers.setdefault(same_text_key(text), len(numbers) + 1)
            texts.setdefault(n, normalize(text))
            users.setdefault(n, []).append(f"{pkg.name} {pkg.version}".strip())

    engine = (
        f"the AI engine ({engine_arch})" if engine_arch
        else "no AI engine (this build was made without one)"
    )
    lines = [
        "Church Slides: third-party notices",
        "",
        f"Church Slides {version} for {target}, with {engine}. "
        f"Generated {date.today().isoformat()}.",
        "",
        "Church Slides includes the open-source components below, which remain under",
        "their own licences. Each entry names its licence and points to the full text",
        "under \"Licence texts\" at the end of this file.",
        "",
    ]
    for eco, title in SECTIONS:
        group = [p for p in packages if p.ecosystem == eco]
        if group:
            lines.append(f"  {title}: {len(group)}")
    for eco, title in SECTIONS:
        group = [p for p in packages if p.ecosystem == eco]
        if not group:
            continue
        lines += ["", RULE, title.upper(), RULE, ""]
        for pkg in group:
            refs = sorted({numbers[same_text_key(t)] for t in pkg.texts})
            head = f"{pkg.name} {pkg.version}".strip()
            lines.append(f"{head} — {pkg.license or 'licence not declared'}")
            if pkg.url:
                lines.append(f"    {pkg.url}")
            for note in pkg.notes:
                lines.append(f"    {note}")
            if refs:
                lines.append("    Licence text: " + ", ".join(f"[{n}]" for n in refs))
            lines.append("")
    lines += ["", RULE, "LICENCE TEXTS", RULE]
    for n, text in sorted(texts.items()):
        used_by = sorted(set(users[n]), key=str.lower)
        shown = ", ".join(used_by[:12])
        if len(used_by) > 12:
            shown += f" and {len(used_by) - 12} more"
        lines += ["", "-" * 78, f"[{n}] Used by: {shown}", "-" * 78, "", text.rstrip("\n")]
    return "\n".join(lines) + "\n"


def app_version() -> str:
    return json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--target",
        default=os.environ.get("TAURI_ENV_TARGET_TRIPLE") or None,
        help="Rust target triple (default: the one Tauri is building, else this machine's)",
    )
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    target = args.target or host_triple()

    engine, engine_arch = engine_packages()
    if engine_arch and not target.startswith(engine_arch):
        raise SystemExit(
            f"the staged engine is {engine_arch} but the app is being built for {target}: "
            f"run scripts/build_sidecar.py --arch {target.split('-')[0]} first"
        )
    packages = model_packages(bool(engine), engine_arch or "") + rust_packages(target) + npm_packages() + engine
    report_review(packages)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = render(packages, target, app_version(), engine_arch)
    args.out.write_text(text, encoding="utf-8")
    counts = ", ".join(
        f"{sum(p.ecosystem == eco for p in packages)} {eco}" for eco, _ in SECTIONS
        if any(p.ecosystem == eco for p in packages)
    )
    print(f"wrote {args.out} ({len(text) / 1e3:.0f} kB: {counts}) for {target}")


if __name__ == "__main__":
    if platform.system() == "Windows":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    main()
