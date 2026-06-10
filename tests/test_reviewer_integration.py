#!/usr/bin/env python3
"""
Reviewer integration tests  (AutoCorp CLI - Phase 8B RED)
=========================================================

Drives the opt-in, non-blocking wiring of the Reviewer Brain into the
orchestrator. RED: `Session` does not yet accept a `review` flag, has no
`reviewer` attribute, and `run()` does not invoke a reviewer.

Design guarantees these tests pin:
  * review is OFF by default (existing pipeline behaviour preserved),
  * when ON, the reviewer runs BEFORE the tester and the pipeline still proceeds
    to test (non-blocking).

Fully offline: the LLM planner/builder/tester are stubbed, the DB and workspace
are redirected to tmp_path. No model, no network.
"""

import os

import pytest

from core import orchestrator as orch
from core.orchestrator import Session
from safety.gate import AllowAllGate
from safety.executor import WriteResult, CommandResult


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect the memory DB and the build workspace into tmp_path."""
    from memory import store
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(orch, "WORKSPACE_DIR", str(tmp_path / "ws"))
    return tmp_path


class _FakeReport:
    """Minimal stand-in for a ReviewReport returned by a stubbed reviewer."""

    def __init__(self):
        self.project_name = "demo"
        self.workspace = ""
        self.score = 100
        self.files_reviewed = 1
        self.findings = []
        self.summary = "0 findings · score 100/100"

    def to_dict(self):
        return {
            "project_name": self.project_name, "workspace": self.workspace,
            "score": self.score, "files_reviewed": self.files_reviewed,
            "findings": [], "summary": self.summary, "ts": "",
        }


def test_session_review_off_by_default(isolated):
    session = Session(AllowAllGate())
    assert session.review is False


def test_session_accepts_review_flag(isolated):
    session = Session(AllowAllGate(), review=True)
    assert session.review is True


def test_session_constructs_reviewer(isolated):
    from brains.reviewer import ReviewerBrain
    session = Session(AllowAllGate())
    assert isinstance(session.reviewer, ReviewerBrain)


def test_reviewer_runs_before_tester_and_pipeline_continues(isolated, monkeypatch):
    from memory import store

    session = Session(AllowAllGate(), review=True)
    calls = []

    plan = {
        "project_name": "demo", "language": "python", "summary": "s",
        "files": [{"path": "main.py", "purpose": "p"}],
        "build_order": ["main.py"], "test_command": "true",
        "success_criteria": ["ok"],
    }
    monkeypatch.setattr(session.planner, "plan", lambda req, lessons="": plan)

    def fake_build(plan, workspace, lessons_text=""):
        os.makedirs(workspace, exist_ok=True)
        with open(os.path.join(workspace, "main.py"), "w") as fh:
            fh.write("x = 1\n")
        return [WriteResult(os.path.join(workspace, "main.py"), True)]

    monkeypatch.setattr(session.builder, "build", fake_build)

    def fake_review(workspace, plan):
        calls.append("review")
        return _FakeReport()

    monkeypatch.setattr(session.reviewer, "review", fake_review)

    def fake_test(workspace, plan):
        calls.append("test")
        return CommandResult("true", returncode=0)

    monkeypatch.setattr(session.tester, "test", fake_test)
    # Persistence is best-effort; capture it without requiring the real impl.
    monkeypatch.setattr(store, "record_review", lambda report: 1, raising=False)

    session.run("build a demo")

    assert "review" in calls and "test" in calls
    assert calls.index("review") < calls.index("test")
