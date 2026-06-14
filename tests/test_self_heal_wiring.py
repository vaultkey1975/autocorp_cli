#!/usr/bin/env python3
"""
Self-healing pipeline: orchestrator wiring  (AutoCorp CLI - Phase 8Q RED)
========================================================================

Drives the opt-in, flag-guarded wiring of the self-healing pipeline into
`Session.run()`. This is the first phase that lets autonomous repair run in the
REAL build path, so it is gated behind a new `self_heal` flag (default OFF) and
operates on the acceptance report (so it is used together with `accept=True`).

Pinned design (RED until GREEN implements it):
  * Session.__init__ gains `self_heal: bool = False` and constructs
    `self.repair_adapter = AcceptanceRepairAdapter()` and
    `self.self_healer = SelfHealingOrchestrator()`.
  * In run(), after the acceptance gate produces a report, when
    `self.self_heal` is enabled AND the report is NOT accepted:
        work_items = self.repair_adapter.to_work_items(report)
        self.self_healer.run_cycle(work_items, fixer=..., verify=...,
                                   max_attempts=MAX_FIX_ATTEMPTS)
    An ACCEPTED report bypasses repair entirely.
  * RetryController stays authoritative: run_cycle is handed
    `max_attempts == MAX_FIX_ATTEMPTS`; the orchestrator does not re-implement the
    budget.
  * Safety is unchanged: every write/command still flows through
    `session.executor`, whose gate (CommandGate / WatchdogGate) is the one passed
    to the Session. The self-heal feature must not detach the executor from its
    gate.
  * Backward compatible: legacy `Session(gate)` calls keep working and default to
    `self_heal=False`, preserving today's behavior exactly.

RED: these tests fail for MISSING IMPLEMENTATION ONLY. Today `Session` does not
accept `self_heal` (TypeError on construction) and has no `self_heal`/
`repair_adapter`/`self_healer` attributes (AttributeError). No production code is
changed in this phase.

Fully offline: planner/builder/tester stubbed; `acceptance_gate.evaluate`
monkeypatched to a controlled report; the adapter and orchestrator are spied with
monkeypatch; DB + workspace redirected to tmp_path. No model, no network, no
subprocess, no real file writes outside tmp_path.
"""

import os

import pytest

from config import MAX_FIX_ATTEMPTS
from core import orchestrator as orch
from core.orchestrator import Session
from safety.gate import AllowAllGate
from safety.executor import WriteResult, CommandResult


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from memory import store
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(orch, "WORKSPACE_DIR", str(tmp_path / "ws"))
    return tmp_path


SQLITE_REQ = "build a customer CRM desktop app backed by SQLite"

_PLAN = {
    "project_name": "demo", "language": "python", "summary": "s",
    "files": [{"path": "main.py", "purpose": "p"}],
    "build_order": ["main.py"], "test_command": "true",
    "success_criteria": ["ok"],
}


class _FakeReport:
    """Stand-in for an AcceptanceReport with a controllable `accepted` flag."""

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

    def to_dict(self):
        return {"accepted": self.accepted, "summary": self.summary,
                "results": list(self.results), "total": self.total,
                "passed": self.passed, "failed": self.failed,
                "unverified": self.unverified}


class _FakeCycle:
    """Stand-in for a RepairCycle returned by run_cycle."""

    def __init__(self, healed=False):
        self.healed = healed
        self.execution_results = []
        self.retry_state = None


def _wire(session, monkeypatch):
    """Stub planner/builder/tester so run() is fully offline and tests pass."""
    monkeypatch.setattr(session.planner, "plan", lambda req, lessons="": _PLAN)

    def fake_build(plan, workspace, lessons_text=""):
        os.makedirs(workspace, exist_ok=True)
        with open(os.path.join(workspace, "main.py"), "w") as fh:
            fh.write("x = 1\n")
        return [WriteResult(os.path.join(workspace, "main.py"), True)]

    monkeypatch.setattr(session.builder, "build", fake_build)
    monkeypatch.setattr(session.tester, "test",
                        lambda ws, pl: CommandResult("true", returncode=0))


def _spy_pipeline(session, monkeypatch, report):
    """Force the acceptance gate to `report` and spy the self-heal collaborators.

    Returns (adapter_calls, run_cycle_calls) - each a list the spies append to."""
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: report)
    adapter_calls = []
    run_cycle_calls = []

    def spy_to_work_items(rep, plan=None):
        adapter_calls.append(rep)
        return []  # empty handoff is fine; run_cycle is spied below

    def spy_run_cycle(*args, **kwargs):
        run_cycle_calls.append({"args": args, "kwargs": kwargs})
        return _FakeCycle(healed=False)

    monkeypatch.setattr(session.repair_adapter, "to_work_items", spy_to_work_items)
    monkeypatch.setattr(session.self_healer, "run_cycle", spy_run_cycle)
    return adapter_calls, run_cycle_calls


# --------------------------------------------------------------------------- #
# 1. Disabled flag preserves existing behavior
# --------------------------------------------------------------------------- #
def test_disabled_flag_preserves_existing_behavior(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=False)
    _wire(session, monkeypatch)
    adapter_calls, run_cycle_calls = _spy_pipeline(
        session, monkeypatch, _FakeReport(accepted=False))

    result = session.run(SQLITE_REQ)

    assert adapter_calls == []               # no repair pipeline
    assert run_cycle_calls == []
    assert result["status"] == "passed"      # advisory acceptance unchanged


# --------------------------------------------------------------------------- #
# 2. Enabled flag invokes AcceptanceRepairAdapter
# --------------------------------------------------------------------------- #
def test_enabled_flag_invokes_adapter(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    report = _FakeReport(accepted=False)
    adapter_calls, _ = _spy_pipeline(session, monkeypatch, report)

    session.run(SQLITE_REQ)

    assert len(adapter_calls) == 1
    assert adapter_calls[0] is report        # the report is handed to the adapter


# --------------------------------------------------------------------------- #
# 3. Enabled flag invokes SelfHealingOrchestrator.run_cycle()
# --------------------------------------------------------------------------- #
def test_enabled_flag_invokes_run_cycle(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _, run_cycle_calls = _spy_pipeline(
        session, monkeypatch, _FakeReport(accepted=False))

    session.run(SQLITE_REQ)

    assert len(run_cycle_calls) == 1


# --------------------------------------------------------------------------- #
# 4. Accepted reports bypass repair execution
# --------------------------------------------------------------------------- #
def test_accepted_report_bypasses_repair(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    adapter_calls, run_cycle_calls = _spy_pipeline(
        session, monkeypatch, _FakeReport(accepted=True))

    result = session.run(SQLITE_REQ)

    assert adapter_calls == []               # nothing to repair
    assert run_cycle_calls == []
    assert result["status"] == "passed"


# --------------------------------------------------------------------------- #
# 5. Failed reports enter repair execution
# --------------------------------------------------------------------------- #
def test_failed_report_enters_repair(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    adapter_calls, run_cycle_calls = _spy_pipeline(
        session, monkeypatch, _FakeReport(accepted=False))

    session.run(SQLITE_REQ)

    assert len(adapter_calls) == 1           # adapter consulted
    assert len(run_cycle_calls) == 1         # repair loop driven


# --------------------------------------------------------------------------- #
# 6. RetryController limits remain enforced (budget passed through)
# --------------------------------------------------------------------------- #
def test_retry_controller_limit_enforced(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _, run_cycle_calls = _spy_pipeline(
        session, monkeypatch, _FakeReport(accepted=False))

    session.run(SQLITE_REQ)

    assert len(run_cycle_calls) == 1
    call = run_cycle_calls[0]
    passed_max = call["kwargs"].get("max_attempts")
    if passed_max is None and call["args"]:
        passed_max = call["args"][-1]        # positional fallback
    assert passed_max == MAX_FIX_ATTEMPTS    # RetryController stays authoritative


# --------------------------------------------------------------------------- #
# 7. Safety systems remain in the execution path
# --------------------------------------------------------------------------- #
def test_safety_gate_remains_in_path(isolated, monkeypatch):
    gate = AllowAllGate()
    session = Session(gate, accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _spy_pipeline(session, monkeypatch, _FakeReport(accepted=False))

    session.run(SQLITE_REQ)

    # Enabling self-heal must NOT detach the executor from its gate: every write
    # and command still flows through the same CommandGate/WatchdogGate.
    assert session.executor.gate is gate


# --------------------------------------------------------------------------- #
# 8. Integration remains backward compatible (legacy ctor, default OFF)
# --------------------------------------------------------------------------- #
def test_backward_compatible_default_off(isolated):
    session = Session(AllowAllGate())        # legacy signature, no new kwargs
    assert session.self_heal is False        # default OFF
    assert hasattr(session, "repair_adapter")
    assert hasattr(session, "self_healer")
