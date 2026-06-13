#!/usr/bin/env python3
"""
Repair proposal quality: target file awareness  (AutoCorp CLI - Phase 8U RED)
=============================================================================

Drives the design of Phase 8U: improving repair-proposal QUALITY. Today every
proposal is generic - `FixerExecutor.propose` maps a FixerWorkItem onto a
RepairAction whose `content` is the failure description and whose `path` is left
unset (None):

    FixerWorkItem(description)
        -> FixerExecutor.propose()
        -> RepairAction(kind="write", content=description, path=None)

This phase makes a proposal TARGET-AWARE: a FixerWorkItem may carry the path of the
file the repair should land in, and `propose` threads that into the RepairAction so
the downstream gated write targets a real workspace file rather than a placeholder.

Pinned design (RED until GREEN implements it; ADDITIVE):
  * FixerWorkItem gains an optional `target_path: str = None` field. The existing
    single-argument `FixerWorkItem(description)` construction keeps working, and a
    work item with no target reports `target_path is None`.
  * FixerExecutor.propose(work_items) becomes target-aware:
        - when a work item carries `target_path`, the produced RepairAction's
          `path` is set to it (target metadata), while `content` still PRESERVES
          the description verbatim and `kind`/`status` stay "write"/"proposed";
        - when a work item has no target (target_path is None), the action's
          `path` stays None - byte-for-byte today's behavior (BACKWARD COMPATIBLE);
        - ordering, one-action-per-item, the empty-input no-op, and the
          unsupported-item TypeError are all preserved.
  * apply(), execute(), RepairAction, run_cycle(), and AcceptanceRepairAdapter are
    UNCHANGED.

RED: these tests fail for MISSING IMPLEMENTATION ONLY. `FixerWorkItem` does not yet
accept/expose `target_path`, so:
  - constructing `FixerWorkItem(description, target_path=...)` raises
        TypeError: __init__() got an unexpected keyword argument 'target_path'
  - reading `.target_path` on a legacy work item raises
        AttributeError: 'FixerWorkItem' object has no attribute 'target_path'
Each failure is reached inside its own test (no module-level construction), so the
tests fail individually rather than collapsing into a collection error. The
backward-compat / existing-behavior guards below already pass and must STAY green.
`FixerWorkItem`, `FixerExecutor`, `RepairAction`, `Executor`, the gates and the
orchestrator all already exist and are imported normally. No production code is
added or modified in this phase.

Fully offline: pure data assertions plus a SpyExecutor / monkeypatched
subprocess.run guard the no-command path. No model, no network, no real shell.
"""

import subprocess

import pytest

from brains.acceptance_brain import FixerWorkItem
from brains.fixer_executor import FixerExecutor, RepairAction
from brains.self_healing_orchestrator import SelfHealingOrchestrator
from safety.executor import Executor, WriteResult, CommandResult
from safety.gate import AllowAllGate


class SpyExecutor:
    """Records write_file / run_command calls without touching disk or shell."""

    def __init__(self):
        self.writes = []
        self.runs = []

    def write_file(self, path, content):
        self.writes.append((path, content))
        return WriteResult(path, written=True)

    def run_command(self, command, cwd):
        self.runs.append(command)
        return CommandResult(command, returncode=0)


# --------------------------------------------------------------------------- #
# 1. FixerWorkItem accepts a target_path                        (requirement 2)
# --------------------------------------------------------------------------- #
def test_work_item_accepts_target_path():
    item = FixerWorkItem("Dashboard missing export button",
                         target_path="ui/main_window.py")
    assert item.description == "Dashboard missing export button"
    assert item.target_path == "ui/main_window.py"


# --------------------------------------------------------------------------- #
# 2. Legacy work item reports target_path is None      (requirements 2 & 7)
# --------------------------------------------------------------------------- #
def test_work_item_target_path_defaults_none():
    # The additive field defaults to None so single-arg construction still works.
    item = FixerWorkItem("Dashboard missing export button")
    assert item.target_path is None


# --------------------------------------------------------------------------- #
# 3. Proposal carries the target path metadata                  (requirement 2)
# --------------------------------------------------------------------------- #
def test_proposal_carries_target_path():
    actions = FixerExecutor().propose(
        [FixerWorkItem("Dashboard missing export button",
                       target_path="ui/main_window.py")])
    assert len(actions) == 1
    assert actions[0].path == "ui/main_window.py"


# --------------------------------------------------------------------------- #
# 4. Proposal preserves the FixerWorkItem description            (requirement 1)
# --------------------------------------------------------------------------- #
def test_proposal_preserves_description_with_target():
    actions = FixerExecutor().propose(
        [FixerWorkItem("Dashboard missing export button",
                       target_path="ui/main_window.py")])
    assert actions[0].content == "Dashboard missing export button"


# --------------------------------------------------------------------------- #
# 5. Targeted proposal is still a proposed write              (requirements 2 & 7)
# --------------------------------------------------------------------------- #
def test_targeted_proposal_kind_and_status():
    actions = FixerExecutor().propose(
        [FixerWorkItem("CSV export not visible", target_path="export.py")])
    assert actions[0].kind == "write"
    assert actions[0].status == "proposed"


# --------------------------------------------------------------------------- #
# 6. Multiple work items produce multiple actions                (requirement 4)
# --------------------------------------------------------------------------- #
def test_multiple_targeted_work_items_produce_multiple_actions():
    items = [
        FixerWorkItem("a", target_path="a.py"),
        FixerWorkItem("b", target_path="b.py"),
        FixerWorkItem("c", target_path="c.py"),
    ]
    actions = FixerExecutor().propose(items)
    assert len(actions) == 3
    assert all(isinstance(a, RepairAction) for a in actions)


# --------------------------------------------------------------------------- #
# 7. Targeted proposals preserve ordering                        (requirement 3)
# --------------------------------------------------------------------------- #
def test_targeted_proposals_preserve_order():
    items = [
        FixerWorkItem("first", target_path="first.py"),
        FixerWorkItem("second", target_path="second.py"),
        FixerWorkItem("third", target_path="third.py"),
    ]
    actions = FixerExecutor().propose(items)
    assert [a.path for a in actions] == ["first.py", "second.py", "third.py"]
    assert [a.content for a in actions] == ["first", "second", "third"]


# --------------------------------------------------------------------------- #
# 8. Empty work-item lists return empty action lists  (requirement 5; guard)
# --------------------------------------------------------------------------- #
def test_empty_work_items_returns_empty():
    assert FixerExecutor().propose([]) == []


# --------------------------------------------------------------------------- #
# 9. Unsupported work-item types still fail safely    (requirement 6; guard)
# --------------------------------------------------------------------------- #
def test_unsupported_work_item_fails_safely():
    with pytest.raises((TypeError, ValueError)):
        FixerExecutor().propose([object()])


# --------------------------------------------------------------------------- #
# 10. Existing propose() behavior remains backward compatible (requirement 7; guard)
#     A work item with NO target still yields the generic, path-less proposal.
# --------------------------------------------------------------------------- #
def test_existing_propose_no_target_backward_compatible():
    actions = FixerExecutor().propose(
        [FixerWorkItem("Dashboard missing export button")])
    assert len(actions) == 1
    action = actions[0]
    assert action.kind == "write"
    assert action.status == "proposed"
    assert action.content == "Dashboard missing export button"
    assert action.path is None                      # unchanged: generic proposal


# --------------------------------------------------------------------------- #
# 11. Existing apply() behavior remains unchanged       (requirement 8; guard)
# --------------------------------------------------------------------------- #
def test_existing_apply_unchanged(tmp_path):
    action = RepairAction(kind="write", path=str(tmp_path / "main.py"),
                          content="x = 1\n")
    applied = FixerExecutor().apply([action], Executor(AllowAllGate()))
    assert applied[0].status == "applied"
    assert (tmp_path / "main.py").read_text() == "x = 1\n"
    spy = SpyExecutor()
    with pytest.raises(ValueError):                  # non-write still rejected
        FixerExecutor().apply([RepairAction(kind="run", command="echo hi")], spy)
    assert spy.runs == []


# --------------------------------------------------------------------------- #
# 12. Existing execute() behavior remains unchanged    (requirement 10; guard)
# --------------------------------------------------------------------------- #
def test_existing_execute_unchanged():
    results = FixerExecutor().execute(
        [FixerWorkItem("Dashboard missing export button")])
    assert len(results) == 1
    assert results[0].status == "planned"
    assert results[0].description == "Dashboard missing export button"


# --------------------------------------------------------------------------- #
# 13. Existing run_cycle() behavior remains unchanged   (requirement 9; guard)
#     Both legacy execute-mode and proposal-mode still drive correctly, and no
#     command/subprocess is ever reached.
# --------------------------------------------------------------------------- #
def test_existing_run_cycle_unchanged(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: runs.append(a))

    # Legacy execute-mode (no executor) heals exactly as Phase 8O.
    cycle = SelfHealingOrchestrator().run_cycle(
        [FixerWorkItem("Dashboard missing export button")],
        fixer=FixerExecutor(), verify=lambda: True, max_attempts=3)
    assert cycle.healed is True
    assert cycle.execution_results[0].status == "planned"

    # Proposal-mode (8T) still drives propose -> apply through the gated executor.
    spy = SpyExecutor()
    cycle2 = SelfHealingOrchestrator().run_cycle(
        [FixerWorkItem("Dashboard missing export button")],
        fixer=FixerExecutor(), verify=lambda: True, max_attempts=3, executor=spy)
    assert cycle2.healed is True
    assert spy.runs == []                            # no gated command
    assert runs == []                                # no subprocess
