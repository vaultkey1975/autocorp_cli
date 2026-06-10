#!/usr/bin/env python3
"""
Acceptance Gate: orchestrator integration  (AutoCorp CLI - Phase 8F RED)
=======================================================================

Drives the opt-in, non-blocking wiring of the Acceptance Gate into the
orchestrator. RED: `Session` does not yet accept `accept`/`accept_strict`, has no
`acceptance_gate`, and `run()` does not gate.

Guarantees pinned here:
  * accept=False -> gate never runs; status unchanged,
  * accept=True (advisory) -> gate runs but never changes status,
  * accept_strict=True -> an unaccepted build is downgraded to "accept_failed",
    while an accepted build stays "passed",
  * a request with no team profile is a no-op,
  * a gate exception is swallowed (non-blocking).

Fully offline: planner/builder/tester stubbed; gate.evaluate monkeypatched to a
controlled report; DB + workspace redirected to tmp_path. No model, no network,
no acceptance storage.
"""

import os

import pytest

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
UNKNOWN_REQ = "build a library that parses ISO date strings"

_PLAN = {
    "project_name": "demo", "language": "python", "summary": "s",
    "files": [{"path": "main.py", "purpose": "p"}],
    "build_order": ["main.py"], "test_command": "true",
    "success_criteria": ["ok"],
}


class _FakeAccept:
    def __init__(self, accepted):
        self.accepted = accepted
        self.summary = "fake acceptance"
        self.results = []
        self.total = self.passed = self.failed = self.unverified = 0

    def to_dict(self):
        return {"accepted": self.accepted, "summary": self.summary,
                "results": [], "total": 0, "passed": 0, "failed": 0,
                "unverified": 0}


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


# --------------------------------------------------------------------------- #
# accept=False guard
# --------------------------------------------------------------------------- #
def test_accept_false_does_not_run_gate(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=False)
    _wire(session, monkeypatch)
    calls = []
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: calls.append(1) or _FakeAccept(True))

    result = session.run(SQLITE_REQ)

    assert calls == []                      # gate never invoked
    assert result["status"] == "passed"


# --------------------------------------------------------------------------- #
# accept=True advisory
# --------------------------------------------------------------------------- #
def test_accept_true_advisory_runs_gate_without_changing_status(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True)
    _wire(session, monkeypatch)
    calls = []
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: calls.append(1) or _FakeAccept(False))

    result = session.run(SQLITE_REQ)

    assert calls == [1]                     # gate ran
    assert result["status"] == "passed"     # advisory: not downgraded


def test_status_downgrade_only_in_strict(isolated, monkeypatch):
    # Advisory mode with an unaccepted build must NOT downgrade.
    session = Session(AllowAllGate(), accept=True, accept_strict=False)
    _wire(session, monkeypatch)
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: _FakeAccept(False))
    assert session.run(SQLITE_REQ)["status"] == "passed"


# --------------------------------------------------------------------------- #
# accept_strict enforcement
# --------------------------------------------------------------------------- #
def test_accept_strict_downgrades_on_failure(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, accept_strict=True)
    _wire(session, monkeypatch)
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: _FakeAccept(False))
    assert session.run(SQLITE_REQ)["status"] == "accept_failed"


def test_accept_strict_passes_when_accepted(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, accept_strict=True)
    _wire(session, monkeypatch)
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: _FakeAccept(True))
    assert session.run(SQLITE_REQ)["status"] == "passed"


# --------------------------------------------------------------------------- #
# no-profile no-op
# --------------------------------------------------------------------------- #
def test_no_profile_is_noop(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, accept_strict=True)
    _wire(session, monkeypatch)
    calls = []
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: calls.append(1) or _FakeAccept(False))

    result = session.run(UNKNOWN_REQ)       # matches no team profile

    assert calls == []                      # no criteria -> gate not run
    assert result["status"] == "passed"


# --------------------------------------------------------------------------- #
# non-blocking: gate exception swallowed
# --------------------------------------------------------------------------- #
def test_gate_exception_is_swallowed(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, accept_strict=True)
    _wire(session, monkeypatch)

    def boom(criteria, ctx):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(session.acceptance_gate, "evaluate", boom)

    result = session.run(SQLITE_REQ)        # must not raise
    assert result["status"] == "passed"     # failure is non-blocking
