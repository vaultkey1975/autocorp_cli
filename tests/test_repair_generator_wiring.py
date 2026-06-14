#!/usr/bin/env python3
"""
Repair generator wiring  (AutoCorp CLI - Phase DS6 RED)
=======================================================

Drives Phase DS6: when `self_heal` is ON, `Session.run` must connect the
ALREADY-EXISTING repair-content components into the live repair path. Today the
orchestrator constructs `GatedRepairFixer(self.executor)` with `generator=None`,
so the gated fixer falls back to writing the failure *description* and never
produces real fix content.

This phase does NOT activate DeepSeek, does NOT inject engines, and does NOT
change routing. It only wires components that already exist:

Pinned design (RED until GREEN implements it):
  * In run()'s self_heal branch, the orchestrator builds the tester-backed
    provider via the factory:
        provider = RepairContentProviderFactory.create(
            "tester", tester=self.tester, workspace=workspace, plan=plan)
    wraps it:
        generator = RepairContentGenerator(provider)
    and hands it to the fixer:
        fixer = GatedRepairFixer(self.executor, generator=generator)
    which is the fixer passed to `self.self_healer.run_cycle(...)`.
  * `self_heal=False` (the default) is unchanged: the repair branch never runs,
    so no factory, no generator, no fixer construction.
  * No engine is injected: `self.tester` is still engine-less. The provider
    produces content through TesterBrain's existing path. Engine-backed routing
    is the NEXT phase (DS7), not this one.

RED mechanism (desired-behavior / wiring-missing):
  * the wiring tests capture the fixer handed to run_cycle and inspect
    `fixer.generator`. Today that is `None`, so the chain assertions fail with
    AssertionError. No production code is changed in this phase.

Fully offline: planner/builder/tester stubbed; `acceptance_gate.evaluate`
monkeypatched to a controlled report; `self_healer.run_cycle` spied so nothing
is actually executed; DB + workspace redirected to tmp_path. No model, no
network, no subprocess, no real repair writes.
"""

import pytest

from core import orchestrator as orch
from core.orchestrator import Session
from brains.gated_repair_fixer import GatedRepairFixer
from brains.repair_content_generator import (
    RepairContentGenerator,
    TesterBackedRepairContentProvider,
)
from safety.executor import WriteResult, CommandResult
from safety.gate import AllowAllGate

import os


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
    """Stub planner/builder/tester so run() is fully offline."""
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


def _capture_run_cycle(session, monkeypatch):
    """Spy run_cycle so nothing executes; capture the fixer it receives."""
    captured = {}

    def fake_run_cycle(work_items, fixer=None, verify=None, max_attempts=None):
        captured["called"] = True
        captured["fixer"] = fixer
        return None

    monkeypatch.setattr(session.self_healer, "run_cycle", fake_run_cycle)
    return captured


def _capture_workspace(session, monkeypatch):
    """Capture the real workspace path run() builds for the project."""
    holder = {}
    orig = session._make_workspace

    def spy(project_name):
        ws = orig(project_name)
        holder["ws"] = ws
        return ws

    monkeypatch.setattr(session, "_make_workspace", spy)
    return holder


# --------------------------------------------------------------------------- #
# A/D. The fixer handed to run_cycle carries a generator (not None)
# --------------------------------------------------------------------------- #
def test_enabled_passes_generator_into_fixer(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)
    captured = _capture_run_cycle(session, monkeypatch)

    session.run(SQLITE_REQ)

    assert captured.get("called") is True
    fixer = captured.get("fixer")
    assert isinstance(fixer, GatedRepairFixer)
    assert fixer.generator is not None        # RED: generator is None today


# --------------------------------------------------------------------------- #
# D. The generator is a RepairContentGenerator
# --------------------------------------------------------------------------- #
def test_generator_is_repair_content_generator(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)
    captured = _capture_run_cycle(session, monkeypatch)

    session.run(SQLITE_REQ)

    fixer = captured.get("fixer")
    assert isinstance(fixer, GatedRepairFixer)
    assert isinstance(fixer.generator, RepairContentGenerator)   # RED today


# --------------------------------------------------------------------------- #
# C. The selected provider is the tester-backed one
# --------------------------------------------------------------------------- #
def test_provider_is_tester_backed(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)
    captured = _capture_run_cycle(session, monkeypatch)

    session.run(SQLITE_REQ)

    fixer = captured.get("fixer")
    assert isinstance(fixer.generator, RepairContentGenerator)   # RED today
    assert isinstance(
        fixer.generator.provider, TesterBackedRepairContentProvider)


# --------------------------------------------------------------------------- #
# C. The provider uses the Session's tester (no new tester, no engine swap)
# --------------------------------------------------------------------------- #
def test_provider_uses_session_tester(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)
    captured = _capture_run_cycle(session, monkeypatch)

    session.run(SQLITE_REQ)

    provider = captured.get("fixer").generator.provider   # RED today (None.generator)
    assert provider.tester is session.tester


# --------------------------------------------------------------------------- #
# D. The provider is built with this run's workspace and plan
# --------------------------------------------------------------------------- #
def test_provider_uses_run_workspace_and_plan(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)
    ws_holder = _capture_workspace(session, monkeypatch)
    captured = _capture_run_cycle(session, monkeypatch)

    session.run(SQLITE_REQ)

    provider = captured.get("fixer").generator.provider   # RED today
    assert provider.workspace == ws_holder["ws"]
    assert provider.plan == _PLAN


# --------------------------------------------------------------------------- #
# B. Flag OFF: repair branch never runs; no generator, no fixer constructed
#    (guard test - must stay GREEN to prove preserved behavior)
# --------------------------------------------------------------------------- #
def test_disabled_flag_no_repair_wiring(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=False)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)
    captured = _capture_run_cycle(session, monkeypatch)

    result = session.run(SQLITE_REQ)

    assert "called" not in captured            # run_cycle never invoked
    assert result["status"] == "passed"        # advisory acceptance unchanged
