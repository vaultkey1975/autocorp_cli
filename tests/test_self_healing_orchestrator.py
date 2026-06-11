#!/usr/bin/env python3
"""
Self-healing orchestrator tests  (AutoCorp CLI - Phase 8N RED)
=============================================================

Drives the design of Phase 8N: orchestration-layer DATA STRUCTURES that can HOLD
a full repair cycle - fix requests, execution results, and a retry state - WITHOUT
performing any action.

Expected API (pinned by these tests, lives in
`brains/self_healing_orchestrator.py`):
  * RepairCycle dataclass:
        fix_requests      (list, default [])
        execution_results (list, default [])
        retry_state       (default None)
  * SelfHealingOrchestrator:
        create_cycle() -> RepairCycle           (empty cycle)
        attach_fix_requests(cycle, fix_requests)        (mutates cycle in place)
        attach_execution_results(cycle, results)        (mutates cycle in place)
        attach_retry_state(cycle, state)                (mutates cycle in place)

STRUCTURAL ONLY: nothing here runs a repair, retries, loops, or wires into the
real orchestrator/Builder/Fixer. These tests are RED on purpose and fail for
MISSING IMPLEMENTATION ONLY: `brains.self_healing_orchestrator` does not exist
yet, so RepairCycle and SelfHealingOrchestrator are lazily imported inside helpers
and each test fails individually with ModuleNotFoundError.

The supporting types (FixRequest, FixExecutionResult, RetryController/RetryState)
already exist (Phases 8J/8L/8M) and are imported normally. Fully offline.
"""

import pytest

from brains.acceptance_brain import FixRequest
from brains.fixer_executor import FixExecutionResult
from brains.retry_controller import RetryController


def _RepairCycle():
    """Lazy import: RED until GREEN adds brains/self_healing_orchestrator.py."""
    from brains.self_healing_orchestrator import RepairCycle
    return RepairCycle


def _SelfHealingOrchestrator():
    """Lazy import: RED until GREEN adds brains/self_healing_orchestrator.py."""
    from brains.self_healing_orchestrator import SelfHealingOrchestrator
    return SelfHealingOrchestrator


# --------------------------------------------------------------------------- #
# RepairCycle
# --------------------------------------------------------------------------- #
def test_repair_cycle_initial_state():
    RepairCycle = _RepairCycle()
    cycle = RepairCycle()
    assert cycle.fix_requests == []
    assert cycle.execution_results == []
    assert cycle.retry_state is None


def test_repair_cycle_tracks_attempts():
    RepairCycle = _RepairCycle()
    state = RetryController().create(3)
    RetryController().record_attempt(state)  # attempts -> 1
    cycle = RepairCycle(retry_state=state)
    assert cycle.retry_state.attempts == 1


def test_repair_cycle_tracks_fix_requests():
    RepairCycle = _RepairCycle()
    requests = [FixRequest("Dashboard missing export button")]
    cycle = RepairCycle(fix_requests=requests)
    assert cycle.fix_requests == requests


# --------------------------------------------------------------------------- #
# SelfHealingOrchestrator
# --------------------------------------------------------------------------- #
def test_self_healing_orchestrator_creates_cycle():
    SelfHealingOrchestrator = _SelfHealingOrchestrator()
    RepairCycle = _RepairCycle()
    cycle = SelfHealingOrchestrator().create_cycle()
    assert isinstance(cycle, RepairCycle)
    assert cycle.fix_requests == []
    assert cycle.execution_results == []
    assert cycle.retry_state is None


def test_self_healing_orchestrator_attaches_retry_state():
    SelfHealingOrchestrator = _SelfHealingOrchestrator()
    orch = SelfHealingOrchestrator()
    cycle = orch.create_cycle()
    state = RetryController().create(3)
    orch.attach_retry_state(cycle, state)
    assert cycle.retry_state is state


def test_self_healing_orchestrator_attaches_fix_execution_results():
    SelfHealingOrchestrator = _SelfHealingOrchestrator()
    orch = SelfHealingOrchestrator()
    cycle = orch.create_cycle()
    results = [
        FixExecutionResult(description="Dashboard missing export button",
                           status="planned")
    ]
    orch.attach_execution_results(cycle, results)
    assert cycle.execution_results == results
