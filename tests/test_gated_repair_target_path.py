#!/usr/bin/env python3
"""
GatedRepairFixer honors RepairAction.path  (AutoCorp CLI - Phase 8W RED)
========================================================================

Drives the design of Phase 8W: making `GatedRepairFixer.execute` HONOR a resolved
target path instead of always overwriting it with a placeholder. Phase 8V made the
adapter populate `FixerWorkItem.target_path` and 8U made `propose()` thread it into
`RepairAction.path` - but `GatedRepairFixer.execute` still blindly assigns every
action `repairs/repair_<i>.txt`, discarding the real target:

    RepairAction(path="ui/main_window.py")  -> GatedRepairFixer -> repairs/repair_0.txt

Desired:
    RepairAction(path="ui/main_window.py")  -> GatedRepairFixer -> ui/main_window.py
Fallback (unchanged):
    RepairAction(path=None)                 -> GatedRepairFixer -> repairs/repair_0.txt

Pinned design (RED until GREEN implements it):
  * When a proposed action already carries a `path` (a real target resolved
    upstream), GatedRepairFixer applies the gated write to THAT path.
  * When the proposed action has no path (None/empty), it falls back to the
    deterministic `repairs/repair_<index>.txt` placeholder - byte-for-byte today's
    behavior (BACKWARD COMPATIBLE).
  * Still WRITE-ONLY + GATED: only `executor.write_file` is used; the gate stays
    authoritative (a blocked write leaves the action NOT "applied"); no
    run_command, no subprocess, no shell. Order preserved.
  * propose(), apply(), and run_cycle() are UNCHANGED.

RED: the honor-path tests fail for MISSING IMPLEMENTATION ONLY - `execute` still
overwrites `action.path`, so a targeted write lands at `repairs/repair_0.txt`
instead of the resolved target (AssertionError comparing the recorded write path).
Each failure is reached inside its own test, so they fail individually. The
backward-compat / safety guards already pass and must STAY green. `GatedRepairFixer`,
`FixerWorkItem`, `RepairAction`, `FixerExecutor`, `Executor`, the gates, and the
orchestrator all already exist and are imported normally. No production code is
added or modified in this phase.

Fully offline: a SpyExecutor records write targets without disk; real gated writes
go to tmp_path (cwd redirected); a monkeypatched subprocess.run guards the
no-command path. No model, no network, no real shell.
"""

import subprocess

import pytest

from brains.acceptance_brain import FixerWorkItem
from brains.fixer_executor import FixerExecutor, RepairAction
from brains.gated_repair_fixer import GatedRepairFixer
from brains.self_healing_orchestrator import SelfHealingOrchestrator
from safety.executor import Executor, WriteResult, CommandResult
from safety.gate import AllowAllGate, CommandGate, Decision


class _DenyWriteGate(CommandGate):
    """A gate that blocks every write (and command)."""

    def review_write(self, path, content):
        return Decision.block("denied for test")

    def review_command(self, command, cwd):
        return Decision.block("denied for test")


class SpyExecutor:
    """Records write_file / run_command without touching disk or shell."""

    def __init__(self):
        self.writes = []
        self.runs = []

    def write_file(self, path, content):
        self.writes.append((path, content))
        return WriteResult(path, written=True)

    def run_command(self, command, cwd):
        self.runs.append(command)
        return CommandResult(command, returncode=0)


def _files_under(path):
    return [p for p in path.rglob("*") if p.is_file()]


# --------------------------------------------------------------------------- #
# 1. A resolved target path is honored (recorded write lands there)
# --------------------------------------------------------------------------- #
def test_honors_target_path_single():
    spy = SpyExecutor()
    GatedRepairFixer(spy).execute(
        [FixerWorkItem("Dashboard missing export button",
                       target_path="ui/main_window.py")])
    assert len(spy.writes) == 1
    assert spy.writes[0][0] == "ui/main_window.py"          # NOT repairs/repair_0.txt


# --------------------------------------------------------------------------- #
# 2. The description is still the written content
# --------------------------------------------------------------------------- #
def test_target_write_preserves_content():
    spy = SpyExecutor()
    GatedRepairFixer(spy).execute(
        [FixerWorkItem("Dashboard missing export button",
                       target_path="ui/main_window.py")])
    assert spy.writes[0] == ("ui/main_window.py", "Dashboard missing export button")


# --------------------------------------------------------------------------- #
# 3. Multiple targeted items each write to their own resolved path, in order
# --------------------------------------------------------------------------- #
def test_multiple_targets_each_honored():
    spy = SpyExecutor()
    GatedRepairFixer(spy).execute([
        FixerWorkItem("a", target_path="a.py"),
        FixerWorkItem("b", target_path="b.py"),
        FixerWorkItem("c", target_path="c.py"),
    ])
    assert [w[0] for w in spy.writes] == ["a.py", "b.py", "c.py"]


# --------------------------------------------------------------------------- #
# 4. A real gated write lands at the resolved target on disk
# --------------------------------------------------------------------------- #
def test_real_write_lands_at_target_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    GatedRepairFixer(Executor(AllowAllGate())).execute(
        [FixerWorkItem("Dashboard missing export button",
                       target_path="ui/main_window.py")])
    assert (tmp_path / "ui" / "main_window.py").is_file()    # landed at the target
    assert not (tmp_path / "repairs").exists()               # no placeholder used


# --------------------------------------------------------------------------- #
# 5. GUARD: path=None still falls back to the placeholder (recorded write)
# --------------------------------------------------------------------------- #
def test_path_none_uses_placeholder():
    spy = SpyExecutor()
    GatedRepairFixer(spy).execute(
        [FixerWorkItem("Dashboard missing export button")])   # no target_path
    assert len(spy.writes) == 1
    assert spy.writes[0][0] == "repairs/repair_0.txt"


# --------------------------------------------------------------------------- #
# 6. GUARD: a real placeholder write still lands under repairs/ on disk
# --------------------------------------------------------------------------- #
def test_real_placeholder_write_still_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    GatedRepairFixer(Executor(AllowAllGate())).execute(
        [FixerWorkItem("Dashboard missing export button")])
    assert (tmp_path / "repairs" / "repair_0.txt").is_file()


# --------------------------------------------------------------------------- #
# 7. GUARD: a blocked gate writes nothing and leaves the action NOT applied
#    (even with a resolved target path)
# --------------------------------------------------------------------------- #
def test_blocked_gate_prevents_target_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    actions = GatedRepairFixer(Executor(_DenyWriteGate())).execute(
        [FixerWorkItem("Dashboard missing export button",
                       target_path="ui/main_window.py")])
    assert _files_under(tmp_path) == []                      # gate blocked: nothing written
    assert all(a.status != "applied" for a in actions)


# --------------------------------------------------------------------------- #
# 8. GUARD: never calls run_command() or subprocess (with a resolved target)
# --------------------------------------------------------------------------- #
def test_never_calls_run_command_or_subprocess(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: runs.append(a))
    spy = SpyExecutor()
    GatedRepairFixer(spy).execute(
        [FixerWorkItem("Dashboard missing export button",
                       target_path="ui/main_window.py")])
    assert spy.runs == []                                    # no gated command
    assert runs == []                                        # no subprocess


# --------------------------------------------------------------------------- #
# 9. GUARD: order preserved and returned actions are applied (mixed target/None)
# --------------------------------------------------------------------------- #
def test_order_preserved_mixed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    actions = GatedRepairFixer(Executor(AllowAllGate())).execute([
        FixerWorkItem("first", target_path="first.py"),
        FixerWorkItem("second"),                              # placeholder
        FixerWorkItem("third", target_path="third.py"),
    ])
    assert [a.content for a in actions] == ["first", "second", "third"]
    assert all(a.status == "applied" for a in actions)


# --------------------------------------------------------------------------- #
# 10. GUARD: propose() unchanged - a targeted work item still yields a
#     "proposed" write whose path is the target
# --------------------------------------------------------------------------- #
def test_propose_unchanged():
    actions = FixerExecutor().propose(
        [FixerWorkItem("Dashboard missing export button",
                       target_path="ui/main_window.py")])
    assert actions[0].kind == "write"
    assert actions[0].status == "proposed"
    assert actions[0].path == "ui/main_window.py"
    assert actions[0].content == "Dashboard missing export button"


# --------------------------------------------------------------------------- #
# 11. GUARD: apply() unchanged - write applied; non-write rejected
# --------------------------------------------------------------------------- #
def test_apply_unchanged(tmp_path):
    action = RepairAction(kind="write", path=str(tmp_path / "main.py"),
                          content="x = 1\n")
    applied = FixerExecutor().apply([action], Executor(AllowAllGate()))
    assert applied[0].status == "applied"
    assert (tmp_path / "main.py").read_text() == "x = 1\n"
    spy = SpyExecutor()
    with pytest.raises(ValueError):
        FixerExecutor().apply([RepairAction(kind="run", command="echo hi")], spy)
    assert spy.runs == []


# --------------------------------------------------------------------------- #
# 12. GUARD: run_cycle() unchanged - GatedRepairFixer still drives a heal
# --------------------------------------------------------------------------- #
def test_run_cycle_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixer = GatedRepairFixer(Executor(AllowAllGate()))
    cycle = SelfHealingOrchestrator().run_cycle(
        [FixerWorkItem("Dashboard missing export button",
                       target_path="ui/main_window.py")],
        fixer=fixer, verify=lambda: True, max_attempts=3)
    assert cycle.healed is True
    assert cycle.retry_state.attempts == 1
