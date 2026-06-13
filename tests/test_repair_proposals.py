#!/usr/bin/env python3
"""
Gated dry-run repair proposals  (AutoCorp CLI - Phase 8R RED)
=============================================================

Drives the design of Phase 8R: a GATED DRY-RUN seam. The FixerExecutor learns to
PROPOSE repairs as inert, gate-reviewable RepairAction objects WITHOUT performing
any real file write, command run, subprocess, or workspace mutation. Actually
applying a proposal (and submitting it to the gate) is a separate, later phase.

    Acceptance Failure -> FixRequest -> FixerWorkItem -> FixerExecutor
        -> RepairAction(status="proposed")

Expected API (pinned by these tests; ADDITIVE to brains/fixer_executor.py):
  * RepairAction dataclass:
        kind     ("write" | "run")
        path     (optional, for "write")
        command  (optional, for "run")
        content  (optional, for "write")
        status   (defaults to "proposed")
  * FixerExecutor.propose(work_items) -> list[RepairAction]
        - one or more proposals per FixerWorkItem, in input order,
        - empty input -> empty list (no-op),
        - an unsupported work-item type fails safely (controlled TypeError /
          ValueError, never a silent or dangerous action),
        - PURELY data: no file write, no command, no subprocess.

ADDITIVE ONLY: the existing `FixerExecutor.execute(...) -> FixExecutionResult`
(status "planned") contract is NOT changed - `propose` is a new, separate method.

RED: these tests fail for MISSING IMPLEMENTATION ONLY. `RepairAction` does not
exist yet (lazy import -> ImportError) and `FixerExecutor.propose` does not exist
yet (-> AttributeError). `FixerExecutor`, `FixerWorkItem`, and the orchestrator
already exist and are imported normally. No production code is added or modified
in this phase.

Fully offline: no model, no network, no subprocess, no writes outside tmp_path.
"""

import subprocess

import pytest

from brains.acceptance_brain import FixerWorkItem
from brains.fixer_executor import FixerExecutor
from brains.self_healing_orchestrator import SelfHealingOrchestrator, RepairCycle
from safety.executor import Executor


def _RepairAction():
    """Lazy import: RED until GREEN adds RepairAction to brains/fixer_executor.py."""
    from brains.fixer_executor import RepairAction
    return RepairAction


# --------------------------------------------------------------------------- #
# 1. Failed FixerWorkItem produces one or more RepairAction objects
# --------------------------------------------------------------------------- #
def test_failed_work_item_produces_repair_actions():
    RepairAction = _RepairAction()
    actions = FixerExecutor().propose([FixerWorkItem("Dashboard missing export button")])
    assert isinstance(actions, list)
    assert len(actions) >= 1
    assert all(isinstance(a, RepairAction) for a in actions)


# --------------------------------------------------------------------------- #
# 2. RepairAction defaults to status="proposed"
# --------------------------------------------------------------------------- #
def test_repair_action_defaults_to_proposed():
    RepairAction = _RepairAction()
    action = RepairAction(kind="write")
    assert action.status == "proposed"


# --------------------------------------------------------------------------- #
# 3. Write actions contain path and content
# --------------------------------------------------------------------------- #
def test_write_action_contains_path_and_content():
    RepairAction = _RepairAction()
    action = RepairAction(kind="write", path="ui/main_window.py", content="x = 1\n")
    assert action.kind == "write"
    assert action.path == "ui/main_window.py"
    assert action.content == "x = 1\n"


# --------------------------------------------------------------------------- #
# 4. Run actions contain command
# --------------------------------------------------------------------------- #
def test_run_action_contains_command():
    RepairAction = _RepairAction()
    action = RepairAction(kind="run", command="python -m pytest")
    assert action.kind == "run"
    assert action.command == "python -m pytest"


# --------------------------------------------------------------------------- #
# 5. Empty work-item lists return empty action lists
# --------------------------------------------------------------------------- #
def test_empty_work_items_returns_empty_actions():
    assert FixerExecutor().propose([]) == []


# --------------------------------------------------------------------------- #
# 6. Unsupported work-item types fail safely
# --------------------------------------------------------------------------- #
def test_unsupported_work_item_fails_safely():
    # A bare object is not a FixerWorkItem: proposal must fail in a CONTROLLED way
    # (typed error), never produce a malformed or dangerous action.
    with pytest.raises((TypeError, ValueError)):
        FixerExecutor().propose([object()])


# --------------------------------------------------------------------------- #
# 7. No filesystem writes occur during proposal generation
# --------------------------------------------------------------------------- #
def test_no_filesystem_writes_during_proposal(tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(Executor, "write_file",
                        lambda self, path, content: writes.append(path))

    FixerExecutor().propose([FixerWorkItem("Dashboard missing export button")])

    assert writes == []                         # no sanctioned write path used
    assert list(tmp_path.iterdir()) == []       # nothing written to disk


# --------------------------------------------------------------------------- #
# 8. No command execution occurs during proposal generation
# --------------------------------------------------------------------------- #
def test_no_command_execution_during_proposal(monkeypatch):
    runs = []
    monkeypatch.setattr(Executor, "run_command",
                        lambda self, command, cwd: runs.append(command))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: runs.append(a))

    FixerExecutor().propose([FixerWorkItem("Dashboard missing export button")])

    assert runs == []                           # nothing executed


# --------------------------------------------------------------------------- #
# 9. FixerExecutor returns proposals rather than executing repairs
# --------------------------------------------------------------------------- #
def test_returns_proposals_not_executions():
    actions = FixerExecutor().propose([FixerWorkItem("Dashboard missing export button")])
    assert actions                              # non-empty
    # Every action is a PROPOSAL - never an applied/executed/done state.
    assert all(a.status == "proposed" for a in actions)
    assert all(a.status not in ("applied", "executed", "done") for a in actions)


# --------------------------------------------------------------------------- #
# 10. Existing self-healing orchestration can consume proposal output
# --------------------------------------------------------------------------- #
def test_orchestrator_can_consume_proposals():
    proposals = FixerExecutor().propose([FixerWorkItem("Dashboard missing export button")])
    orch = SelfHealingOrchestrator()
    cycle = orch.create_cycle()
    orch.attach_execution_results(cycle, proposals)
    assert isinstance(cycle, RepairCycle)
    assert cycle.execution_results == proposals
    assert cycle.healed is False                # consuming proposals executes nothing
