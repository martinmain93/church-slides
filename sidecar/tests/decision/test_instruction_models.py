"""Catalog structure sanity — every entry has what its kind needs."""

from __future__ import annotations

from auto_presenter.decision.instruction_models import (
    CATALOG,
    DEFAULT_MODEL_ID,
    FALLBACK_MODEL_ID,
    find_model,
)


def test_ids_are_unique():
    ids = [m.id for m in CATALOG]
    assert len(ids) == len(set(ids))


def test_local_entries_have_a_pull_name_and_no_provider():
    for m in CATALOG:
        if m.kind == "local":
            assert m.pull_name, f"{m.id} is local but has no pull_name"
            assert not m.provider, f"{m.id} is local but has a provider"


def test_hosted_entries_have_a_provider_and_base_url_and_no_pull_name():
    for m in CATALOG:
        if m.kind == "hosted":
            assert m.provider, f"{m.id} is hosted but has no provider"
            assert m.base_url, f"{m.id} is hosted but has no base_url"
            assert not m.pull_name, f"{m.id} is hosted but has a pull_name"


def test_every_entry_has_a_label_and_a_note():
    for m in CATALOG:
        assert m.label
        assert m.note


def test_find_model_returns_none_for_an_unknown_id():
    assert find_model("not-a-real-model") is None


def test_find_model_finds_the_default():
    assert find_model(DEFAULT_MODEL_ID) is not None


def test_the_fallback_model_is_local_and_needs_nothing():
    """Trying the feature must not require a network or a key.

    That invariant used to be spelled `DEFAULT_MODEL_ID is local`, and it is
    the reason the catalog refused a hosted default for so long. The default
    is hosted now because it measured 15/15 against the best local model's
    14/15 *and* four times faster — but the invariant is unchanged, so it
    moved to where it now lives: with no key on file the backend builder falls
    all the way back to this model, and a service that loses its connection
    lands here too rather than losing the feature.

    See `test_server_instruction_models_dispatch` for the builder side.
    """
    spec = find_model(FALLBACK_MODEL_ID)
    assert spec is not None
    assert spec.kind == "local"
    assert not spec.provider, "a local fallback must not need a provider key"
