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


class TesterBackedRepairContentProvider(RepairContentProvider):
    """Produces REAL fix content by delegating to the model-backed TesterBrain.

    generate(path, description) calls
    `tester.suggest_fix(workspace, path, description, plan)` and returns the
    result's "new_content". Any failure - an empty/keyless result OR a Tester
    exception - yields "" so the 8X seam falls back to the description and nothing
    dangerous is written. The provider never lets a Tester exception escape."""

    def __init__(self, tester, workspace, plan=None):
        self.tester = tester
        self.workspace = workspace
        self.plan = plan

    def generate(self, path, description):
        try:
            result = self.tester.suggest_fix(
                self.workspace, path, description, self.plan
            )
        except Exception:  # noqa: BLE001 - never let a Tester failure escape
            return ""
        if not result:
            return ""
        return result.get("new_content") or ""


class RepairContentProviderFactory:
    """Maps a provider NAME to a concrete RepairContentProvider.

    Known names this phase are the offline stubs "mock" and "local"; an unknown
    name raises ValueError (no silent/None fallback). No model, no network."""

    @classmethod
    def create(cls, provider_name, **kwargs):
        if provider_name == "mock":
            return MockRepairContentProvider()

        if provider_name == "local":
            return LocalRepairContentProvider()

        if provider_name == "tester":
            return TesterBackedRepairContentProvider(
                kwargs["tester"], kwargs["workspace"], kwargs.get("plan"),
            )

        raise ValueError(
            f"Unknown repair content provider: {provider_name}"
        )
