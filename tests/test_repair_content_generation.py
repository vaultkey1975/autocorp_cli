#!/usr/bin/env python3
"""
Repair content generation seam  (AutoCorp CLI - Phase 8X RED)
=============================================================

Drives the design of Phase 8X: closing the "artificial repair" gap. Today a
proposed RepairAction's `content` is the failure DESCRIPTION (prose), so the gated
write replaces a real source file with text and `verify()` can never flip. This
phase introduces an optional, INJECTED content generator so a repair can carry REAL
file content while every safety property is preserved.

    work_items -> GatedRepairFixer.execute
        -> FixerExecutor.propose()            (RepairAction, content=description)
        -> [resolve path / placeholder fallback]   (8W)
        -> generator.generate(target_path, description) -> new content (if present)
        -> FixerExecutor.apply(actions, executor)  (gated write of the new content)

Pinned design (RED until GREEN implements it; ADDITIVE):
  * GatedRepairFixer(executor, generator=None). Default None == today's behavior.
  * When a generator is supplied, for EACH proposed action (after path resolution)
    `generator.generate(action.path, description)` is called; a NON-EMPTY return
    replaces `action.content` before apply().
  * When the generator returns None or "" (empty), the action keeps its existing
    description content - graceful fallback.
  * With NO generator, behavior is byte-for-byte identical to today (8W).
  * The generated content is what reaches `executor.write_file`. Still WRITE-ONLY +
    GATED: only write_file is used; the gate stays authoritative; no run_command,
    no subprocess, no shell. Order preserved. propose()/apply()/run_cycle() and the
    8W target-path/placeholder logic are UNCHANGED.

RED: the generator tests fail for MISSING IMPLEMENTATION ONLY - GatedRepairFixer's
constructor does not yet accept `generator`, so each generator-bearing construction
raises
    TypeError: GatedRepairFixer.__init__() got an unexpected keyword argument 'generator'
reached inside its own test, so the tests fail individually. The no-generator
guards already pass and must STAY green. `GatedRepairFixer`, `FixerExecutor`,
`RepairAction`, `FixerWorkItem`, `Executor`, the gates, and the orchestrator all
already exist and are imported normally. No production code is added or modified in
this phase.

Fully offline: generators are deterministic local fakes (NO model, NO network);
a SpyExecutor records writes without disk, real gated writes go to tmp_path, and a
monkeypatched subprocess.run guards the no-command path.
"""

import subprocess

import pytest

from brains.acceptance_brain import FixerWorkItem
from brains.fixer_executor import FixerExecutor, RepairAction
from brains.gated_repair_fixer import GatedRepairFixer
from brains.self_healing_orchestrator import SelfHealingOrchestrator
from safety.executor import Executor, WriteResult, CommandResult
from safety.gate import AllowAllGate, CommandGate, Decision


# --------------------------------------------------------------------------- #
# Deterministic, fully offline fake generators (NO model, NO network).
# --------------------------------------------------------------------------- #
class FakeGenerator:
    """Returns a fixed chunk of 'real' content and records every call as
    (target_path, description). Stands in for the future model-backed generator."""

    def __init__(self, content="def fixed():\n    return True\n"):
        self.content = content
        self.calls = []

    def generate(self, target_path, description):
        self.calls.append((target_path, description))
        return self.content


class EchoGenerator:
    """Returns content derived from the description, so per-action ordering is
    observable in the written output."""

    def __init__(self):
        self.calls = []

    def generate(self, target_path, description):
        self.calls.append((target_path, description))
        return f"# fix for: {description}\n"


class NoneGenerator:
    """Always declines (returns None) - the fallback-to-description path."""

    def generate(self, target_path, description):
        return None


class EmptyGenerator:
    """Returns empty content - also the fallback-to-description path."""

    def generate(self, target_path, description):
        return ""


class _DenyWriteGate(CommandGate):
    """Blocks every write (and command)."""

    def review_write(self, path, content):
        return Decision.block("denied for test")

    def review_command(self, command, cwd):
        return Decision.block("denied for test")


class SpyExecutor:
    """Records write_file / run_command without touching disk or shell."""

    def __init__(self):
        self.writes = []
        self.runs = []

    def write_file(self, path, content):
        self.writes.append((path, content))
        return WriteResult(path, written=True)

    def run_command(self, command, cwd):
        self.runs.append(command)
        return CommandResult(command, returncode=0)


def _files_under(path):
    return [p for p in path.rglob("*") if p.is_file()]


DESC = "Dashboard missing export button"


# =========================================================================== #
# RED - new generator-seam behavior
# =========================================================================== #

# 1. generator.generate(...) is called once per proposed action
def test_generator_called_per_action():
    gen = FakeGenerator()
    GatedRepairFixer(SpyExecutor(), generator=gen).execute([
        FixerWorkItem("a", target_path="a.py"),
        FixerWorkItem("b", target_path="b.py"),
    ])
    assert len(gen.calls) == 2


# 2. generated content replaces RepairAction.content
def test_generated_content_replaces_action_content():
    gen = FakeGenerator()
    actions = GatedRepairFixer(SpyExecutor(), generator=gen).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert actions[0].content == gen.content
    assert actions[0].content != DESC               # no longer the prose description


# 3. generated content is what reaches executor.write_file()
def test_generated_content_reaches_write_file():
    gen = FakeGenerator()
    spy = SpyExecutor()
    GatedRepairFixer(spy, generator=gen).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert spy.writes[0] == ("ui/main_window.py", gen.content)


# 4. generator receives the resolved target_path and the failure description
def test_generator_receives_target_path_and_description():
    gen = FakeGenerator()
    GatedRepairFixer(SpyExecutor(), generator=gen).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert gen.calls == [("ui/main_window.py", DESC)]


# 5. generator returning None falls back to the description content
def test_none_generator_falls_back_to_description():
    spy = SpyExecutor()
    GatedRepairFixer(spy, generator=NoneGenerator()).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert spy.writes[0] == ("ui/main_window.py", DESC)


# 6. generator returning empty content falls back to the description content
def test_empty_generator_falls_back_to_description():
    spy = SpyExecutor()
    GatedRepairFixer(spy, generator=EmptyGenerator()).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert spy.writes[0] == ("ui/main_window.py", DESC)


# 7. a real gated write lands the GENERATED content at the resolved target
def test_generated_write_lands_at_target_path_realfs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gen = FakeGenerator()
    GatedRepairFixer(Executor(AllowAllGate()), generator=gen).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    target = tmp_path / "ui" / "main_window.py"
    assert target.is_file()
    assert target.read_text() == gen.content        # real content on disk


# 8. with a generator, no run_command and no subprocess are ever reached
def test_no_subprocess_with_generator(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: runs.append(a))
    spy = SpyExecutor()
    GatedRepairFixer(spy, generator=FakeGenerator()).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert spy.runs == []                            # no gated command
    assert runs == []                                # no subprocess


# 9. order preserved with a generator (per-action generated content, in order)
def test_ordering_preserved_with_generator():
    spy = SpyExecutor()
    GatedRepairFixer(spy, generator=EchoGenerator()).execute([
        FixerWorkItem("a", target_path="a.py"),
        FixerWorkItem("b", target_path="b.py"),
        FixerWorkItem("c", target_path="c.py"),
    ])
    assert [w[0] for w in spy.writes] == ["a.py", "b.py", "c.py"]
    assert [w[1] for w in spy.writes] == [
        "# fix for: a\n", "# fix for: b\n", "# fix for: c\n"]


# 10. gate authority holds WITH a generator: blocked write writes nothing and the
#     action is not "applied" (the generator does not write around the gate)
def test_blocked_gate_with_generator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    actions = GatedRepairFixer(Executor(_DenyWriteGate()),
                               generator=FakeGenerator()).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert _files_under(tmp_path) == []              # gate blocked: nothing on disk
    assert all(a.status != "applied" for a in actions)


# =========================================================================== #
# GUARDS - no generator -> byte-for-byte today's behavior (must STAY green)
# =========================================================================== #

# 11. no generator: a resolved target path still gets the description content (8W)
def test_no_generator_behavior_unchanged():
    spy = SpyExecutor()
    GatedRepairFixer(spy).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert spy.writes[0] == ("ui/main_window.py", DESC)


# 12. no generator: path=None still falls back to repairs/repair_0.txt (8W)
def test_no_generator_placeholder_unchanged():
    spy = SpyExecutor()
    GatedRepairFixer(spy).execute([FixerWorkItem(DESC)])   # no target_path
    assert spy.writes[0] == ("repairs/repair_0.txt", DESC)


# 13. no generator: gate authority unchanged (blocked write, not applied)
def test_gate_authority_no_generator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    actions = GatedRepairFixer(Executor(_DenyWriteGate())).execute(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert _files_under(tmp_path) == []
    assert all(a.status != "applied" for a in actions)


# 14. propose() unchanged - targeted item -> proposed write, path=target, content=desc
def test_propose_unchanged():
    actions = FixerExecutor().propose(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")])
    assert actions[0].kind == "write"
    assert actions[0].status == "proposed"
    assert actions[0].path == "ui/main_window.py"
    assert actions[0].content == DESC


# 15. apply() unchanged - write applied; non-write rejected; no command
def test_apply_unchanged(tmp_path):
    action = RepairAction(kind="write", path=str(tmp_path / "main.py"),
                          content="x = 1\n")
    applied = FixerExecutor().apply([action], Executor(AllowAllGate()))
    assert applied[0].status == "applied"
    assert (tmp_path / "main.py").read_text() == "x = 1\n"
    spy = SpyExecutor()
    with pytest.raises(ValueError):
        FixerExecutor().apply([RepairAction(kind="run", command="echo hi")], spy)
    assert spy.runs == []


# 16. run_cycle() unchanged - a no-generator GatedRepairFixer still drives a heal
def test_run_cycle_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixer = GatedRepairFixer(Executor(AllowAllGate()))
    cycle = SelfHealingOrchestrator().run_cycle(
        [FixerWorkItem(DESC, target_path="ui/main_window.py")],
        fixer=fixer, verify=lambda: True, max_attempts=3)
    assert cycle.healed is True
    assert cycle.retry_state.attempts == 1
