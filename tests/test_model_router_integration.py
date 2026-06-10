#!/usr/bin/env python3
"""
Model Router integration tests  (AutoCorp CLI - Phase 8C RED)
=============================================================

Drives the opt-in, non-blocking wiring of the Model Router into the orchestrator
and CLI. RED: `Session` does not yet accept a `route` flag, has no `router`, and
`run()` does not route; the parser does not yet accept `--engine auto`.

Design guarantees these tests pin:
  * routing is OFF by default (existing pipeline behaviour preserved),
  * when ON, the router runs AFTER planning and BEFORE build, and sets
    `builder.engine` from the RouteDecision,
  * a routing failure is non-blocking - the build continues on the fallback
    engine.

Fully offline: planner/builder/tester are stubbed, DB and workspace redirected to
tmp_path. No model, no network.
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


_PLAN = {
    "project_name": "demo", "language": "python", "summary": "s",
    "files": [{"path": "main.py", "purpose": "p"}],
    "build_order": ["main.py"], "test_command": "true",
    "success_criteria": ["ok"],
}


def _wire_pipeline(session, monkeypatch, order):
    """Stub planner/builder/tester so run() is offline and records call order."""
    monkeypatch.setattr(session.planner, "plan", lambda req, lessons="": _PLAN)

    def fake_build(plan, workspace, lessons_text=""):
        order.append("build")
        order.append(("engine", session.builder.engine.name))
        os.makedirs(workspace, exist_ok=True)
        with open(os.path.join(workspace, "main.py"), "w") as fh:
            fh.write("x = 1\n")
        return [WriteResult(os.path.join(workspace, "main.py"), True)]

    monkeypatch.setattr(session.builder, "build", fake_build)
    monkeypatch.setattr(session.tester, "test",
                        lambda ws, pl: order.append("test") or CommandResult("true", returncode=0))


# --------------------------------------------------------------------------- #
# Flag wiring
# --------------------------------------------------------------------------- #
def test_session_route_off_by_default(isolated):
    assert Session(AllowAllGate()).route is False


def test_session_accepts_route_flag(isolated):
    assert Session(AllowAllGate(), route=True).route is True


def test_session_constructs_router(isolated):
    from brains.model_router import ModelRouter
    assert isinstance(Session(AllowAllGate()).router, ModelRouter)


# --------------------------------------------------------------------------- #
# Routing runs after plan, before build, and sets builder.engine
# --------------------------------------------------------------------------- #
def test_router_runs_after_plan_before_build_and_sets_engine(isolated, monkeypatch):
    from memory import store
    from brains.model_router import RouteDecision

    session = Session(AllowAllGate(), route=True)
    order = []
    _wire_pipeline(session, monkeypatch, order)

    monkeypatch.setattr(
        session.router, "route",
        lambda ctx: order.append("route") or RouteDecision(
            engine="local", rule="r", reason="x", fallback_used=False),
    )
    monkeypatch.setattr(store, "record_route_decision", lambda *a, **k: 1, raising=False)

    session.run("build a demo")

    assert order.index("route") < order.index("build")
    assert ("engine", "local") in order  # builder.engine set from the decision


# --------------------------------------------------------------------------- #
# Routing failure is non-blocking
# --------------------------------------------------------------------------- #
def test_routing_failure_is_non_blocking(isolated, monkeypatch):
    session = Session(AllowAllGate(), route=True)
    order = []
    _wire_pipeline(session, monkeypatch, order)

    def boom(ctx):
        raise RuntimeError("router exploded")

    monkeypatch.setattr(session.router, "route", boom)

    session.run("build a demo")  # must not raise

    assert "build" in order and "test" in order  # pipeline continued on fallback


# --------------------------------------------------------------------------- #
# CLI parser
# --------------------------------------------------------------------------- #
def test_parser_accepts_engine_auto():
    import autocorp
    args = autocorp.build_parser().parse_args(["build", "req", "--engine", "auto"])
    assert args.engine == "auto"


def test_parser_still_accepts_local_and_claude():
    import autocorp
    parser = autocorp.build_parser()
    assert parser.parse_args(["build", "req", "--engine", "local"]).engine == "local"
    assert parser.parse_args(["build", "req", "--engine", "claude"]).engine == "claude"
