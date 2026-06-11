#!/usr/bin/env python3
"""
Acceptance repair-planning tests  (AutoCorp CLI - Phase 8I RED)
==============================================================

Drives the design of Phase 8I (Acceptance Repair Planning): turning acceptance
failures into structured RepairTask PLANNING objects that the existing Fixer can
consume in a future phase. This phase creates planning objects only - it executes
NO fixes, invokes NO Fixer, and triggers NO retry, rerun, or rebuild.

These tests are RED on purpose:
  * `RepairTask` does not exist yet - it is lazily imported inside `_RepairTask()`
    so tests 1, 3, 4 fail individually (ImportError) rather than collapsing the
    whole module into one collection error.
  * `AcceptanceBrain.plan_repairs` is a STUB that raises NotImplementedError, so
    test 2 fails until GREEN implements it.

`AcceptanceResult` already exists (Phase 8H) and is imported normally. Fully
offline: pure data, no model, no network.
"""

import pytest

from brains.acceptance_brain import AcceptanceBrain, AcceptanceResult


def _RepairTask():
    """Lazy import: RED until GREEN adds RepairTask to brains.acceptance_brain."""
    from brains.acceptance_brain import RepairTask
    return RepairTask


# --------------------------------------------------------------------------- #
# 1. Acceptance failure generates repair tasks
# --------------------------------------------------------------------------- #
def test_acceptance_failure_generates_repair_tasks():
    RepairTask = _RepairTask()
    result = AcceptanceResult(
        passed=False,
        failures=["Dashboard missing export button", "CSV export not visible"],
    )
    assert AcceptanceBrain().plan_repairs(result) == [
        RepairTask("Dashboard missing export button"),
        RepairTask("CSV export not visible"),
    ]


# --------------------------------------------------------------------------- #
# 2. Acceptance success generates no repair tasks
# --------------------------------------------------------------------------- #
def test_acceptance_success_generates_no_repair_tasks():
    result = AcceptanceResult(passed=True, failures=[])
    assert AcceptanceBrain().plan_repairs(result) == []


# --------------------------------------------------------------------------- #
# 3. RepairTask preserves original failure text
# --------------------------------------------------------------------------- #
def test_repairtask_preserves_failure_text():
    RepairTask = _RepairTask()
    task = RepairTask("Dashboard missing export button")
    assert task.description == "Dashboard missing export button"


# --------------------------------------------------------------------------- #
# 4. AcceptanceBrain exposes a repair-planning method returning list[RepairTask]
# --------------------------------------------------------------------------- #
def test_plan_repairs_returns_list_of_repairtask():
    RepairTask = _RepairTask()
    result = AcceptanceResult(passed=False, failures=["Dashboard missing export button"])
    tasks = AcceptanceBrain().plan_repairs(result)
    assert isinstance(tasks, list)
    assert all(isinstance(t, RepairTask) for t in tasks)
