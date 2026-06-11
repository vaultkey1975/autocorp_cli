#!/usr/bin/env python3
"""
Acceptance Brain tests  (AutoCorp CLI - Phase 8H RED)
=====================================================

Drives the design of the Acceptance -> Fix Feedback seam (Phase 8H): after the
Tester and Reviewer run, an AcceptanceBrain should be able to (1) turn acceptance
failures into fix requests, (2) return nothing when acceptance passed, (3) be
present in the orchestration flow, and (4) attach failures to project state.

These tests are RED on purpose:
  * fix_requests / record_failures are STUBS that raise NotImplementedError, so
    tests 1, 2, 4 fail until GREEN implements the behaviour.
  * the Session does not construct an AcceptanceBrain yet, so test 3 fails
    (AttributeError) - the orchestrator wiring is intentionally NOT added here.

RED scope only: no production behaviour, no retry loop, no autonomous repair.
Fully offline - test 3 redirects the store DB + workspace to tmp (the `isolated`
fixture), so constructing a Session touches no real state, model, or network.
"""

from types import SimpleNamespace

import pytest

from core import orchestrator as orch
from core.orchestrator import Session
from safety.gate import AllowAllGate
from brains.acceptance_brain import AcceptanceResult, AcceptanceBrain


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect the memory store + workspace to tmp so Session() is side-effect
    free (mirrors the Phase 8C integration fixture)."""
    from memory import store
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(orch, "WORKSPACE_DIR", str(tmp_path / "ws"))
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. Acceptance failure generates fix requests
# --------------------------------------------------------------------------- #
def test_acceptance_failure_generates_fix_requests():
    result = AcceptanceResult(
        passed=False, failures=["Dashboard missing export button"]
    )
    assert AcceptanceBrain().fix_requests(result) == [
        "Dashboard missing export button"
    ]


# --------------------------------------------------------------------------- #
# 2. Acceptance success generates no fix requests
# --------------------------------------------------------------------------- #
def test_acceptance_success_generates_no_fix_requests():
    result = AcceptanceResult(passed=True, failures=[])
    assert AcceptanceBrain().fix_requests(result) == []


# --------------------------------------------------------------------------- #
# 3. Orchestrator invokes AcceptanceBrain (wiring seam)
# --------------------------------------------------------------------------- #
def test_orchestrator_constructs_acceptance_brain(isolated):
    # The Session must expose an AcceptanceBrain so a FUTURE phase can run it
    # AFTER the Tester + Reviewer. RED: Session has no `acceptance_brain` yet.
    session = Session(AllowAllGate())
    assert isinstance(session.acceptance_brain, AcceptanceBrain)


# --------------------------------------------------------------------------- #
# 4. Acceptance failures are attached to project state
# --------------------------------------------------------------------------- #
def test_acceptance_failures_attached_to_project_state():
    project = SimpleNamespace()
    result = AcceptanceResult(
        passed=False, failures=["Dashboard missing export button"]
    )
    AcceptanceBrain().record_failures(project, result)
    assert project.acceptance_failures == ["Dashboard missing export button"]
