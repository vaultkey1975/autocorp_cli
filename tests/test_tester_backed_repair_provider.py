#!/usr/bin/env python3
"""
Tester-backed repair content provider  (AutoCorp CLI - Phase 8AA RED)
=====================================================================

Drives the design of Phase 8AA: the FIRST non-stub RepairContentProvider. Phase 8Y
gave us the provider interface + delegating generator, and 8Z gave us the factory
with OFFLINE stub providers (mock/local). This phase introduces a provider that
produces REAL fix content by delegating to the existing model-backed Tester:

    RepairContentProviderFactory.create("tester", tester=..., workspace=...)
        -> TesterBackedRepairContentProvider
            generate(path, description)
                -> tester.suggest_fix(workspace, filename=path, error_output=description, plan)
                -> {explanation, filename, new_content}
                -> returns new_content (or "" on empty/failure)

Pinned design (RED until GREEN implements it; in `brains/repair_content_generator.py`):
  * TesterBackedRepairContentProvider(tester, workspace, plan=None) - a
    RepairContentProvider whose generate(path, description) calls
    `tester.suggest_fix(workspace, path, description, plan)` and returns the dict's
    "new_content" string.
  * FAILURE CONTRACT (matches the seam's expectations): if suggest_fix returns {} /
    no new_content, OR raises, generate returns "" - so the 8X seam falls back to
    the description and NOTHING dangerous is written. The provider never lets a
    Tester exception escape.
  * RepairContentProviderFactory.create("tester", tester=..., workspace=...) builds
    one; "mock"/"local" and the unknown->ValueError behavior are unchanged.

RED: these tests fail for MISSING IMPLEMENTATION ONLY -
`TesterBackedRepairContentProvider` does not exist in
`brains.repair_content_generator` yet (lazy import -> ImportError), and the factory
does not yet accept a "tester" name with kwargs (-> TypeError/ValueError). Each
failure is reached inside its own test, so the tests fail individually. The
factory mock/local guard already passes and must STAY green.
`RepairContentProvider` and `RepairContentProviderFactory` already exist (8Y/8Z)
and are imported normally. No production code is added or modified in this phase.

Fully offline: a FakeTester stands in for the model-backed TesterBrain - NO model,
NO network, NO subprocess. The real TesterBrain is never constructed here.
"""

import pytest

from brains.repair_content_generator import (
    RepairContentProvider,
    RepairContentProviderFactory,
)


def _TesterBackedProvider():
    """Lazy import: RED until GREEN adds TesterBackedRepairContentProvider."""
    from brains.repair_content_generator import TesterBackedRepairContentProvider
    return TesterBackedRepairContentProvider


class FakeTester:
    """Offline stand-in for TesterBrain.suggest_fix. Records calls; returns a
    scripted dict or raises a scripted exception. NO model, NO network."""

    def __init__(self, result=None, exc=None):
        self.result = result if result is not None else {}
        self.exc = exc
        self.calls = []

    def suggest_fix(self, workspace, filename, error_output, plan=None, findings=None):
        self.calls.append((workspace, filename, error_output, plan))
        if self.exc is not None:
            raise self.exc
        return self.result


WS = "workspace/demo"
PATH = "ui/main_window.py"
DESC = "Dashboard missing export button"
FIX = "class MainWindow:\n    pass\n"


# =========================================================================== #
# A. Construction
# =========================================================================== #
def test_can_be_instantiated():
    Provider = _TesterBackedProvider()
    provider = Provider(FakeTester(), WS)
    assert provider is not None


# =========================================================================== #
# E. Contract compliance (is a RepairContentProvider; generate returns str)
# =========================================================================== #
def test_is_repair_content_provider():
    Provider = _TesterBackedProvider()
    provider = Provider(FakeTester(result={"new_content": FIX}), WS)
    assert isinstance(provider, RepairContentProvider)
    assert isinstance(provider.generate(PATH, DESC), str)


# =========================================================================== #
# B. Delegation - calls tester.suggest_fix with mapped args
# =========================================================================== #
def test_delegates_to_tester():
    Provider = _TesterBackedProvider()
    tester = FakeTester(result={"new_content": FIX})
    Provider(tester, WS).generate(PATH, DESC)
    assert len(tester.calls) == 1
    workspace, filename, error_output, _plan = tester.calls[0]
    assert workspace == WS
    assert filename == PATH                       # path -> filename
    assert error_output == DESC                   # description -> error_output


# =========================================================================== #
# B. Delegation - tester's new_content returned unchanged
# =========================================================================== #
def test_returns_tester_new_content_unchanged():
    Provider = _TesterBackedProvider()
    provider = Provider(FakeTester(result={"new_content": FIX,
                                           "filename": PATH,
                                           "explanation": "fix it"}), WS)
    assert provider.generate(PATH, DESC) == FIX


# =========================================================================== #
# C. Factory integration - create("tester", ...) builds the provider
# =========================================================================== #
def test_factory_creates_tester_backed_provider():
    Provider = _TesterBackedProvider()
    provider = RepairContentProviderFactory.create(
        "tester", tester=FakeTester(result={"new_content": FIX}), workspace=WS)
    assert isinstance(provider, Provider)


# =========================================================================== #
# C. Factory integration - returns a working RepairContentProvider
# =========================================================================== #
def test_factory_returns_correct_type():
    provider = RepairContentProviderFactory.create(
        "tester", tester=FakeTester(result={"new_content": FIX}), workspace=WS)
    assert isinstance(provider, RepairContentProvider)
    assert provider.generate(PATH, DESC) == FIX


# =========================================================================== #
# D. Failure handling - empty result yields "" (seam falls back to description)
# =========================================================================== #
def test_empty_result_yields_empty_string():
    Provider = _TesterBackedProvider()
    provider = Provider(FakeTester(result={}), WS)        # suggest_fix gave up
    assert provider.generate(PATH, DESC) == ""


# =========================================================================== #
# D. Failure handling - a Tester exception is contained, yields ""
# =========================================================================== #
def test_tester_exception_yields_empty_string():
    Provider = _TesterBackedProvider()
    provider = Provider(FakeTester(exc=RuntimeError("model exploded")), WS)
    assert provider.generate(PATH, DESC) == ""            # no exception escapes


# =========================================================================== #
# E. Contract compliance - missing new_content key also yields a str ("")
# =========================================================================== #
def test_missing_new_content_yields_empty_string():
    Provider = _TesterBackedProvider()
    provider = Provider(FakeTester(result={"explanation": "no content"}), WS)
    result = provider.generate(PATH, DESC)
    assert isinstance(result, str)
    assert result == ""


# =========================================================================== #
# GUARD - existing factory behavior unchanged (mock/local/unknown) - STAY green
# =========================================================================== #
def test_factory_mock_local_unchanged():
    assert isinstance(RepairContentProviderFactory.create("mock"),
                      RepairContentProvider)
    assert isinstance(RepairContentProviderFactory.create("local"),
                      RepairContentProvider)
    with pytest.raises(ValueError):
        RepairContentProviderFactory.create("does-not-exist")
