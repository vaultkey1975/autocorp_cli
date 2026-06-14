#!/usr/bin/env python3
"""
Session activation of the live repair path  (AutoCorp CLI - Phase 8U RED)
=========================================================================

Drives Phase 8U: when `self_heal` is ON, `Session.run` must wire the live
`GatedRepairFixer(self.executor)` into `run_cycle` (instead of the inert
`FixerExecutor()`), so an enabled run performs REAL, gated, write-only repairs.
When OFF (the default), nothing changes and the live fixer is never constructed.

Pinned design (RED until GREEN implements it):
  * `core.orchestrator` imports `GatedRepairFixer` at module level.
  * In run(), the self_heal branch passes `fixer=GatedRepairFixer(self.executor)`
    (the SAME gated Executor the Session writes through) to `run_cycle`, keeping
    `max_attempts=MAX_FIX_ATTEMPTS` (RetryController authoritative).
  * Repairs flow ONLY through `Executor.write_file` -> gate -> filesystem; no
    run_command, no subprocess. `self_heal=False` stays the default.

RED mechanisms (both missing-implementation / desired-behavior):
  * construction/guard tests monkeypatch `orchestrator.GatedRepairFixer`, which
    does not exist yet -> AttributeError;
  * wiring/behavior tests assert the live fixer is received / a real repair file
    is written, which is false today (Session still wires the inert
    FixerExecutor) -> AssertionError.

Fully offline: planner/builder/tester stubbed; `acceptance_gate.evaluate`
monkeypatched to a controlled report; real gated writes redirected under tmp_path
via chdir; DB + workspace redirected to tmp_path. No model, no network, no shell.
"""

import os
import subprocess

import pytest

from config import MAX_FIX_ATTEMPTS
from core import orchestrator as orch
from core.orchestrator import Session
from brains.gated_repair_fixer import GatedRepairFixer
from safety.executor import Executor, WriteResult, CommandResult
from safety.gate import AllowAllGate


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


def _force_report(session, monkeypatch, accepted):
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: _FakeReport(accepted))


def _make_construct_spy():
    """A drop-in GatedRepairFixer replacement that records each construction."""
    records = []

    class _Spy:
        def __init__(self, executor, generator=None):
            records.append(executor)
            self.executor = executor
            self.generator = generator

        def execute(self, work_items):
            return []          # no writes; we only care that it was constructed

    return _Spy, records


def _repair_files(tmp_path):
    # DS10: repairs now target the REAL planned file resolved from the plan
    # (build_order ["main.py"]) instead of the repairs/repair_N.txt placeholder.
    # The gated write is relative, so it lands at <cwd>/main.py (cwd is chdir'd
    # to tmp_path in these tests). A truthy list means a real repair was written.
    return [tmp_path / "main.py"] if (tmp_path / "main.py").exists() else []


# --------------------------------------------------------------------------- #
# 1. self_heal=False -> GatedRepairFixer NOT constructed; behavior unchanged
# --------------------------------------------------------------------------- #
def test_off_does_not_construct_live_fixer(isolated, monkeypatch):
    Spy, records = _make_construct_spy()
    monkeypatch.setattr(orch, "GatedRepairFixer", Spy)   # RED: attr missing today
    session = Session(AllowAllGate(), accept=True, self_heal=False)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)

    result = session.run(SQLITE_REQ)

    assert records == []                                 # never constructed
    assert result["status"] == "passed"                  # advisory, unchanged


# --------------------------------------------------------------------------- #
# 2. Default self_heal OFF preserved
# --------------------------------------------------------------------------- #
def test_default_self_heal_off_preserved(isolated, monkeypatch):
    Spy, records = _make_construct_spy()
    monkeypatch.setattr(orch, "GatedRepairFixer", Spy)   # RED: attr missing today
    session = Session(AllowAllGate(), accept=True)        # default self_heal
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)

    result = session.run(SQLITE_REQ)

    assert records == []
    assert result["status"] == "passed"


# --------------------------------------------------------------------------- #
# 3. self_heal=True -> GatedRepairFixer constructed with the Session's executor
# --------------------------------------------------------------------------- #
def test_on_constructs_live_fixer_with_session_executor(isolated, monkeypatch):
    Spy, records = _make_construct_spy()
    monkeypatch.setattr(orch, "GatedRepairFixer", Spy)   # RED: attr missing today
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)

    session.run(SQLITE_REQ)

    assert len(records) >= 1                              # constructed
    assert records[0] is session.executor                # with the gated executor


# --------------------------------------------------------------------------- #
# 4. self_heal=True -> run_cycle receives the live fixer
# --------------------------------------------------------------------------- #
def test_on_run_cycle_receives_live_fixer(isolated, monkeypatch):
    captured = {}

    def fake_run_cycle(work_items, fixer, verify, max_attempts):
        captured["fixer"] = fixer
        return None

    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)
    monkeypatch.setattr(session.self_healer, "run_cycle", fake_run_cycle)

    session.run(SQLITE_REQ)

    assert isinstance(captured.get("fixer"), GatedRepairFixer)   # RED: it's FixerExecutor today


# --------------------------------------------------------------------------- #
# 5. Real repair writes through Executor.write_file() -> gate -> filesystem
# --------------------------------------------------------------------------- #
def test_on_real_repair_writes_to_filesystem(isolated, monkeypatch):
    monkeypatch.chdir(isolated)                           # relative repair writes land here
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)

    session.run(SQLITE_REQ)

    assert _repair_files(isolated)                        # RED: inert fixer writes nothing today


# --------------------------------------------------------------------------- #
# 6. No run_command path during a live repair
# --------------------------------------------------------------------------- #
def test_on_live_repair_uses_no_run_command(isolated, monkeypatch):
    monkeypatch.chdir(isolated)
    runs = []
    monkeypatch.setattr(Executor, "run_command",
                        lambda self, command, cwd: runs.append(command))
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)

    session.run(SQLITE_REQ)

    assert _repair_files(isolated)                        # RED trigger: a real repair happened
    assert runs == []                                     # and never via run_command


# --------------------------------------------------------------------------- #
# 7. No subprocess path during a live repair
# --------------------------------------------------------------------------- #
def test_on_live_repair_uses_no_subprocess(isolated, monkeypatch):
    monkeypatch.chdir(isolated)
    runs = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: runs.append(a))
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)

    session.run(SQLITE_REQ)

    assert _repair_files(isolated)                        # RED trigger: a real repair happened
    assert runs == []                                     # and never via subprocess


# --------------------------------------------------------------------------- #
# 8. RetryController remains authoritative with the live fixer
# --------------------------------------------------------------------------- #
def test_retry_controller_authoritative_with_live_fixer(isolated, monkeypatch):
    captured = {}

    def fake_run_cycle(work_items, fixer, verify, max_attempts):
        captured["fixer"] = fixer
        captured["max_attempts"] = max_attempts
        return None

    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)
    monkeypatch.setattr(session.self_healer, "run_cycle", fake_run_cycle)

    session.run(SQLITE_REQ)

    assert isinstance(captured.get("fixer"), GatedRepairFixer)   # RED: FixerExecutor today
    assert captured.get("max_attempts") == MAX_FIX_ATTEMPTS      # budget unchanged
