#!/usr/bin/env python3
"""
Self-healing LOOP tests  (AutoCorp CLI - Phase 8O RED)
======================================================

Drives the design of Phase 8O: the FIRST phase that actually DRIVES a repair
cycle. A `SelfHealingOrchestrator.run_cycle(...)` runs one offline

        fix -> verify -> retry

loop over FixerWorkItem objects and returns a populated RepairCycle that records
the outcome.

Expected API (pinned by these tests; extends `brains/self_healing_orchestrator.py`):
  * SelfHealingOrchestrator.run_cycle(work_items, fixer, verify, max_attempts)
        -> RepairCycle
      Per-iteration contract (driven by the existing RetryController):
        record_attempt -> fixer.execute(work_items) -> verify()
      Stop when verify() returns True (healed) OR RetryController.is_exhausted.
  * RepairCycle gains a terminal `healed: bool` outcome field.
  * The loop talks ONLY to its injected collaborators (`fixer`, `verify`) and the
    pure RetryController - never a real engine, subprocess, or network.

These tests are RED on purpose and fail for MISSING IMPLEMENTATION ONLY. The
module `brains.self_healing_orchestrator` already exists (Phase 8N), so the
not-yet-built `run_cycle` method and the `RepairCycle.healed` field are reached
LAZILY inside each test; every test fails individually with AttributeError rather
than collapsing the file into a single collection error.

The supporting types (FixerWorkItem, RetryController, FixExecutionResult) already
exist (Phases 8K/8M/8L) and are imported normally. Fully offline: the only
collaborators are the local FakeFixer / FakeVerify below - no model, no network,
no subprocess. No production code is added in this phase.
"""

import pytest

from brains.acceptance_brain import FixerWorkItem
from brains.fixer_executor import FixExecutionResult
from brains.retry_controller import RetryController
from brains.self_healing_orchestrator import SelfHealingOrchestrator


# --------------------------------------------------------------------------- #
# Local fake collaborators - fully offline, deterministic, call-recording.
# --------------------------------------------------------------------------- #
class FakeFixer:
    """Stand-in for the live Fixer. Records every execute() call and returns one
    FixExecutionResult per work item with a LIVE status (never "planned"), so the
    loop's wiring can be observed without touching a model, file, or subprocess."""

    def __init__(self, status="fixed"):
        self.status = status
        self.calls = []  # one entry (the work_items list) per execute() call

    def execute(self, work_items):
        self.calls.append(list(work_items or []))
        return [
            FixExecutionResult(description=item.description, status=self.status)
            for item in (work_items or [])
        ]


class FakeVerify:
    """Scripted re-verification. Each call pops the next bool from `results`
    (True == build now healthy). Records its call count so the loop's iteration
    count can be asserted. Defaults to False once the script is exhausted."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return False


def _run_cycle(orch, work_items, fixer, verify, max_attempts):
    """Lazy access to the not-yet-built API: RED until GREEN adds run_cycle().
    Fails with AttributeError (missing implementation) until then."""
    return orch.run_cycle(
        work_items=work_items,
        fixer=fixer,
        verify=verify,
        max_attempts=max_attempts,
    )


# --------------------------------------------------------------------------- #
# 1. Heal on first attempt
# --------------------------------------------------------------------------- #
def test_heals_on_first_attempt():
    fixer = FakeFixer()
    verify = FakeVerify([True])
    cycle = _run_cycle(
        SelfHealingOrchestrator(),
        [FixerWorkItem("Dashboard missing export button")],
        fixer,
        verify,
        max_attempts=3,
    )
    assert cycle.healed is True
    assert cycle.retry_state.attempts == 1
    assert cycle.retry_state.exhausted is False
    assert len(fixer.calls) == 1          # fixer ran exactly once
    assert verify.calls == 1              # verified exactly once


# --------------------------------------------------------------------------- #
# 2. Retry then heal
# --------------------------------------------------------------------------- #
def test_retries_then_heals():
    fixer = FakeFixer()
    verify = FakeVerify([False, True])   # fails once, then healthy
    cycle = _run_cycle(
        SelfHealingOrchestrator(),
        [FixerWorkItem("CSV export not visible")],
        fixer,
        verify,
        max_attempts=3,
    )
    assert cycle.healed is True
    assert cycle.retry_state.attempts == 2
    assert cycle.retry_state.exhausted is False
    assert len(fixer.calls) == 2
    assert verify.calls == 2


# --------------------------------------------------------------------------- #
# 3. Exhaust retry budget on persistent failure
# --------------------------------------------------------------------------- #
def test_exhausts_budget_on_persistent_failure():
    fixer = FakeFixer()
    verify = FakeVerify([False, False, False])   # never heals
    cycle = _run_cycle(
        SelfHealingOrchestrator(),
        [FixerWorkItem("Dashboard missing export button")],
        fixer,
        verify,
        max_attempts=3,
    )
    assert cycle.healed is False
    assert cycle.retry_state.attempts == 3
    assert cycle.retry_state.exhausted is True
    assert len(fixer.calls) == 3
    assert verify.calls == 3


# --------------------------------------------------------------------------- #
# 4. Empty work items is a no-op
# --------------------------------------------------------------------------- #
def test_empty_work_items_is_noop():
    fixer = FakeFixer()
    verify = FakeVerify([True])
    cycle = _run_cycle(
        SelfHealingOrchestrator(),
        [],
        fixer,
        verify,
        max_attempts=3,
    )
    # Nothing to repair: the fixer is never invoked and no attempt is consumed.
    assert fixer.calls == []
    assert cycle.retry_state.attempts == 0
    assert cycle.execution_results == []


# --------------------------------------------------------------------------- #
# 5. Execution results carry a LIVE status (not "planned")
# --------------------------------------------------------------------------- #
def test_results_carry_live_status():
    fixer = FakeFixer(status="fixed")
    verify = FakeVerify([True])
    cycle = _run_cycle(
        SelfHealingOrchestrator(),
        [FixerWorkItem("Dashboard missing export button")],
        fixer,
        verify,
        max_attempts=3,
    )
    assert len(cycle.execution_results) == 1
    result = cycle.execution_results[0]
    assert isinstance(result, FixExecutionResult)
    assert result.status != "planned"
    assert result.status == "fixed"


# --------------------------------------------------------------------------- #
# 6. Preserve FixerWorkItem order
# --------------------------------------------------------------------------- #
def test_preserves_work_item_order():
    fixer = FakeFixer()
    verify = FakeVerify([True])
    items = [
        FixerWorkItem("first"),
        FixerWorkItem("second"),
        FixerWorkItem("third"),
    ]
    cycle = _run_cycle(
        SelfHealingOrchestrator(),
        items,
        fixer,
        verify,
        max_attempts=3,
    )
    assert [r.description for r in cycle.execution_results] == [
        "first",
        "second",
        "third",
    ]


# --------------------------------------------------------------------------- #
# 7. Drive only injected collaborators (no hidden execution path)
# --------------------------------------------------------------------------- #
def test_drives_only_injected_collaborators():
    fixer = FakeFixer()
    verify = FakeVerify([False, True])
    _run_cycle(
        SelfHealingOrchestrator(),
        [FixerWorkItem("Dashboard missing export button")],
        fixer,
        verify,
        max_attempts=5,
    )
    # The loop reaches the build ONLY through the injected fixer/verify - their
    # call counts equal the attempts taken, proving no other execution path ran.
    assert len(fixer.calls) == 2
    assert verify.calls == 2


# --------------------------------------------------------------------------- #
# 8. Return a fully populated RepairCycle
# --------------------------------------------------------------------------- #
def test_returns_populated_repair_cycle():
    from brains.self_healing_orchestrator import RepairCycle

    fixer = FakeFixer()
    verify = FakeVerify([True])
    work_items = [FixerWorkItem("Dashboard missing export button")]
    cycle = _run_cycle(
        SelfHealingOrchestrator(),
        work_items,
        fixer,
        verify,
        max_attempts=3,
    )
    assert isinstance(cycle, RepairCycle)
    # The cycle records the full chain: results executed, a retry budget tracked,
    # and a terminal outcome.
    assert len(cycle.execution_results) == 1
    assert cycle.retry_state is not None
    assert cycle.retry_state.max_attempts == 3
    assert cycle.healed is True
