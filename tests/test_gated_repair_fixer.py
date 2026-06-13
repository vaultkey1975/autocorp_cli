#!/usr/bin/env python3
"""
Gated live repair fixer + run_cycle integration  (AutoCorp CLI - Phase 8T RED)
==============================================================================

Drives the design of Phase 8T: a small additive `GatedRepairFixer(executor)` whose
`.execute(work_items)` runs the existing propose() -> apply() chain through a GATED
Executor, so `SelfHealingOrchestrator.run_cycle(..., fixer=GatedRepairFixer(...))`
can drive REAL, gated, write-only repairs.

    work_items -> GatedRepairFixer.execute
                    -> FixerExecutor.propose()  (RepairAction[], "proposed")
                    -> FixerExecutor.apply(actions, executor)  (gated write_file)
                    -> RepairAction[] ("applied")

Locked scope (pinned by these tests):
  * GatedRepairFixer(executor); execute(work_items) -> list[RepairAction].
  * run_cycle's 8O contract is UNCHANGED - it still only calls `fixer.execute()`.
  * Session.run() is UNCHANGED this phase: it still wires the inert FixerExecutor(),
    so an enabled self_heal run writes no repair files (deferred to a later 8U).
  * Write actions only: no run_command, no subprocess, no shell.
  * The gate (CommandGate/WatchdogGate) inside write_file stays authoritative: a
    blocking gate writes nothing and leaves the action NOT "applied".

RED: these tests fail for MISSING IMPLEMENTATION ONLY - `brains.gated_repair_fixer`
does not exist yet (lazy import -> ModuleNotFoundError). Everything else
(FixerExecutor.propose/apply, RepairAction, run_cycle, Executor, the gates,
Session) already exists and is imported normally. No production code is added or
modified in this phase.

Fully offline: real gated writes go to tmp_path (cwd redirected); the spy executor
and a monkeypatched subprocess.run guard the no-command path. No model, no network.
"""

import os
import subprocess

import pytest

from brains.acceptance_brain import FixerWorkItem
from brains.fixer_executor import RepairAction
from brains.self_healing_orchestrator import SelfHealingOrchestrator
from core import orchestrator as orch
from core.orchestrator import Session
from safety.executor import Executor, WriteResult, CommandResult
from safety.gate import AllowAllGate, CommandGate, Decision


def _GatedRepairFixer():
    """Lazy import: RED until GREEN adds brains/gated_repair_fixer.py."""
    from brains.gated_repair_fixer import GatedRepairFixer
    return GatedRepairFixer


class _DenyWriteGate(CommandGate):
    """A gate that blocks every write (and command)."""

    def review_write(self, path, content):
        return Decision.block("denied for test")

    def review_command(self, command, cwd):
        return Decision.block("denied for test")


class _SpyExecutor:
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


class _FakeVerify:
    """Scripted re-verification: pops the next bool per call, else False."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self._results.pop(0) if self._results else False


def _files_under(path):
    return [p for p in path.rglob("*") if p.is_file()]


# --------------------------------------------------------------------------- #
# 1. execute() returns applied RepairAction objects
# --------------------------------------------------------------------------- #
def test_execute_returns_applied_actions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    GatedRepairFixer = _GatedRepairFixer()
    actions = GatedRepairFixer(Executor(AllowAllGate())).execute(
        [FixerWorkItem("Dashboard missing export button")])
    assert isinstance(actions, list)
    assert len(actions) == 1
    assert isinstance(actions[0], RepairAction)
    assert actions[0].status == "applied"


# --------------------------------------------------------------------------- #
# 2. Real gated write through Executor.write_file() into tmp_path
# --------------------------------------------------------------------------- #
def test_performs_real_gated_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    GatedRepairFixer = _GatedRepairFixer()
    GatedRepairFixer(Executor(AllowAllGate())).execute(
        [FixerWorkItem("Dashboard missing export button")])
    assert _files_under(tmp_path), "expected a real gated write under tmp_path"


# --------------------------------------------------------------------------- #
# 3. One action per work item; order preserved
# --------------------------------------------------------------------------- #
def test_one_action_per_work_item_order_preserved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    GatedRepairFixer = _GatedRepairFixer()
    items = [FixerWorkItem("first"), FixerWorkItem("second"), FixerWorkItem("third")]
    actions = GatedRepairFixer(Executor(AllowAllGate())).execute(items)
    assert len(actions) == 3
    assert [a.content for a in actions] == ["first", "second", "third"]
    assert all(a.status == "applied" for a in actions)


# --------------------------------------------------------------------------- #
# 4. Empty work-item list returns []
# --------------------------------------------------------------------------- #
def test_empty_work_items_returns_empty():
    GatedRepairFixer = _GatedRepairFixer()
    spy = _SpyExecutor()
    assert GatedRepairFixer(spy).execute([]) == []
    assert spy.writes == []          # executor untouched


# --------------------------------------------------------------------------- #
# 5. execute() never calls run_command() or subprocess
# --------------------------------------------------------------------------- #
def test_never_calls_run_command_or_subprocess(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: runs.append(a))
    GatedRepairFixer = _GatedRepairFixer()
    spy = _SpyExecutor()
    GatedRepairFixer(spy).execute([FixerWorkItem("Dashboard missing export button")])
    assert spy.runs == []            # no gated command
    assert runs == []                # no subprocess


# --------------------------------------------------------------------------- #
# 6. run_cycle heals when verification transitions False -> True
# --------------------------------------------------------------------------- #
def test_run_cycle_heals_when_verify_flips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    GatedRepairFixer = _GatedRepairFixer()
    fixer = GatedRepairFixer(Executor(AllowAllGate()))
    cycle = SelfHealingOrchestrator().run_cycle(
        [FixerWorkItem("Dashboard missing export button")],
        fixer=fixer, verify=_FakeVerify([False, True]), max_attempts=3)
    assert cycle.healed is True
    assert cycle.retry_state.attempts == 2


# --------------------------------------------------------------------------- #
# 7. run_cycle still exhausts retry bounds when verification never passes
# --------------------------------------------------------------------------- #
def test_run_cycle_exhausts_when_verify_never_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    GatedRepairFixer = _GatedRepairFixer()
    fixer = GatedRepairFixer(Executor(AllowAllGate()))
    cycle = SelfHealingOrchestrator().run_cycle(
        [FixerWorkItem("x")], fixer=fixer,
        verify=_FakeVerify([False, False, False]), max_attempts=3)
    assert cycle.healed is False
    assert cycle.retry_state.attempts == 3
    assert cycle.retry_state.exhausted is True


# --------------------------------------------------------------------------- #
# 8. run_cycle contract unchanged: interacts only through fixer.execute()
# --------------------------------------------------------------------------- #
def test_run_cycle_interacts_only_via_execute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    GatedRepairFixer = _GatedRepairFixer()
    fixer = GatedRepairFixer(Executor(AllowAllGate()))
    calls = []
    orig = fixer.execute
    monkeypatch.setattr(fixer, "execute",
                        lambda work_items: calls.append(list(work_items)) or orig(work_items))
    SelfHealingOrchestrator().run_cycle(
        [FixerWorkItem("x")], fixer=fixer,
        verify=_FakeVerify([False, True]), max_attempts=3)
    assert len(calls) == 2           # driven purely through execute(), once per attempt


# --------------------------------------------------------------------------- #
# 9. Blocked gate prevents file modification; action NOT marked "applied"
# --------------------------------------------------------------------------- #
def test_blocked_gate_prevents_write_and_applied(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    GatedRepairFixer = _GatedRepairFixer()
    actions = GatedRepairFixer(Executor(_DenyWriteGate())).execute(
        [FixerWorkItem("Dashboard missing export button")])
    assert _files_under(tmp_path) == []          # gate blocked: nothing on disk
    assert all(a.status != "applied" for a in actions)


# --------------------------------------------------------------------------- #
# 10. Session activation guard (Phase 8U): Session.run wires the live
#     GatedRepairFixer when self_heal=True (before 8U it used the inert
#     FixerExecutor; 8U flipped Session.run over to the live, gated fixer).
# --------------------------------------------------------------------------- #
SQLITE_REQ = "build a customer CRM desktop app backed by SQLite"

_PLAN = {
    "project_name": "demo", "language": "python", "summary": "s",
    "files": [{"path": "main.py", "purpose": "p"}],
    "build_order": ["main.py"], "test_command": "true",
    "success_criteria": ["ok"],
}


class _FakeReport:
    def __init__(self, accepted):
        self.accepted = accepted
        self.summary = "fake acceptance"
        self.results = [] if accepted else [
            {"criterion": "Dashboard missing export button",
             "check": "", "status": "fail", "detail": ""}
        ]
        self.total = len(self.results)
        self.passed = 0
        self.failed = 0 if accepted else 1
        self.unverified = 0


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from memory import store
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(orch, "WORKSPACE_DIR", str(tmp_path / "ws"))
    return tmp_path


def _wire(session, monkeypatch):
    monkeypatch.setattr(session.planner, "plan", lambda req, lessons="": _PLAN)

    def fake_build(plan, workspace, lessons_text=""):
        os.makedirs(workspace, exist_ok=True)
        with open(os.path.join(workspace, "main.py"), "w") as fh:
            fh.write("x = 1\n")
        return [WriteResult(os.path.join(workspace, "main.py"), True)]

    monkeypatch.setattr(session.builder, "build", fake_build)
    monkeypatch.setattr(session.tester, "test",
                        lambda ws, pl: CommandResult("true", returncode=0))


def test_session_activates_live_fixer_when_self_heal_on(isolated, monkeypatch):
    # Spy the live fixer's execute() to prove Session.run actually drives it.
    import brains.gated_repair_fixer as grf_mod
    calls = []
    monkeypatch.setattr(grf_mod.GatedRepairFixer, "execute",
                        lambda self, work_items: calls.append(1) or [])

    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: _FakeReport(accepted=False))

    session.run(SQLITE_REQ)

    assert calls != []     # Phase 8U: Session.run drives the live GatedRepairFixer when self_heal=True
