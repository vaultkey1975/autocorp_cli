#!/usr/bin/env python3
"""
Agent Team Templates: integration  (AutoCorp CLI - Phase 8E RED)
================================================================

Drives consumption of team profiles through the EXISTING seams - the 8C
ModelRouter and the 8B ReviewerBrain - plus the acceptance-merge helper, without
changing any of those components. Also pins the non-blocking / flag-respecting
guarantees.

Fully offline: ModelRouter/ReviewerBrain are the real classes (LocalEngine is
always available; nothing hits the network). Orchestrator guard tests stub the
pipeline like the 8C/8D integration tests.

RED: select_team_profile / merge_acceptance do not exist yet. The two
flag-respecting guards pass today (the orchestrator ignores profiles entirely)
and must keep passing after GREEN.
"""

import os

import pytest

from core import orchestrator as orch
from core.orchestrator import Session
from safety.gate import AllowAllGate
from safety.executor import WriteResult, CommandResult


def _select(request):
    from brains.templates import select_team_profile
    return select_team_profile(request)


def _merge(plan, profile):
    from brains.templates import merge_acceptance
    return merge_acceptance(plan, profile)


SQLITE_REQ = "build a customer CRM desktop app backed by SQLite"


# --------------------------------------------------------------------------- #
# Consumption through existing seams (no component changes)
# --------------------------------------------------------------------------- #
def test_profile_route_rules_consumed_by_modelrouter():
    from brains.model_router import ModelRouter, RouteDecision, Rule, context_from

    profile = _select(SQLITE_REQ)
    rules = [Rule(**r) for r in profile["route_rules"]]
    router = ModelRouter(rules, default_engine="local")
    decision = router.route(context_from("req", {
        "project_name": "demo", "project_type": "api", "language": "python",
        "files": [{"path": "a.py"}],
    }))
    assert isinstance(decision, RouteDecision)
    assert isinstance(decision.engine, str) and decision.engine


def test_profile_review_profile_consumed_by_reviewerbrain(tmp_path):
    from brains.reviewer import ReviewerBrain, ReviewReport

    profile = _select(SQLITE_REQ)
    lines = profile["review_profile"].get("large_function_lines", 50)
    (tmp_path / "main.py").write_text("import os\n\n\ndef f():\n    return os.getcwd()\n")
    report = ReviewerBrain(max_function_lines=lines).review(
        str(tmp_path), {"project_name": "demo"})
    assert isinstance(report, ReviewReport)


# --------------------------------------------------------------------------- #
# Acceptance merge into success_criteria
# --------------------------------------------------------------------------- #
def test_acceptance_merged_into_success_criteria():
    plan = {"success_criteria": ["pytest reports all tests passing"]}
    merged = _merge(plan, {"acceptance": ["app imports without error"]})
    sc = merged["success_criteria"]
    assert "pytest reports all tests passing" in sc
    assert "app imports without error" in sc


def test_merge_preserves_existing_and_dedupes():
    plan = {"success_criteria": ["A", "B"]}
    merged = _merge(plan, {"acceptance": ["B", "C"]})
    sc = merged["success_criteria"]
    assert sc[:2] == ["A", "B"]   # existing kept, in order
    assert "C" in sc
    assert sc.count("B") == 1     # no duplicate


# --------------------------------------------------------------------------- #
# Malformed / non-blocking
# --------------------------------------------------------------------------- #
def test_malformed_profile_merge_falls_back():
    plan = {"success_criteria": ["A"]}
    merged = _merge(plan, {"unexpected": 1})  # no acceptance key
    assert merged["success_criteria"] == ["A"]  # unchanged, no raise


def test_merge_acceptance_none_profile_is_noop():
    plan = {"success_criteria": ["A"]}
    assert _merge(plan, None)["success_criteria"] == ["A"]


# --------------------------------------------------------------------------- #
# Orchestrator flag guards (pass today; must remain true after GREEN)
# --------------------------------------------------------------------------- #
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


def _wire(session, monkeypatch, captured):
    monkeypatch.setattr(session.planner, "plan", lambda req, lessons="": _PLAN)

    def fake_build(plan, workspace, lessons_text=""):
        captured["engine"] = session.builder.engine.name
        os.makedirs(workspace, exist_ok=True)
        with open(os.path.join(workspace, "main.py"), "w") as fh:
            fh.write("x = 1\n")
        return [WriteResult(os.path.join(workspace, "main.py"), True)]

    monkeypatch.setattr(session.builder, "build", fake_build)
    monkeypatch.setattr(session.tester, "test",
                        lambda ws, pl: CommandResult("true", returncode=0))


def test_route_false_ignores_profile(isolated, monkeypatch):
    session = Session(AllowAllGate(), route=False)
    captured = {}
    _wire(session, monkeypatch, captured)
    session.run(SQLITE_REQ)
    # No routing => builder.engine stays the default; profile never applied.
    assert captured["engine"] == "local"


def test_review_false_ignores_profile(isolated, monkeypatch):
    from config import REVIEW_LARGE_FUNCTION_LINES

    session = Session(AllowAllGate(), review=False)
    captured = {}
    _wire(session, monkeypatch, captured)
    session.run(SQLITE_REQ)
    # Review disabled => reviewer threshold is the global default, not a profile.
    assert session.reviewer.max_function_lines == REVIEW_LARGE_FUNCTION_LINES
