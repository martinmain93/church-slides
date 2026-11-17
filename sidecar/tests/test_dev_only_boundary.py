"""Dev-only engine modules stay out of release builds without breaking them.

`packaging/dev_only.py` lists them and `sidecar.spec` leaves them out of the
frozen engine. A shipped module that imports one at module level would then
fail to import in release, and one that imports it inside a function would
fail when that function runs — so the first is never allowed, and the second
only from the modules `LAZY_IMPORTERS` names, with the reason.

The other direction too: a shipped module the app can't reach from its entry
point (only dev-only code or nothing imports it) belongs on the list, or out
of the package. `dev/client.py` shipped that way after the cleanup that
introduced this test.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

SIDECAR = Path(__file__).resolve().parents[1]
PACKAGE = SIDECAR / "auto_presenter"


def _load_lists():
    spec = importlib.util.spec_from_file_location("dev_only", SIDECAR / "packaging" / "dev_only.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DEV_ONLY_MODULES, mod.LAZY_IMPORTERS


DEV_ONLY, LAZY_IMPORTERS = _load_lists()


def _modules() -> dict[str, Path]:
    out = {}
    for path in PACKAGE.rglob("*.py"):
        parts = list(path.relative_to(SIDECAR).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = path
    return out


MODULES = _modules()


def _is_dev_only(name: str) -> bool:
    return any(name == d or name.startswith(d + ".") for d in DEV_ONLY)


def _imports(path: Path, name: str):
    """(imported module, is_module_level) for every import in a file."""
    tree = ast.parse(path.read_text())
    is_pkg = path.name == "__init__.py"

    def walk(node, module_level):
        for child in ast.iter_child_nodes(node):
            level = module_level and not isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            )
            if isinstance(child, ast.Import):
                for a in child.names:
                    yield a.name, module_level
            elif isinstance(child, ast.ImportFrom):
                base = child.module or ""
                if child.level:
                    pkg = name.split(".") if is_pkg else name.split(".")[:-1]
                    pkg = pkg[: len(pkg) - (child.level - 1)]
                    base = ".".join(pkg + ([base] if base else []))
                yield base, module_level
                for a in child.names:
                    yield f"{base}.{a.name}", module_level
            yield from walk(child, level)

    yield from walk(tree, True)


def test_every_listed_module_exists():
    missing = [d for d in DEV_ONLY if d not in MODULES]
    assert not missing, f"dev_only.py names modules that don't exist: {missing}"


def test_lazy_importers_are_shipped_modules_that_need_the_exception():
    for name in LAZY_IMPORTERS:
        assert name in MODULES and not _is_dev_only(name), name
        assert any(_is_dev_only(t) for t, _ in _imports(MODULES[name], name)), (
            f"{name} no longer imports anything dev-only; drop it from LAZY_IMPORTERS"
        )


@pytest.mark.parametrize("name", sorted(n for n in MODULES if not _is_dev_only(n)))
def test_shipped_module_does_not_depend_on_dev_only_code(name):
    for target, module_level in _imports(MODULES[name], name):
        if not _is_dev_only(target):
            continue
        assert not module_level, (
            f"{name} imports dev-only {target} at module level, so it would not "
            "import in a release build"
        )
        assert name in LAZY_IMPORTERS, (
            f"{name} imports dev-only {target}; release builds leave it out. "
            "Move the call behind a dev-only path and add the module to "
            "LAZY_IMPORTERS in packaging/dev_only.py with the reason."
        )


#: Shipped modules nothing in the engine imports, and why they ship anyway.
UNIMPORTED_ON_PURPOSE = {
    "auto_presenter.__main__": "`python -m auto_presenter`, the dev entry point",
}


def _module_of(target: str) -> str | None:
    """The engine module an import target names (`pkg.mod.attr` -> `pkg.mod`)."""
    parts = target.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in MODULES:
            return candidate
    return None


def _reachable_from_main() -> set[str]:
    seen: set[str] = set()
    stack = ["auto_presenter.main"]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        parts = name.split(".")
        # its packages (`follower` is a namespace package: no file to read)
        stack.extend(p for i in range(1, len(parts)) if (p := ".".join(parts[:i])) in MODULES)
        for target, _ in _imports(MODULES[name], name):
            mod = _module_of(target)
            if mod is not None and not _is_dev_only(mod):
                stack.append(mod)
    return seen


def test_every_shipped_module_is_reachable_from_the_entry_point():
    reachable = _reachable_from_main()
    stray = sorted(
        n for n in MODULES
        if not _is_dev_only(n) and n not in reachable and n not in UNIMPORTED_ON_PURPOSE
    )
    assert not stray, (
        f"these ship in the frozen engine but nothing the app runs imports them: {stray}. "
        "Add them to DEV_ONLY_MODULES in packaging/dev_only.py if a dev tool needs "
        "them, or move them out of the package."
    )
