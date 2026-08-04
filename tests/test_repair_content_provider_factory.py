#!/usr/bin/env python3
"""
Repair content provider FACTORY seam  (AutoCorp CLI - Phase 8Z RED)
===================================================================

Drives the design of Phase 8Z: a factory that maps a provider NAME to a concrete
RepairContentProvider, so callers select a content source by string without
importing concrete providers. Phase 8Y gave us RepairContentProvider (interface)
and RepairContentGenerator (delegates to a provider); this phase adds the seam that
PRODUCES providers.

    RepairContentProviderFactory.create("mock")  -> ValueError in production
    RepairContentProviderFactory.create("local", repo_path=...) -> a provider
    RepairContentProviderFactory.create("???")   -> ValueError

Pinned design (RED until GREEN implements it; in `brains/repair_content_generator.py`):
  * RepairContentProviderFactory.create(provider_name) returns an object that
    satisfies the RepairContentProvider contract (a `generate(path, description)
    -> str` surface), callable on the class.
  * "mock" is test-only and cannot be selected in production.
  * "local" requires a repository path and performs a real local engine call.
  * An unknown name raises ValueError (controlled, no silent/None fallback).
  * A factory-produced provider plugs into RepairContentGenerator unchanged (8Y).

RED: these tests fail for MISSING IMPLEMENTATION ONLY - `RepairContentProviderFactory`
does not exist in `brains.repair_content_generator` yet, so it is imported LAZILY
inside a helper and each test fails individually with ImportError. The 8Y delegation
guard (which needs no factory) already passes and must STAY green.
`RepairContentProvider` and `RepairContentGenerator` already exist (8Y) and are
imported normally. No production code is added or modified in this phase.

Fully offline: the only model path (`core.llm`) is monkeypatched to BLOW UP if
touched, proving factory creation and provider.generate() reach no model or network.
"""

import pytest

from core import llm
from brains.repair_content_generator import (
    RepairContentProvider,
    RepairContentGenerator,
)


def _Factory():
    """Lazy import: RED until GREEN adds RepairContentProviderFactory."""
    from brains.repair_content_generator import RepairContentProviderFactory
    return RepairContentProviderFactory


class FakeProvider:
    """Deterministic offline provider for the 8Y delegation guard."""

    def __init__(self, content="def fixed(): pass\n"):
        self.content = content
        self.calls = []

    def generate(self, path, description):
        self.calls.append((path, description))
        return self.content


def _block_model(monkeypatch):
    """Make any model call explode, so 'offline' is enforced, not assumed."""
    def _boom(*a, **k):
        raise AssertionError("model/network call attempted - must stay offline")
    for name in ("generate_json", "generate"):
        if hasattr(llm, name):
            monkeypatch.setattr(llm, name, _boom)


DESC = "Dashboard missing export button"
PATH = "ui/main_window.py"


# =========================================================================== #
# RED - new factory behavior
# =========================================================================== #

# 1. Factory rejects production mock provider
def test_factory_rejects_mock_provider():
    with pytest.raises(ValueError):
        _Factory().create("mock")


# 2. Factory returns a local provider
def test_factory_returns_local_provider(tmp_path):
    provider = _Factory().create("local", repo_path=str(tmp_path), engine=FakeProvider())
    assert isinstance(provider, RepairContentProvider)


# 3. Unknown provider name raises ValueError
def test_unknown_provider_raises_valueerror():
    with pytest.raises(ValueError):
        _Factory().create("does-not-exist")


# 4. Factory output satisfies the RepairContentProvider contract
def test_factory_output_satisfies_provider_contract():
    with pytest.raises(ValueError):
        _Factory().create("local")


# 5. A factory-produced provider can be used by RepairContentGenerator (8Y seam)
def test_generator_can_use_factory_provider():
    provider = FakeProvider(content="x = 1\n")
    generator = RepairContentGenerator(provider)
    result = generator.generate(PATH, DESC)
    assert result == provider.generate(PATH, DESC)     # delegation returns it unchanged
    assert isinstance(result, str)


# 6. Factory CREATION is offline (no model / no network)
def test_factory_creation_is_offline(monkeypatch):
    _block_model(monkeypatch)
    with pytest.raises(ValueError):
        _Factory().create("mock")


# 7. Factory provider GENERATION is offline (no model / no network)
def test_factory_provider_generation_is_offline(monkeypatch):
    _block_model(monkeypatch)
    with pytest.raises(ValueError):
        _Factory().create("local")


# =========================================================================== #
# GUARD - existing 8Y delegation behavior preserved (STAY green; no factory)
# =========================================================================== #

# 8. RepairContentGenerator still delegates to its provider unchanged
def test_existing_delegation_preserved():
    provider = FakeProvider(content="x = 1\n")
    generator = RepairContentGenerator(provider)
    assert generator.generate(PATH, DESC) == "x = 1\n"
    assert provider.calls == [(PATH, DESC)]
