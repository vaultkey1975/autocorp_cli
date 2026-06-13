#!/usr/bin/env python3
"""
Repair content PROVIDER seam  (AutoCorp CLI - Phase 8Y RED)
===========================================================

Drives the design of Phase 8Y: factoring the content-generation seam into a
pluggable PROVIDER. Phase 8X gave GatedRepairFixer an optional `generator` with a
`.generate(path, description)` surface (duck-typed). This phase introduces a real,
named generator class that DELEGATES the actual content synthesis to an injected
provider - so a future model-backed provider (DeepSeek / Claude / Tester) can be
dropped in without touching the generator, the gated fixer, or the orchestrator.

    GatedRepairFixer(generator=RepairContentGenerator(provider))
        generator.generate(path, description)
            -> provider.generate(path, description)   (the actual synthesis)
            -> new file content

Pinned design (RED until GREEN implements it; in a NEW module
`brains/repair_content_generator.py`):
  * RepairContentProvider - an interface with
        generate(self, path: str, description: str) -> str
    that concrete providers implement.
  * RepairContentGenerator(provider) - stores the provider and delegates:
        generate(path, description) -> provider.generate(path, description)
    returning the provider's output unchanged. It plugs into the 8X seam (it has
    the same `.generate(path, description)` surface GatedRepairFixer expects).

Scope guards (NONE of these happen in this phase): no model call, no network, no
DeepSeek, no Claude, no orchestrator change, no self-healing change, no command
execution. Providers in these tests are deterministic local fakes / mocks.

RED: these tests fail for MISSING IMPLEMENTATION ONLY - the module
`brains.repair_content_generator` does not exist yet, so `RepairContentProvider`
and `RepairContentGenerator` are imported LAZILY inside helpers and each test fails
individually with ModuleNotFoundError. The duck-typed-seam guard (which needs no
new module) already passes and must STAY green. `GatedRepairFixer`, `FixerWorkItem`,
`Executor`, and the gates already exist and are imported normally. No production
code is added or modified in this phase.

Fully offline: no model, no network, no subprocess, no shell.
"""

from unittest.mock import Mock

import pytest

from brains.acceptance_brain import FixerWorkItem
from brains.gated_repair_fixer import GatedRepairFixer
from safety.executor import WriteResult, CommandResult


# --------------------------------------------------------------------------- #
# Lazy imports: RED until GREEN adds brains/repair_content_generator.py.
# --------------------------------------------------------------------------- #
def _RepairContentGenerator():
    from brains.repair_content_generator import RepairContentGenerator
    return RepairContentGenerator


def _RepairContentProvider():
    from brains.repair_content_generator import RepairContentProvider
    return RepairContentProvider


# --------------------------------------------------------------------------- #
# Deterministic, offline fake collaborators (NO model, NO network).
# --------------------------------------------------------------------------- #
class FakeProvider:
    """A deterministic provider: records every call, returns fixed content."""

    def __init__(self, content="def fixed():\n    return True\n"):
        self.content = content
        self.calls = []

    def generate(self, path, description):
        self.calls.append((path, description))
        return self.content


class SpyExecutor:
    """Records write_file / run_command without touching disk or shell."""

    def __init__(self):
        self.writes = []
        self.runs = []

    def write_file(self, path, content):
        self.writes.append((path, content))
        return WriteResult(path, written=True)

    def run_command(self, command, cwd):
        self.runs.append(command)
        return CommandResult(command, returncode=0)


DESC = "Dashboard missing export button"


# =========================================================================== #
# RED - new provider-delegation behavior
# =========================================================================== #

# 1. Constructor stores the provider
def test_constructor_stores_provider():
    Generator = _RepairContentGenerator()
    provider = FakeProvider()
    gen = Generator(provider)
    assert gen.provider is provider


# 2. Generator delegates generation to the provider
def test_generator_delegates_to_provider():
    Generator = _RepairContentGenerator()
    provider = FakeProvider()
    Generator(provider).generate("ui/main_window.py", DESC)
    assert len(provider.calls) == 1


# 3. Path is passed through to the provider
def test_path_passed_correctly():
    Generator = _RepairContentGenerator()
    provider = FakeProvider()
    Generator(provider).generate("ui/main_window.py", DESC)
    assert provider.calls[0][0] == "ui/main_window.py"


# 4. Description is passed through to the provider
def test_description_passed_correctly():
    Generator = _RepairContentGenerator()
    provider = FakeProvider()
    Generator(provider).generate("ui/main_window.py", DESC)
    assert provider.calls[0][1] == DESC


# 5. The provider's output is returned unchanged
def test_provider_output_returned_unchanged():
    Generator = _RepairContentGenerator()
    provider = FakeProvider(content="x = 42\n")
    assert Generator(provider).generate("a.py", "d") == "x = 42\n"


# 6. Multiple calls are supported (each delegated, in order)
def test_multiple_calls_supported():
    Generator = _RepairContentGenerator()
    provider = FakeProvider()
    gen = Generator(provider)
    gen.generate("a.py", "first")
    gen.generate("b.py", "second")
    assert provider.calls == [("a.py", "first"), ("b.py", "second")]


# 7. The provider can be mocked (duck-typed delegation)
def test_provider_can_be_mocked():
    Generator = _RepairContentGenerator()
    provider = Mock()
    provider.generate.return_value = "mocked content\n"
    result = Generator(provider).generate("ui/main_window.py", DESC)
    assert result == "mocked content\n"
    provider.generate.assert_called_once_with("ui/main_window.py", DESC)


# 8. The RepairContentProvider interface can be implemented and used
def test_provider_interface_can_be_implemented():
    Provider = _RepairContentProvider()
    Generator = _RepairContentGenerator()

    class ConcreteProvider(Provider):
        def generate(self, path, description):
            return f"# {path} :: {description}\n"

    gen = Generator(ConcreteProvider())
    assert gen.generate("crud.py", "add fails") == "# crud.py :: add fails\n"


# 9. Existing 8X seam preserved: the generator plugs into GatedRepairFixer and the
#    provider's output reaches executor.write_file()
def test_existing_seam_behavior_preserved():
    Generator = _RepairContentGenerator()
    provider = FakeProvider(content="def fixed(): pass\n")
    spy = SpyExecutor()
    GatedRepairFixer(spy, generator=Generator(provider)).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert spy.writes[0] == ("ui/main_window.py", "def fixed(): pass\n")
    assert provider.calls == [("ui/main_window.py", DESC)]


# =========================================================================== #
# GUARD - the 8X duck-typed seam still works without the new module (STAY green)
# =========================================================================== #

# 10. A plain duck-typed generator (no provider, no new module) still drives the
#     8X seam exactly as before - the refactor target must not change this contract.
def test_duck_typed_generator_still_works():
    class DuckGenerator:
        def generate(self, path, description):
            return "duck content\n"

    spy = SpyExecutor()
    GatedRepairFixer(spy, generator=DuckGenerator()).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert spy.writes[0] == ("ui/main_window.py", "duck content\n")
