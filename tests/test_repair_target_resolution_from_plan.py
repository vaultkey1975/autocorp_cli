#!/usr/bin/env python3
"""
Repair target resolution from the plan  (AutoCorp CLI - Phase DS10 RED)
======================================================================

Drives Phase DS10: make acceptance-generated repairs target REAL project files
instead of the fallback placeholder. Phase 8V resolves a `target_path` only when
a failed acceptance row carries an explicit `file`/`path`/`filename` hint; real
acceptance criteria are semantic and carry NO such hint, so `target_path` stays
None, `FixerExecutor.propose` leaves the action path unset, and
`GatedRepairFixer` falls back to `repairs/repair_N.txt` - the heal lands in a
throwaway file and never fixes the actual code.

This phase extends `AcceptanceRepairAdapter.to_work_items` to ALSO accept the
build `plan` and, when a failed row has no direct hint, resolve a target file
from the plan:

    AcceptanceReport (no file hint)
        -> AcceptanceRepairAdapter.to_work_items(report, plan=plan)
        -> FixerWorkItem(target_path=<real project file>)
        -> FixerExecutor.propose -> RepairAction(path=<real file>)
        -> GatedRepairFixer -> writes the REAL file (not repairs/repair_N.txt)

Pinned design (RED until GREEN implements it; ADAPTER-ONLY, ADDITIVE):
  * `to_work_items(self, report, plan=None)` - `plan` is OPTIONAL and defaults to
    None, so legacy `to_work_items(report)` calls are unchanged.
  * Requirement A: when a failed row has no `file`/`path`/`filename` hint AND a
    plan is supplied, resolve `target_path` from the plan.
  * Requirement B: prefer the FIRST entry in `plan["build_order"]` that also
    appears (by path) in `plan["files"]`; build_order order wins over the files
    list order, and a build_order entry absent from files is skipped.
  * Requirement C: a direct file hint on the row still takes precedence over any
    plan resolution (8V behavior preserved).
  * Requirement D: if resolution fails completely (no hint, no usable plan),
    `target_path` stays None - the existing repairs/repair_N.txt fallback in
    GatedRepairFixer remains in force.
  * Requirement E: accepted/None/empty handling and legacy signature stay green.

RED mechanisms:
  * resolution tests call `to_work_items(report, plan=plan)`; today the method
    takes only `report`, so the kwarg raises TypeError (missing implementation).
  * the integration test shows the gated write still targets the placeholder
    today.

Fully offline: a fake AcceptanceReport, a plain dict plan, and a capturing fake
executor. No model, no network, no subprocess, no real disk writes. No production
code is changed in this phase.
"""

import types

import pytest

from brains.acceptance_repair_adapter import AcceptanceRepairAdapter
from brains.gated_repair_fixer import GatedRepairFixer


class _FakeReport:
    """Minimal AcceptanceReport: `.accepted` + `.results` (list of dict rows)."""

    def __init__(self, accepted, results):
        self.accepted = accepted
        self.results = results


def _fail_row(criterion, **hints):
    row = {"criterion": criterion, "check": "", "status": "fail", "detail": ""}
    row.update(hints)
    return row


def _unaccepted(*rows):
    return _FakeReport(accepted=False, results=list(rows))


class _CapturingExecutor:
    """Records each write_file(path, content); reports every write as written."""

    def __init__(self):
        self.writes = []

    def write_file(self, path, content):
        self.writes.append((path, content))
        return types.SimpleNamespace(written=True)


# --------------------------------------------------------------------------- #
# A/B. No hint -> resolve the first build_order file present in plan["files"]
# --------------------------------------------------------------------------- #
def test_no_hint_resolves_first_build_order_file():
    adapter = AcceptanceRepairAdapter()
    report = _unaccepted(_fail_row("Dashboard missing export button"))
    plan = {
        "files": [{"path": "a.py"}, {"path": "main.py"}],
        "build_order": ["a.py", "main.py"],
    }

    items = adapter.to_work_items(report, plan=plan)   # RED: TypeError (no plan param)

    assert len(items) == 1
    assert items[0].target_path == "a.py"


# --------------------------------------------------------------------------- #
# B. build_order order wins over the files-list order
# --------------------------------------------------------------------------- #
def test_build_order_priority_over_files_list():
    adapter = AcceptanceRepairAdapter()
    report = _unaccepted(_fail_row("Missing CRUD endpoint"))
    plan = {
        "files": [{"path": "util.py"}, {"path": "main.py"}],
        "build_order": ["main.py", "util.py"],
    }

    items = adapter.to_work_items(report, plan=plan)   # RED: TypeError today

    assert items[0].target_path == "main.py"


# --------------------------------------------------------------------------- #
# B. A build_order entry absent from plan["files"] is skipped
# --------------------------------------------------------------------------- #
def test_skips_build_order_entry_absent_from_files():
    adapter = AcceptanceRepairAdapter()
    report = _unaccepted(_fail_row("Broken validation"))
    plan = {
        "files": [{"path": "main.py"}],
        "build_order": ["ghost.py", "main.py"],
    }

    items = adapter.to_work_items(report, plan=plan)   # RED: TypeError today

    assert items[0].target_path == "main.py"


# --------------------------------------------------------------------------- #
# C. A direct file hint still takes precedence over plan resolution
# --------------------------------------------------------------------------- #
def test_direct_hint_takes_precedence_over_plan():
    adapter = AcceptanceRepairAdapter()
    report = _unaccepted(_fail_row("Bad export", file="explicit.py"))
    plan = {
        "files": [{"path": "main.py"}],
        "build_order": ["main.py"],
    }

    items = adapter.to_work_items(report, plan=plan)   # RED: TypeError today

    assert items[0].target_path == "explicit.py"


# --------------------------------------------------------------------------- #
# D. Resolution fails completely (no hint, empty plan) -> target_path stays None
# --------------------------------------------------------------------------- #
def test_unresolvable_plan_keeps_none():
    adapter = AcceptanceRepairAdapter()
    report = _unaccepted(_fail_row("Nothing to map"))
    plan = {"files": [], "build_order": []}

    items = adapter.to_work_items(report, plan=plan)   # RED: TypeError today

    assert items[0].target_path is None


# --------------------------------------------------------------------------- #
# E. Legacy signature (no plan) keeps None for a hintless row
#    (backward-compat guard - must stay GREEN)
# --------------------------------------------------------------------------- #
def test_legacy_no_plan_keeps_none():
    adapter = AcceptanceRepairAdapter()
    report = _unaccepted(_fail_row("Semantic failure, no hint"))

    items = adapter.to_work_items(report)              # legacy call, unchanged

    assert len(items) == 1
    assert items[0].target_path is None


# --------------------------------------------------------------------------- #
# E. Accepted report still yields no work items (guard - must stay GREEN)
# --------------------------------------------------------------------------- #
def test_accepted_report_yields_no_items():
    adapter = AcceptanceRepairAdapter()
    report = _FakeReport(accepted=True, results=[])

    assert adapter.to_work_items(report) == []


# --------------------------------------------------------------------------- #
# Integration: a plan-resolved target flows to the REAL gated write path
# (not the repairs/repair_N.txt placeholder)
# --------------------------------------------------------------------------- #
def test_resolved_target_flows_to_real_write_path():
    adapter = AcceptanceRepairAdapter()
    report = _unaccepted(_fail_row("Dashboard missing export button"))
    plan = {
        "files": [{"path": "main.py"}],
        "build_order": ["main.py"],
    }

    items = adapter.to_work_items(report, plan=plan)   # RED: TypeError today

    executor = _CapturingExecutor()
    GatedRepairFixer(executor).execute(items)

    written_paths = [p for p, _ in executor.writes]
    assert written_paths == ["main.py"]               # real file, not repairs/repair_0.txt
