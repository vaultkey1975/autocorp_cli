#!/usr/bin/env python3
"""
Reviewer -> Fix Loop feedback: orchestrator tests  (AutoCorp CLI - Phase 8D RED)
===============================================================================

Drives the orchestrator wiring that carries the Review report's findings into
the Fix Loop, so every repair attempt sees them.

Guarantees pinned here:
  * when review is ON, the fixer receives the review findings on EVERY attempt,
  * when review is OFF, the fixer receives no findings (behaviour unchanged),
  * a Reviewer persistence failure (record_review raising) does NOT stop fixing
    and does NOT lose the findings,
  * a passing first test still skips the fix loop entirely (unchanged).

Fully offline: planner/builder/reviewer/tester are stubbed; DB and workspace are
redirected to tmp_path. No model, no network.
"""

import os

import pytest

from core import orchestrator as orch
from core.orchestrator import Session
from safety.gate import AllowAllGate
from safety.executor import WriteResult, CommandResult
from brains.reviewer import Finding, ReviewReport


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from memory import store
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(orch, "WORKSPACE_DIR", str(tmp_path / "ws"))
    return tmp_path


_PLAN = {
    "project_name": "demo", "language": "python", "summary": "s",
    "files": [{"path": "main.py", "purpose": "p"}],
    "build_order": ["main.py"], "test_command": "true",
    "success_criteria": ["ok"],
}

_FINDINGS = [
    Finding(file="main.py", line=1, severity="error", category="missing_import",
            symbol="sqlite3", message="Name 'sqlite3' used but never imported."),
    Finding(file="main.py", line=5, severity="warning", category="large_function",
            symbol="big", message="Function 'big' is 60 lines."),
]


def _report():
    return ReviewReport(project_name="demo", workspace="ws", ts="", files_reviewed=1,
                        score=78, findings=list(_FINDINGS), summary="2 findings")


def _messages(items):
    """Extract message text from findings whether they are Finding objects or
    dicts (so GREEN may pass either)."""
    out = []
    for it in items or []:
        if hasattr(it, "message"):
            out.append(it.message)
        elif isinstance(it, dict):
            out.append(it.get("message", ""))
    return out


def _wire(session, monkeypatch, captured, n_fail, with_review):
    """Stub the pipeline; tester fails `n_fail` times then passes. Each
    suggest_fix call appends its `findings` argument to `captured`."""
    monkeypatch.setattr(session.planner, "plan", lambda req, lessons="": _PLAN)

    def fake_build(plan, workspace, lessons_text=""):
        os.makedirs(workspace, exist_ok=True)
        with open(os.path.join(workspace, "main.py"), "w") as fh:
            fh.write("x = 1\n")
        return [WriteResult(os.path.join(workspace, "main.py"), True)]

    monkeypatch.setattr(session.builder, "build", fake_build)
    if with_review:
        monkeypatch.setattr(session.reviewer, "review", lambda ws, pl: _report())

    results = [CommandResult("true", returncode=1,
                             stdout="main.py:1: AssertionError")] * n_fail
    results.append(CommandResult("true", returncode=0))
    monkeypatch.setattr(session.tester, "test", lambda ws, pl: results.pop(0))
    monkeypatch.setattr(session.tester, "pick_file_to_fix", lambda plan, out: "main.py")

    def spy_suggest(workspace, filename, error_output, plan=None, findings=None):
        captured.append(findings)
        return {"explanation": "e", "filename": "main.py", "new_content": "x = 2\n"}

    monkeypatch.setattr(session.tester, "suggest_fix", spy_suggest)


# --------------------------------------------------------------------------- #
# A. findings reach the fixer (every attempt)
# --------------------------------------------------------------------------- #
def test_findings_passed_to_fixer_when_review_enabled(isolated, monkeypatch):
    session = Session(AllowAllGate(), review=True)
    captured = []
    _wire(session, monkeypatch, captured, n_fail=1, with_review=True)

    session.run("build a demo")

    assert len(captured) == 1
    assert _messages(captured[0]) == [f.message for f in _FINDINGS]


def test_findings_available_on_every_attempt(isolated, monkeypatch):
    session = Session(AllowAllGate(), review=True)
    captured = []
    _wire(session, monkeypatch, captured, n_fail=2, with_review=True)

    session.run("build a demo")

    assert len(captured) == 2
    for passed in captured:
        assert _messages(passed) == [f.message for f in _FINDINGS]


# --------------------------------------------------------------------------- #
# B. backward compatibility (review off)
# --------------------------------------------------------------------------- #
def test_review_disabled_passes_no_findings(isolated, monkeypatch):
    session = Session(AllowAllGate(), review=False)
    captured = []
    _wire(session, monkeypatch, captured, n_fail=1, with_review=False)

    session.run("build a demo")

    assert len(captured) == 1
    assert captured[0] in (None, [])


def test_passing_test_skips_fix_loop(isolated, monkeypatch):
    session = Session(AllowAllGate(), review=False)
    captured = []
    _wire(session, monkeypatch, captured, n_fail=0, with_review=False)

    result = session.run("build a demo")

    assert captured == []  # fixer never called
    assert result["status"] == "passed"


# --------------------------------------------------------------------------- #
# C. non-blocking: persistence failure must not affect fixing
# --------------------------------------------------------------------------- #
def test_persistence_failure_does_not_block_fixing(isolated, monkeypatch):
    from memory import store

    session = Session(AllowAllGate(), review=True)
    captured = []
    _wire(session, monkeypatch, captured, n_fail=1, with_review=True)

    def boom(report):
        raise RuntimeError("review persistence failed")

    monkeypatch.setattr(store, "record_review", boom)

    session.run("build a demo")  # must not raise

    assert len(captured) == 1
    assert _messages(captured[0]) == [f.message for f in _FINDINGS]
