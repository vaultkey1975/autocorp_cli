#!/usr/bin/env python3
"""
Tester engine construction  (AutoCorp CLI - Phase DS7 RED)
==========================================================

Drives Phase DS7: activate the EXISTING `TesterBrain(engine=...)` seam through
`Session` construction. Today `Session.__init__` builds
`self.tester = TesterBrain(self.executor)` with no engine, so `suggest_fix`
always takes the offline `llm.generate_json` fallback and the DS6 repair chain
inherits an engine-less tester.

This phase changes EXACTLY ONE thing: Session constructs TesterBrain with an
engine obtained from the EngineRegistry, defaulting to the offline `local`
engine. It does NOT touch ModelRouter, Builder routing, or the DS6 repair wiring,
and it never activates DeepSeek implicitly.

Pinned design (RED until GREEN implements it):
  * `Session.__init__` gains a `tester_engine: str = "local"` configuration
    (a registry engine NAME), defaulting to the free offline local engine.
  * Session builds the engine via `engine_registry.create(tester_engine)` and
    passes it: `self.tester = TesterBrain(self.executor, engine=<engine>)`.
  * The engine is a `BaseEngine`; the default is `name == "local"`.
  * DeepSeek is only ever selected when `tester_engine="deepseek"` is passed
    explicitly - never by default.
  * Because the DS6 repair chain reuses `self.tester`, the provider's tester
    automatically carries the engine with NO provider changes.
  * Backward compatible: legacy `Session(gate)` keeps working.

RED mechanisms:
  * kwarg tests: `Session(..., tester_engine=...)` raises TypeError today
    (unknown kwarg).
  * default/inheritance tests: `session.tester.engine` is `None` today, so
    `isinstance(..., BaseEngine)` / `.name` assertions fail.

Fully offline: engine CONSTRUCTORS are inert (DeepSeek/Claude defer all
network/subprocess to generate()/available()); the repair-chain test spies
`run_cycle` so nothing executes and `suggest_fix` is never actually called. No
model, no network, no subprocess. DB + workspace redirected to tmp_path.
"""

import os

import pytest

from core import orchestrator as orch
from core.orchestrator import Session
from brains import engine_registry
from brains.base_engine import BaseEngine
from brains.tester import TesterBrain
from brains.gated_repair_fixer import GatedRepairFixer
from safety.executor import WriteResult, CommandResult
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


def _capture_run_cycle(session, monkeypatch):
    captured = {}

    def fake_run_cycle(work_items, fixer=None, verify=None, max_attempts=None):
        captured["fixer"] = fixer
        return None

    monkeypatch.setattr(session.self_healer, "run_cycle", fake_run_cycle)
    return captured


# --------------------------------------------------------------------------- #
# A. Session accepts a tester_engine configuration
# --------------------------------------------------------------------------- #
def test_session_accepts_tester_engine_kwarg(isolated):
    session = Session(AllowAllGate(), tester_engine="local")   # RED: TypeError today
    assert session.tester.engine is not None


# --------------------------------------------------------------------------- #
# A/C. Default Session gives the tester an offline local engine
# --------------------------------------------------------------------------- #
def test_default_tester_engine_is_local(isolated):
    session = Session(AllowAllGate())                          # legacy/default ctor
    assert isinstance(session.tester.engine, BaseEngine)       # RED: None today
    assert session.tester.engine.name == "local"


# --------------------------------------------------------------------------- #
# B. The engine is sourced from the EngineRegistry (no router, no builder)
# --------------------------------------------------------------------------- #
def test_engine_comes_from_registry(isolated, monkeypatch):
    calls = []
    orig = engine_registry.create

    def spy(name, **opts):
        calls.append(name)
        return orig(name, **opts)

    monkeypatch.setattr(engine_registry, "create", spy)

    session = Session(AllowAllGate(), tester_engine="local")   # RED: TypeError today

    assert "local" in calls                                    # built via registry
    assert isinstance(session.tester.engine, BaseEngine)


# --------------------------------------------------------------------------- #
# C. DeepSeek never activates implicitly (default is not deepseek)
# --------------------------------------------------------------------------- #
def test_default_never_selects_deepseek(isolated):
    session = Session(AllowAllGate())
    assert session.tester.engine.name != "deepseek"            # RED: None.name today


# --------------------------------------------------------------------------- #
# C. DeepSeek is selectable, but ONLY when explicitly named
# --------------------------------------------------------------------------- #
def test_deepseek_requires_explicit_selection(isolated):
    session = Session(AllowAllGate(), tester_engine="deepseek")  # RED: TypeError today
    assert session.tester.engine.name == "deepseek"


# --------------------------------------------------------------------------- #
# D. The DS6 repair chain inherits the Session tester's engine (no extra wiring)
# --------------------------------------------------------------------------- #
def test_repair_chain_inherits_tester_engine(isolated, monkeypatch):
    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    _force_report(session, monkeypatch, accepted=False)
    captured = _capture_run_cycle(session, monkeypatch)

    session.run(SQLITE_REQ)

    fixer = captured.get("fixer")
    assert isinstance(fixer, GatedRepairFixer)
    provider = fixer.generator.provider
    assert provider.tester is session.tester                   # DS6 guarantee
    assert isinstance(provider.tester.engine, BaseEngine)      # RED: None today


# --------------------------------------------------------------------------- #
# E. Backward compatibility: legacy construction still yields a TesterBrain
#    (guard test - must stay GREEN)
# --------------------------------------------------------------------------- #
def test_legacy_construction_still_builds_tester(isolated):
    session = Session(AllowAllGate())                          # legacy signature
    assert isinstance(session.tester, TesterBrain)
