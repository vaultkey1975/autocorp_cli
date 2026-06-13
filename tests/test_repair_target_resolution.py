#!/usr/bin/env python3
"""
Repair target resolution in the adapter  (AutoCorp CLI - Phase 8V RED)
======================================================================

Drives the design of Phase 8V: ACTIVATING the target-aware repair proposals built
in 8U. Phase 8U gave FixerWorkItem an optional `target_path` and made
FixerExecutor.propose thread it into a RepairAction's `path`, but nothing upstream
ever populates it - so in the live flow every work item still has
`target_path=None` and the capability is dormant.

This phase makes `AcceptanceRepairAdapter.to_work_items(report)` resolve a TARGET
FILE for each failure from a deterministic hint on the acceptance result row and
attach it to the emitted FixerWorkItem:

    AcceptanceReport.results[ {criterion, status, file?/path?/filename?} ]
        -> AcceptanceRepairAdapter.to_work_items()
        -> FixerWorkItem(description=<criterion>, target_path=<resolved hint>)

Pinned design (RED until GREEN implements it; ADAPTER-ONLY, ADDITIVE):
  * When a FAILED result row carries a deterministic file hint - any of
    `file`, `path`, or `filename` - that value becomes the work item's
    `target_path`.
  * When a failed row carries no such hint, `target_path` stays None - byte-for-byte
    today's behavior (BACKWARD COMPATIBLE; the None fallback remains valid).
  * One work item per failed criterion, order preserved, descriptions preserved
    verbatim, target_path paired with its originating failure row.
  * `to_acceptance_result`, the AcceptanceBrain chain, FixerWorkItem, accepted/None
    handling, and every other contract are UNCHANGED.

RED: the target-resolution tests fail for MISSING IMPLEMENTATION ONLY. The adapter
already builds `FixerWorkItem(description)` (target_path defaults to None from 8U),
so a hint-bearing failure currently yields `target_path is None` - the assertions
below fail with AssertionError (None != the expected path) because the adapter does
not yet read the hint. Each failure is reached inside its own test, so the tests
fail individually. The backward-compat / existing-behavior guards already pass and
must STAY green. `AcceptanceRepairAdapter`, `AcceptanceReport`, `AcceptanceResult`,
and `FixerWorkItem` all already exist and are imported normally. No production code
is added or modified in this phase.

Fully offline: reports are built in-memory; no gate evaluation, no model, no
network, no subprocess, no file writes.
"""

import pytest

from brains.acceptance import AcceptanceReport
from brains.acceptance_brain import AcceptanceResult, FixerWorkItem
from brains.acceptance_repair_adapter import AcceptanceRepairAdapter


# --------------------------------------------------------------------------- #
# In-memory report builders (offline; no gate evaluation needed).
# --------------------------------------------------------------------------- #
def _row(criterion, status, **hint):
    """A result row; `hint` adds deterministic file-hint keys (file/path/filename)."""
    row = {"criterion": criterion, "check": "", "status": status, "detail": ""}
    row.update(hint)
    return row


def _report(rows):
    """Build an AcceptanceReport from pre-built result-row dicts. `accepted` is
    True unless some row failed - exactly the gate's own rule."""
    passed = sum(1 for r in rows if r["status"] == "pass")
    failed = sum(1 for r in rows if r["status"] == "fail")
    unverified = sum(1 for r in rows if r["status"] == "unverified")
    return AcceptanceReport(
        accepted=(failed == 0),
        total=len(rows),
        passed=passed,
        failed=failed,
        unverified=unverified,
        results=rows,
        summary="",
    )


# --------------------------------------------------------------------------- #
# 1. A `file` hint becomes target_path                          (requirement 1)
# --------------------------------------------------------------------------- #
def test_file_hint_becomes_target_path():
    report = _report([_row("Dashboard missing export button", "fail",
                           file="ui/main_window.py")])
    items = AcceptanceRepairAdapter().to_work_items(report)
    assert len(items) == 1
    assert items[0].target_path == "ui/main_window.py"
    assert items[0].description == "Dashboard missing export button"


# --------------------------------------------------------------------------- #
# 2. A `path` hint becomes target_path                          (requirement 1)
# --------------------------------------------------------------------------- #
def test_path_hint_becomes_target_path():
    report = _report([_row("CSV export not visible", "fail", path="export.py")])
    items = AcceptanceRepairAdapter().to_work_items(report)
    assert items[0].target_path == "export.py"


# --------------------------------------------------------------------------- #
# 3. A `filename` hint becomes target_path                      (requirement 1)
# --------------------------------------------------------------------------- #
def test_filename_hint_becomes_target_path():
    report = _report([_row("crud add fails", "fail", filename="crud.py")])
    items = AcceptanceRepairAdapter().to_work_items(report)
    assert items[0].target_path == "crud.py"


# --------------------------------------------------------------------------- #
# 4. Ordering is preserved with resolved paths                  (requirement 3)
# --------------------------------------------------------------------------- #
def test_ordering_preserved_with_paths():
    report = _report([
        _row("first", "fail", file="a.py"),
        _row("second", "fail", file="b.py"),
        _row("third", "fail", file="c.py"),
    ])
    items = AcceptanceRepairAdapter().to_work_items(report)
    assert [i.description for i in items] == ["first", "second", "third"]
    assert [i.target_path for i in items] == ["a.py", "b.py", "c.py"]


# --------------------------------------------------------------------------- #
# 5. Multiple failures preserve per-failure paths (hint + no-hint mix)
#                                                            (requirements 1 & 4)
# --------------------------------------------------------------------------- #
def test_multiple_failures_preserve_paths():
    report = _report([
        _row("Dashboard missing export button", "fail", file="ui/main_window.py"),
        _row("no resolvable file", "fail"),                    # no hint -> None
        _row("crud add fails", "fail", filename="crud.py"),
    ])
    items = AcceptanceRepairAdapter().to_work_items(report)
    assert len(items) == 3
    assert [i.target_path for i in items] == ["ui/main_window.py", None, "crud.py"]
    assert [i.description for i in items] == [
        "Dashboard missing export button", "no resolvable file", "crud add fails"]


# --------------------------------------------------------------------------- #
# 6. A failure with no hint resolves to None         (requirement 2; guard)
#     Backward compatible: None remains a valid fallback. (Passes today.)
# --------------------------------------------------------------------------- #
def test_missing_hint_becomes_none():
    report = _report([_row("Dashboard missing export button", "fail")])
    items = AcceptanceRepairAdapter().to_work_items(report)
    assert len(items) == 1
    assert items[0].target_path is None
    assert items[0].description == "Dashboard missing export button"


# --------------------------------------------------------------------------- #
# 7. Accepted reports remain []                       (requirement 5; guard)
# --------------------------------------------------------------------------- #
def test_accepted_report_remains_empty():
    report = _report([_row("tests pass", "pass"), _row("imports clean", "pass")])
    assert report.accepted is True
    assert AcceptanceRepairAdapter().to_work_items(report) == []


# --------------------------------------------------------------------------- #
# 8. Empty / None reports remain []                   (requirement 6; guard)
# --------------------------------------------------------------------------- #
def test_empty_and_none_report_remains_empty():
    assert AcceptanceRepairAdapter().to_work_items(_report([])) == []
    assert AcceptanceRepairAdapter().to_work_items(None) == []


# --------------------------------------------------------------------------- #
# 9. Existing to_acceptance_result behavior unchanged (requirement 7; guard)
#     Still maps accepted->passed and only "fail" criteria -> failures.
# --------------------------------------------------------------------------- #
def test_acceptance_result_unchanged():
    report = _report([
        _row("tests pass", "pass"),
        _row("Dashboard missing export button", "fail", file="ui/main_window.py"),
        _row("no deterministic check", "unverified"),
    ])
    result = AcceptanceRepairAdapter().to_acceptance_result(report)
    assert isinstance(result, AcceptanceResult)
    assert result.passed is report.accepted
    assert result.passed is False
    # Only the FAILED criterion is repair work; pass + unverified are excluded.
    # The file hint does not leak into the failure strings.
    assert result.failures == ["Dashboard missing export button"]


# --------------------------------------------------------------------------- #
# 10. Existing FixerWorkItem behavior unchanged       (requirement 8; guard)
# --------------------------------------------------------------------------- #
def test_existing_fixerworkitem_behavior_unchanged():
    legacy = FixerWorkItem("Dashboard missing export button")
    assert legacy.description == "Dashboard missing export button"
    assert legacy.target_path is None                  # additive default (8U)
    targeted = FixerWorkItem("desc", target_path="ui/main_window.py")
    assert targeted.target_path == "ui/main_window.py"


# --------------------------------------------------------------------------- #
# 11. Failed report still produces matching work items with verbatim
#     descriptions (requirement: adapter contract preserved; guard)
# --------------------------------------------------------------------------- #
def test_failed_report_descriptions_preserved():
    report = _report([
        _row("Dashboard missing export button", "fail"),
        _row("CSV export not visible", "fail"),
    ])
    items = AcceptanceRepairAdapter().to_work_items(report)
    assert len(items) == 2
    assert all(isinstance(i, FixerWorkItem) for i in items)
    assert [i.description for i in items] == [
        "Dashboard missing export button", "CSV export not visible"]
