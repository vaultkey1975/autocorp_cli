#!/usr/bin/env python3
"""
Repair Content Generator  (AutoCorp CLI - brains)  [Phase 8Y]
=============================================================

The pluggable content-synthesis seam behind the 8X `generator` slot on
GatedRepairFixer. A RepairContentGenerator holds a PROVIDER and DELEGATES the
actual content synthesis to it, returning the provider's output unchanged. This
keeps the gated fixer, the orchestrator, and the self-healing loop ignorant of
*how* content is produced - a future model-backed provider (DeepSeek / Claude /
Tester) can be injected without touching any of them.

PURE DELEGATION: this module performs no model call, no network, no subprocess,
no shell, no retry, and adds no extra abstraction. `RepairContentProvider` is the
interface concrete providers implement; `RepairContentGenerator` simply forwards
`generate(path, description)` to the injected provider.
"""


class RepairContentProvider:
    """Interface for a concrete repair-content provider.

    A provider turns a target `path` plus a failure `description` into the new
    file content (a string). Concrete implementations are a separate, later phase;
    the base raises NotImplementedError."""

    def generate(self, path: str, description: str) -> str:
        raise NotImplementedError


class RepairContentGenerator:
    """A generator that delegates content synthesis to an injected provider.

    Plugs into the 8X `generator` seam (it exposes `generate(path, description)`)
    and forwards every call to the provider, returning its output unchanged."""

    def __init__(self, provider):
        self.provider = provider

    def generate(self, path: str, description: str) -> str:
        return self.provider.generate(path, description)


class MockRepairContentProvider(RepairContentProvider):
    """A deterministic, fully offline provider for tests/wiring (no model)."""

    def generate(self, path, description):
        return f"# mock provider\n# {path}\n# {description}\n"


class LocalRepairContentProvider(RepairContentProvider):
    """A deterministic, fully offline placeholder for the future local provider.

    Offline stub this phase: a real Ollama-backed local provider is a later phase."""

    def generate(self, path, description):
        return f"# local provider\n# {path}\n# {description}\n"


class RepairContentProviderFactory:
    """Maps a provider NAME to a concrete RepairContentProvider.

    Known names this phase are the offline stubs "mock" and "local"; an unknown
    name raises ValueError (no silent/None fallback). No model, no network."""

    @classmethod
    def create(cls, provider_name):
        if provider_name == "mock":
            return MockRepairContentProvider()

        if provider_name == "local":
            return LocalRepairContentProvider()

        raise ValueError(
            f"Unknown repair content provider: {provider_name}"
        )
