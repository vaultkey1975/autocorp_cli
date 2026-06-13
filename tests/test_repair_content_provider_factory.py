#!/usr/bin/env python3
"""
Repair content provider FACTORY seam  (AutoCorp CLI - Phase 8Z RED)
===================================================================

Drives the design of Phase 8Z: a factory that maps a provider NAME to a concrete
RepairContentProvider, so callers select a content source by string without
importing concrete providers. Phase 8Y gave us RepairContentProvider (interface)
and RepairContentGenerator (delegates to a provider); this phase adds the seam that
PRODUCES providers.

    RepairContentProviderFactory.create("mock")  -> a RepairContentProvider
    RepairContentProviderFactory.create("local") -> a RepairContentProvider
    RepairContentProviderFactory.create("???")   -> ValueError

Pinned design (RED until GREEN implements it; in `brains/repair_content_generator.py`):
  * RepairContentProviderFactory.create(provider_name) returns an object that
    satisfies the RepairContentProvider contract (a `generate(path, description)
    -> str` surface), callable on the class.
  * Known names this phase: "mock" and "local" - BOTH fully OFFLINE stubs (no model,
    no network). Real DeepSeek / Claude / Ollama-backed providers are a LATER phase
    and are intentionally NOT wired here.
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

# 1. Factory returns a mock provider
def test_factory_returns_mock_provider():
    provider = _Factory().create("mock")
    assert isinstance(provider, RepairContentProvider)
    assert isinstance(provider.generate(PATH, DESC), str)


# 2. Factory returns a local provider
def test_factory_returns_local_provider():
    provider = _Factory().create("local")
    assert isinstance(provider, RepairContentProvider)
    assert isinstance(provider.generate(PATH, DESC), str)


# 3. Unknown provider name raises ValueError
def test_unknown_provider_raises_valueerror():
    with pytest.raises(ValueError):
        _Factory().create("does-not-exist")


# 4. Factory output satisfies the RepairContentProvider contract
def test_factory_output_satisfies_provider_contract():
    for name in ("mock", "local"):
        provider = _Factory().create(name)
        assert isinstance(provider, RepairContentProvider)
        result = provider.generate(PATH, DESC)
        assert isinstance(result, str)


# 5. A factory-produced provider can be used by RepairContentGenerator (8Y seam)
def test_generator_can_use_factory_provider():
    provider = _Factory().create("mock")
    generator = RepairContentGenerator(provider)
    result = generator.generate(PATH, DESC)
    assert result == provider.generate(PATH, DESC)     # delegation returns it unchanged
    assert isinstance(result, str)


# 6. Factory CREATION is offline (no model / no network)
def test_factory_creation_is_offline(monkeypatch):
    _block_model(monkeypatch)
    _Factory().create("mock")
    _Factory().create("local")                          # neither touches the model


# 7. Factory provider GENERATION is offline (no model / no network)
def test_factory_provider_generation_is_offline(monkeypatch):
    _block_model(monkeypatch)
    for name in ("mock", "local"):
        result = _Factory().create(name).generate(PATH, DESC)
        assert isinstance(result, str)                  # produced without a model


# =========================================================================== #
# GUARD - existing 8Y delegation behavior preserved (STAY green; no factory)
# =========================================================================== #

# 8. RepairContentGenerator still delegates to its provider unchanged
def test_existing_delegation_preserved():
    provider = FakeProvider(content="x = 1\n")
    generator = RepairContentGenerator(provider)
    assert generator.generate(PATH, DESC) == "x = 1\n"
    assert provider.calls == [(PATH, DESC)]
