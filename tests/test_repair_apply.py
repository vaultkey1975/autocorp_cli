#!/usr/bin/env python3
"""
Gated proposal application  (AutoCorp CLI - Phase 8S RED)
=========================================================

Drives the design of Phase 8S: the FIRST seam that may eventually MODIFY the
workspace. A FixerExecutor learns to APPLY a proposed write action by routing it
through the gated Executor.write_file(), flipping its status "proposed" ->
"applied".

    RepairAction(status="proposed")  ->  Executor.write_file()  ->  gate review
        ->  RepairAction(status="applied")

WRITE ACTIONS ONLY (this phase): no command execution, no subprocess, no
Executor.run_command(), no shell. A non-write ("run") action is rejected safely;
an unsupported object fails safely. The gate (CommandGate / WatchdogGate) inside
Executor.write_file stays authoritative - apply never writes around it.

Expected API (pinned by these tests; ADDITIVE to brains/fixer_executor.py):
  * FixerExecutor.apply(actions, executor) -> list[RepairAction]
      - applies each WRITE action via `executor.write_file(action.path,
        action.content)`, sets its status to "applied", preserving input order,
      - [] -> [] (no-op; executor untouched),
      - a non-write action (e.g. kind "run") is rejected with a controlled
        ValueError and NEVER executes a command/subprocess,
      - an unsupported action type fails with a controlled TypeError,
      - uses ONLY write_file: never run_command, never subprocess.run.

ADDITIVE ONLY: `propose` (8R), `execute`/FixExecutionResult (8L), and the
self-healing flow are unchanged.

RED: these tests fail for MISSING IMPLEMENTATION ONLY - `FixerExecutor.apply` does
not exist yet (-> AttributeError). `RepairAction`, `FixerExecutor`, `Executor`,
and the gates already exist and are imported normally. No production code is added
or modified in this phase.

Fully offline: real writes go to tmp_path through an AllowAllGate; Executor
methods and subprocess.run are spied. No model, no network, no real shell.
"""

import subprocess

import pytest

from brains.acceptance_brain import FixerWorkItem
from brains.fixer_executor import FixerExecutor, RepairAction
from safety.executor import Executor, WriteResult, CommandResult
from safety.gate import AllowAllGate


class _SpyExecutor:
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


def _write_action(path, content="x = 1\n"):
    return RepairAction(kind="write", path=path, content=content)


# --------------------------------------------------------------------------- #
# 1. Proposed write action can be applied through Executor.write_file()
# --------------------------------------------------------------------------- #
def test_proposed_write_action_is_applied(tmp_path):
    target = tmp_path / "main.py"
    executor = Executor(AllowAllGate())
    FixerExecutor().apply([_write_action(str(target), "x = 1\n")], executor)
    assert target.read_text() == "x = 1\n"          # really written (gated) to tmp


# --------------------------------------------------------------------------- #
# 2. Applied action status transitions "proposed" -> "applied"
# --------------------------------------------------------------------------- #
def test_status_transitions_to_applied(tmp_path):
    action = _write_action(str(tmp_path / "main.py"))
    assert action.status == "proposed"              # pre-state
    applied = FixerExecutor().apply([action], Executor(AllowAllGate()))
    assert applied[0].status == "applied"


# --------------------------------------------------------------------------- #
# 3. Empty action lists are a no-op
# --------------------------------------------------------------------------- #
def test_empty_actions_is_noop():
    spy = _SpyExecutor()
    assert FixerExecutor().apply([], spy) == []
    assert spy.writes == []                          # executor untouched


# --------------------------------------------------------------------------- #
# 4. Non-write actions are rejected safely (and never executed)
# --------------------------------------------------------------------------- #
def test_non_write_action_rejected_safely():
    spy = _SpyExecutor()
    run_action = RepairAction(kind="run", command="echo hi")
    with pytest.raises(ValueError):
        FixerExecutor().apply([run_action], spy)
    assert spy.runs == []                            # never executed
    assert spy.writes == []


# --------------------------------------------------------------------------- #
# 5. Unsupported action types fail safely
# --------------------------------------------------------------------------- #
def test_unsupported_action_type_fails_safely():
    spy = _SpyExecutor()
    with pytest.raises(TypeError):
        FixerExecutor().apply([object()], spy)


# --------------------------------------------------------------------------- #
# 6. Executor.write_file() is used for writes
# --------------------------------------------------------------------------- #
def test_write_file_is_used_for_writes():
    spy = _SpyExecutor()
    FixerExecutor().apply([_write_action("main.py", "x = 1\n")], spy)
    assert spy.writes == [("main.py", "x = 1\n")]


# --------------------------------------------------------------------------- #
# 7. Executor.run_command() is never used
# --------------------------------------------------------------------------- #
def test_run_command_never_used():
    spy = _SpyExecutor()
    FixerExecutor().apply([_write_action("main.py")], spy)
    assert spy.runs == []


# --------------------------------------------------------------------------- #
# 8. subprocess.run() is never used
# --------------------------------------------------------------------------- #
def test_subprocess_run_never_used(tmp_path, monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: runs.append(a))
    FixerExecutor().apply([_write_action(str(tmp_path / "main.py"))],
                          Executor(AllowAllGate()))
    assert runs == []


# --------------------------------------------------------------------------- #
# 9. Action order is preserved
# --------------------------------------------------------------------------- #
def test_action_order_preserved():
    spy = _SpyExecutor()
    actions = [_write_action("a.py", "a"),
               _write_action("b.py", "b"),
               _write_action("c.py", "c")]
    applied = FixerExecutor().apply(actions, spy)
    assert [p for p, _c in spy.writes] == ["a.py", "b.py", "c.py"]
    assert [a.status for a in applied] == ["applied", "applied", "applied"]


# --------------------------------------------------------------------------- #
# 10. Existing proposal-generation behavior remains unchanged
# --------------------------------------------------------------------------- #
def test_proposal_generation_unchanged(tmp_path):
    fe = FixerExecutor()
    # The new apply seam must not disturb propose(): apply a write (RED trigger),
    # then confirm proposals are still generated with status "proposed".
    fe.apply([_write_action(str(tmp_path / "main.py"))], Executor(AllowAllGate()))
    proposals = fe.propose([FixerWorkItem("Dashboard missing export button")])
    assert [p.status for p in proposals] == ["proposed"]


# --------------------------------------------------------------------------- #
# 11. Existing execute() behavior remains unchanged
# --------------------------------------------------------------------------- #
def test_execute_behavior_unchanged(tmp_path):
    fe = FixerExecutor()
    fe.apply([_write_action(str(tmp_path / "main.py"))], Executor(AllowAllGate()))
    results = fe.execute([FixerWorkItem("Dashboard missing export button")])
    assert results[0].status == "planned"           # execute() untouched
