#!/usr/bin/env python3
"""
Workspace-anchored repair writes  (AutoCorp CLI - Phase DS11 RED)
================================================================

Drives Phase DS11: anchor self-heal repair WRITES to the workspace. DS10 made the
adapter resolve a real target file (e.g. "main.py") from the plan, and the
TesterBackedRepairContentProvider reads the REAL file at
`os.path.join(workspace, "main.py")`. But the gated WRITE goes to the bare,
workspace-RELATIVE `action.path` ("main.py"), which `Executor.write_file` opens
relative to the CURRENT WORKING DIRECTORY (safety/executor.py: `open(path,...)`).

So today the repair is READ from `<workspace>/main.py` but WRITTEN to
`<cwd>/main.py`. The acceptance `verify()` re-reads the workspace, never sees the
repair, and the self-heal loop exhausts MAX_FIX_ATTEMPTS without healing.

Pinned design (RED until GREEN implements it):
  * The self-heal gated write must target the SAME file the provider read:
    `os.path.join(workspace, "main.py")`, NOT `<cwd>/main.py`.
  * `target_path` stays workspace-RELATIVE through the adapter/work item; the
    workspace join happens at WRITE time (so DS10 resolution is unchanged).
  * After a repair, `verify()` (which reads the workspace) can observe the new
    file content and the cycle can reach `healed=True`.
  * Backward compatible: a GatedRepairFixer built with no workspace still writes
    relative paths (legacy/placeholder behavior preserved).

RED mechanisms (desired-behavior):
  * the write-path-capture test asserts the gated write lands at
    `os.path.join(workspace, "main.py")`; today it is the bare "main.py".
  * the end-to-end test asserts the workspace file is healed and the cycle
    reaches `healed=True`; today the repair lands outside the workspace so
    `verify()` never accepts.

Fully offline: planner/builder/tester stubbed; `suggest_fix` stubbed to return a
deterministic fix (no model, no Ollama); acceptance reads the workspace file or a
forced report; DB + workspace under tmp_path; CWD chdir'd to tmp_path so the
(buggy) relative writes are contained. No network, no subprocess. No production
code is changed in this phase.
"""

import os
import types

import pytest

from core import orchestrator as orch
from core.orchestrator import Session
from brains.acceptance_repair_adapter import AcceptanceRepairAdapter
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

_FIXED = "FIXED\n"


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
    """Stub the pipeline offline; build writes <workspace>/main.py, tests pass,
    and suggest_fix returns a deterministic fix WITHOUT touching any model."""
    monkeypatch.setattr(session.planner, "plan", lambda req, lessons="": _PLAN)

    def fake_build(plan, workspace, lessons_text=""):
        os.makedirs(workspace, exist_ok=True)
        with open(os.path.join(workspace, "main.py"), "w") as fh:
            fh.write("x = 1\n")
        return [WriteResult(os.path.join(workspace, "main.py"), True)]

    monkeypatch.setattr(session.builder, "build", fake_build)
    monkeypatch.setattr(session.tester, "test",
                        lambda ws, pl: CommandResult("true", returncode=0))
    monkeypatch.setattr(
        session.tester, "suggest_fix",
        lambda ws, fn, err, plan=None, findings=None: {
            "new_content": _FIXED, "filename": fn, "explanation": "x"},
    )


def _spy_workspace(session, monkeypatch):
    holder = {}
    real = session._make_workspace

    def spy(name):
        ws = real(name)
        holder["ws"] = ws
        return ws

    monkeypatch.setattr(session, "_make_workspace", spy)
    return holder


# --------------------------------------------------------------------------- #
# A/B. The gated repair write targets <workspace>/main.py, not <cwd>/main.py
# --------------------------------------------------------------------------- #
def test_repair_write_path_is_inside_workspace(isolated, monkeypatch):
    written_paths = []

    def capture_write(self, path, content):
        written_paths.append(path)
        return WriteResult(path, True)

    monkeypatch.setattr(Executor, "write_file", capture_write)

    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    ws = _spy_workspace(session, monkeypatch)
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: _FakeReport(accepted=False))

    session.run(SQLITE_REQ)

    expected = os.path.join(ws["ws"], "main.py")
    assert expected in written_paths          # RED: today the write path is bare "main.py"


# --------------------------------------------------------------------------- #
# A. The provider reads and the writer targets the SAME path
# --------------------------------------------------------------------------- #
def test_repair_write_path_matches_provider_read_path(isolated, monkeypatch):
    written_paths = []

    def capture_write(self, path, content):
        written_paths.append(path)
        return WriteResult(path, True)

    monkeypatch.setattr(Executor, "write_file", capture_write)

    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    ws = _spy_workspace(session, monkeypatch)
    monkeypatch.setattr(session.acceptance_gate, "evaluate",
                        lambda criteria, ctx: _FakeReport(accepted=False))

    session.run(SQLITE_REQ)

    # The provider reads os.path.join(workspace, "main.py"); the write MUST match.
    provider_read_path = os.path.join(ws["ws"], "main.py")
    assert written_paths, "no gated write happened"
    assert all(p == provider_read_path for p in written_paths)   # RED today


# --------------------------------------------------------------------------- #
# C. End-to-end: verify() observes the repaired workspace file and the cycle heals
# --------------------------------------------------------------------------- #
def test_end_to_end_repair_is_observed_by_verify(isolated, monkeypatch):
    monkeypatch.chdir(isolated)            # contain any (buggy) relative writes here

    session = Session(AllowAllGate(), accept=True, self_heal=True)
    _wire(session, monkeypatch)
    ws = _spy_workspace(session, monkeypatch)

    # Acceptance reads the REAL workspace file: accepted iff it contains the fix.
    def evaluate_from_file(criteria, ctx):
        try:
            with open(os.path.join(ctx.workspace, "main.py")) as fh:
                content = fh.read()
        except OSError:
            content = ""
        return _FakeReport(accepted=(_FIXED in content))

    monkeypatch.setattr(session.acceptance_gate, "evaluate", evaluate_from_file)

    # Capture the cycle's terminal healed flag (orchestrator ignores the return).
    captured = {}
    real_run = session.self_healer.run_cycle

    def wrapped(work_items, fixer, verify, max_attempts):
        cycle = real_run(work_items, fixer=fixer, verify=verify,
                         max_attempts=max_attempts)
        captured["healed"] = cycle.healed
        return cycle

    monkeypatch.setattr(session.self_healer, "run_cycle", wrapped)

    session.run(SQLITE_REQ)

    with open(os.path.join(ws["ws"], "main.py")) as fh:
        final = fh.read()
    assert final == _FIXED                  # RED: repair landed outside the workspace
    assert captured.get("healed") is True   # RED: verify() never saw the repair today


# --------------------------------------------------------------------------- #
# D. target_path stays workspace-RELATIVE through the adapter (join is at write
#    time) - guard, must stay GREEN
# --------------------------------------------------------------------------- #
def test_adapter_target_path_stays_relative():
    adapter = AcceptanceRepairAdapter()
    report = _FakeReport(accepted=False)
    items = adapter.to_work_items(report, plan=_PLAN)
    assert items[0].target_path == "main.py"     # relative, unchanged by DS11


# --------------------------------------------------------------------------- #
# E. Backward compatibility: a workspace-less GatedRepairFixer writes relative
#    placeholder paths (legacy behavior) - guard, must stay GREEN
# --------------------------------------------------------------------------- #
def test_legacy_fixer_without_workspace_writes_relative():
    written = []

    class _CapturingExecutor:
        def write_file(self, path, content):
            written.append(path)
            return WriteResult(path, True)

    work_item = types.SimpleNamespace(description="boom", target_path=None)
    GatedRepairFixer(_CapturingExecutor()).execute([work_item])

    assert written == ["repairs/repair_0.txt"]   # relative placeholder, unchanged
