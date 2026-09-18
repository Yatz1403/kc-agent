"""Shared fixtures. Every test gets its own database file, so nothing depends
on the order tests run in or on a leftover kc.db."""

from __future__ import annotations

import pytest

from kc import fixture as fixture_module
from kc.agents import llm as llm_seam
from kc.stores.sqlite_store import Store


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(tmp_path / "kc.db")
    store.init_schema()
    return store


@pytest.fixture
def loaded(store: Store) -> tuple[Store, dict]:
    """Store with the BU-A / BU-B fixture applied and projected."""
    return store, fixture_module.build(store)


@pytest.fixture(autouse=True)
def no_llm_by_default():
    """No test may silently depend on a model client (FR-11.1, FR-6.5)."""
    llm_seam.set_default_client(None)
    yield
    llm_seam.set_default_client(None)
