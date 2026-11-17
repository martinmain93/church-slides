"""Verify that the committed TypeScript is in sync with current Pydantic models.

If a model changes and `pnpm codegen` isn't re-run, this test fails. CI should
run it on every PR.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_PATH = REPO_ROOT / "src" / "types" / "generated.ts"
CODEGEN_PATH = REPO_ROOT / "shared" / "codegen.py"


def _load_codegen():
    spec = importlib.util.spec_from_file_location("auto_presenter_codegen", CODEGEN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("auto_presenter_codegen", module)
    spec.loader.exec_module(module)
    return module


def test_generated_ts_in_sync():
    codegen = _load_codegen()
    # Ask the generator for the bytes rather than rebuilding them here: a
    # second copy of the assembly order is the same drift this file guards
    # against, and it broke the moment codegen started emitting defaults.
    expected = codegen.render()

    actual = GENERATED_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "src/types/generated.ts is out of sync with Pydantic models. "
        "Run `pnpm codegen` to regenerate."
    )


def test_no_underscore_prefixed_models_emitted():
    """Private models (e.g. `_Base`) should not appear in the output."""
    contents = GENERATED_PATH.read_text()
    assert "interface _" not in contents


def test_tunable_defaults_are_generated_not_hand_copied():
    """The frontend's default tunables must come from the Pydantic model.

    `src/lib/tunables.ts` used to hand-copy these. It drifted: `target_margin_ms`
    stayed at 500 after a documented sweep moved the Python default to 1500,
    and since a session start always sends tunables, the stale copy won — the
    shipped app scheduled commits at 500 ms. Measured cost on the seven
    approved goldens: displayed wrong-slide 0.273 -> 0.297, median lag 287 ms
    worse. Every other field still matched, which is exactly why nobody
    noticed.
    """
    from auto_presenter.protocol import ParticleFilterTunables

    contents = GENERATED_PATH.read_text()
    assert "export const DEFAULT_PARTICLE_FILTER_TUNABLES" in contents
    for field, value in ParticleFilterTunables().model_dump().items():
        ts_value = str(value).lower() if isinstance(value, bool) else value  # True -> true
        assert f"{field}: {ts_value}," in contents, f"{field} missing from generated defaults"

    # And the consumer must reference it rather than re-typing the numbers.
    tunables_ts = (
        GENERATED_PATH.parents[2] / "src" / "lib" / "tunables.ts"
    ).read_text()
    assert "DEFAULT_PARTICLE_FILTER_TUNABLES" in tunables_ts
    assert "target_margin_ms:" not in tunables_ts, "defaults are being re-typed again"


def test_every_request_kind_has_a_declared_reply_type():
    """`ResponseForKind` must cover the whole `ClientMessage` union.

    The frontend's `request()` is typed off this map, so a request kind
    missing from it is a compile error at the call site rather than a silent
    `any` — but only if the map is complete. It is generated from the
    protocol for that reason: the `as unknown as Reply` casts it replaced
    were, in effect, a hand-maintained copy of this map that nothing checked
    against the server.
    """
    from typing import get_args

    from auto_presenter import protocol

    contents = GENERATED_PATH.read_text()
    block = contents.split("export interface ResponseForKind {")[1].split("}")[0]
    declared = {
        line.split(":")[0].strip() for line in block.splitlines() if ":" in line
    }
    requests = get_args(get_args(protocol.ClientMessage)[0])
    expected = {r.model_fields["kind"].default for r in requests}
    assert declared == expected, f"missing: {expected - declared}"


def test_a_notification_is_declared_as_never():
    """`record_slide_change` gets no reply from the server. Typing it `never`
    is what makes `AnsweredKind` exclude it, so calling `request()` on it
    cannot compile — it used to, and parked a pending entry per slide change
    for the full timeout before rejecting into a swallowed catch."""
    contents = GENERATED_PATH.read_text()
    assert "record_slide_change: never;" in contents
