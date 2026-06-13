#!/usr/bin/env python3
"""
Acceptance -> Repair adapter tests  (AutoCorp CLI - Phase 8P RED)
================================================================

Drives the design of Phase 8P: the keystone seam that connects the acceptance
GATE's output to the repair pipeline's input. The gate emits an AcceptanceReport
(`accepted` + `results` list[dict]); the repair chain consumes an AcceptanceResult
(`passed` + `failures` list[str]). These two halves are currently unconnected -
the adapter bridges them and runs the existing, unchanged chain
(plan_repairs -> to_fix_requests -> to_fixer_work_items) to FixerWorkItem objects.

Expected API (pinned by these tests, lives in
`brains/acceptance_repair_adapter.py`):
  * AcceptanceRepairAdapter.to_acceptance_result(report) -> AcceptanceResult
      - report.accepted            -> result.passed
      - results with status "fail" -> result.failures (the `criterion` strings,
        in order). "pass" and "unverified" results are NOT repair work and are
        excluded (mirrors the gate: unverified never blocks).
      - None report -> a passed result with no failures (defensive).
  * AcceptanceRepairAdapter.to_work_items(report) -> list[FixerWorkItem]
      - an accepted (or None/empty) report yields [] (nothing to repair),
      - a failed report yields one FixerWorkItem per failed criterion, order and
        description preserved verbatim.

ADAPTER ONLY: this seam BUILDS the run_cycle inputs; it invokes no Fixer, runs no
repair loop, calls run_cycle nowhere, and changes no orchestrator flow. Wiring it
into the live Session.run is a separate, later, flag-guarded phase.

These tests are RED on purpose and fail for MISSING IMPLEMENTATION ONLY: the
module `brains.acceptance_repair_adapter` does not exist yet, so the adapter is
imported LAZILY inside a helper and each test fails individually with
ModuleNotFoundError rather than collapsing the file into one collection error.

The existing types (AcceptanceReport, AcceptanceResult, FixerWorkItem) are
imported normally. Fully offline: reports are constructed in-memory; no gate
evaluation, no model, no network, no subprocess, no file writes.
"""

import pytest

from brains.acceptance import AcceptanceReport
from brains.acceptance_brain import AcceptanceResult, FixerWorkItem


def _AcceptanceRepairAdapter():
    """Lazy import: RED until GREEN adds brains/acceptance_repair_adapter.py."""
    from brains.acceptance_repair_adapter import AcceptanceRepairAdapter
    return AcceptanceRepairAdapter


# --------------------------------------------------------------------------- #
# In-memory report builders (offline; no gate evaluation needed).
# --------------------------------------------------------------------------- #
def _result_row(criterion, status):
    return {"criterion": criterion, "check": "", "status": status, "detail": ""}


def _report(rows):
    """Build an AcceptanceReport from (criterion, status) pairs. `accepted` is
    True unless some row failed - exactly the gate's own rule."""
    results = [_result_row(c, s) for c, s in rows]
    passed = sum(1 for _c, s in rows if s == "pass")
    failed = sum(1 for _c, s in rows if s == "fail")
    unverified = sum(1 for _c, s in rows if s == "unverified")
    return AcceptanceReport(
        accepted=(failed == 0),
        total=len(results),
        passed=passed,
        failed=failed,
        unverified=unverified,
        results=results,
        summary="",
    )


# --------------------------------------------------------------------------- #
# 1. Accepted report produces empty work items
# --------------------------------------------------------------------------- #
def test_accepted_report_produces_empty_work_items():
    Adapter = _AcceptanceRepairAdapter()
    report = _report([("tests pass", "pass"), ("imports parse clean", "pass")])
    assert report.accepted is True
    assert Adapter().to_work_items(report) == []


# --------------------------------------------------------------------------- #
# 2. Failed report produces matching FixerWorkItems
# --------------------------------------------------------------------------- #
def test_failed_report_produces_matching_work_items():
    Adapter = _AcceptanceRepairAdapter()
    report = _report([
        ("Dashboard missing export button", "fail"),
        ("CSV export not visible", "fail"),
    ])
    items = Adapter().to_work_items(report)
    assert isinstance(items, list)
    assert len(items) == 2
    assert all(isinstance(i, FixerWorkItem) for i in items)


# --------------------------------------------------------------------------- #
# 3. Failure descriptions preserved verbatim
# --------------------------------------------------------------------------- #
def test_failure_descriptions_preserved_verbatim():
    Adapter = _AcceptanceRepairAdapter()
    report = _report([("Dashboard missing export button", "fail")])
    items = Adapter().to_work_items(report)
    assert items[0].description == "Dashboard missing export button"


# --------------------------------------------------------------------------- #
# 4. Failure order preserved
# --------------------------------------------------------------------------- #
def test_failure_order_preserved():
    Adapter = _AcceptanceRepairAdapter()
    report = _report([
        ("first", "fail"),
        ("second", "fail"),
        ("third", "fail"),
    ])
    items = Adapter().to_work_items(report)
    assert [i.description for i in items] == ["first", "second", "third"]


# --------------------------------------------------------------------------- #
# 5. Empty / None report is a defensive no-op
# --------------------------------------------------------------------------- #
def test_empty_and_none_report_is_noop():
    Adapter = _AcceptanceRepairAdapter()
    empty = _report([])
    assert Adapter().to_work_items(empty) == []
    assert Adapter().to_work_items(None) == []


# --------------------------------------------------------------------------- #
# 6. AcceptanceReport fields correctly map to AcceptanceResult
# --------------------------------------------------------------------------- #
def test_report_fields_map_to_acceptance_result():
    Adapter = _AcceptanceRepairAdapter()
    report = _report([
        ("tests pass", "pass"),
        ("Dashboard missing export button", "fail"),
        ("no deterministic check", "unverified"),
    ])
    result = Adapter().to_acceptance_result(report)
    assert isinstance(result, AcceptanceResult)
    # accepted -> passed (one failure means not accepted)
    assert result.passed is report.accepted
    assert result.passed is False
    # Only the FAILED criterion is repair work; pass + unverified are excluded.
    assert result.failures == ["Dashboard missing export button"]
