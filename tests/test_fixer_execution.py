#!/usr/bin/env python3
"""
Fixer execution tests  (AutoCorp CLI - Phase 8L RED)
====================================================

Drives the design of Phase 8L: a FixerExecutor that consumes FixerWorkItem
objects (the Phase 8K handoff objects) and returns a FixExecutionResult per item.

Expected API (pinned by these tests, lives in `brains/fixer_executor.py`):
  * FixExecutionResult - a dataclass carrying at least `description` (preserved
    from the work item) and `status` (a string).
  * FixerExecutor.execute(work_items) -> list[FixExecutionResult]
      - one result per work item, in the SAME order,
      - empty input -> empty list (no-op).

These tests are RED on purpose and fail for MISSING IMPLEMENTATION ONLY: the
module `brains.fixer_executor` does not exist yet, so `FixerExecutor` and
`FixExecutionResult` are lazily imported inside helpers and each test fails
individually with ModuleNotFoundError rather than collapsing the module into one
collection error.

`FixerWorkItem` already exists (Phase 8K) and is imported normally. Fully
offline: no model, no network. No production code is added in this phase.
"""

import pytest

from brains.acceptance_brain import FixerWorkItem


def _FixerExecutor():
    """Lazy import: RED until GREEN adds brains/fixer_executor.py."""
    from brains.fixer_executor import FixerExecutor
    return FixerExecutor


def _FixExecutionResult():
    """Lazy import: RED until GREEN adds brains/fixer_executor.py."""
    from brains.fixer_executor import FixExecutionResult
    return FixExecutionResult


# --------------------------------------------------------------------------- #
# 1. Execute a single work item -> one result
# --------------------------------------------------------------------------- #
def test_execute_fixer_work_item():
    FixerExecutor = _FixerExecutor()
    FixExecutionResult = _FixExecutionResult()
    results = FixerExecutor().execute([FixerWorkItem("Dashboard missing export button")])
    assert isinstance(results, list)
    assert len(results) == 1
    assert isinstance(results[0], FixExecutionResult)
    assert results[0].description == "Dashboard missing export button"


# --------------------------------------------------------------------------- #
# 2. Execute multiple work items -> multiple results
# --------------------------------------------------------------------------- #
def test_execute_multiple_fixer_work_items():
    FixerExecutor = _FixerExecutor()
    items = [
        FixerWorkItem("Dashboard missing export button"),
        FixerWorkItem("CSV export not visible"),
    ]
    results = FixerExecutor().execute(items)
    assert [r.description for r in results] == [
        "Dashboard missing export button",
        "CSV export not visible",
    ]


# --------------------------------------------------------------------------- #
# 3. Empty work-item list -> no-op (empty list)
# --------------------------------------------------------------------------- #
def test_empty_work_items_returns_noop():
    FixerExecutor = _FixerExecutor()
    assert FixerExecutor().execute([]) == []


# --------------------------------------------------------------------------- #
# 4. Execution preserves the input (priority) order
# --------------------------------------------------------------------------- #
def test_execution_preserves_priority_order():
    FixerExecutor = _FixerExecutor()
    items = [
        FixerWorkItem("first"),
        FixerWorkItem("second"),
        FixerWorkItem("third"),
    ]
    results = FixerExecutor().execute(items)
    assert [r.description for r in results] == ["first", "second", "third"]


# --------------------------------------------------------------------------- #
# 5. Each result carries a status
# --------------------------------------------------------------------------- #
def test_execution_result_contains_status():
    FixerExecutor = _FixerExecutor()
    results = FixerExecutor().execute([FixerWorkItem("Dashboard missing export button")])
    assert hasattr(results[0], "status")
    assert isinstance(results[0].status, str)
