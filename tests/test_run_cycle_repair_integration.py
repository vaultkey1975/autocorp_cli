#!/usr/bin/env python3
"""
run_cycle propose->apply integration  (AutoCorp CLI - Phase 8T RED)
===================================================================

Drives the integration of the repair PROPOSAL (8R) and repair APPLICATION (8S)
layers directly into the driven `SelfHealingOrchestrator.run_cycle(...)` loop.
Today run_cycle only calls `fixer.execute(work_items)` (Phase 8O); the
propose()/apply() chain lives inside collaborators (e.g. GatedRepairFixer) but is
never driven by run_cycle itself. This phase makes run_cycle drive the full
end-to-end chain:

    work_items -> fixer.propose(work_items)        (RepairAction[], "proposed")
               -> fixer.apply(actions, executor)   (gated write_file, "applied")
               -> verify()                          (re-check; heal or retry)

Expected API (pinned by these tests; ADDITIVE to run_cycle's 8O signature):
  * SelfHealingOrchestrator.run_cycle(work_items, fixer, verify, max_attempts,
        executor=None) -> RepairCycle
      - When `executor` IS provided (proposal mode), each attempt drives, in order:
            record_attempt -> fixer.propose(work_items)
                           -> fixer.apply(proposed_actions, executor)
                           -> verify()
        Stop as soon as verify() returns True (healed) or the RetryController
        budget is exhausted; the applied RepairAction objects are recorded on
        `cycle.execution_results`.
      - When `executor` is None (DEFAULT), the existing Phase 8O behavior is
        unchanged: run_cycle calls only `fixer.execute(work_items)`. This keeps the
        legacy callers (test_self_healing_loop.py etc.) green - the new parameter
        is purely additive.
      - Empty work_items -> no-op (loop skipped; propose/apply/verify never run).
      - An empty proposal list is applied safely (apply([]) is a no-op).
      - A controlled error from propose()/apply() (e.g. a non-write "run" action ->
        ValueError) PROPAGATES; run_cycle never swallows it or routes around the
        gate.
      - Command execution stays impossible: only write_file is ever reached; no
        run_command, no subprocess, no shell.

RED: the proposal-mode tests fail for MISSING IMPLEMENTATION ONLY - run_cycle does
not yet accept the `executor=` parameter, so each proposal-mode call raises
    TypeError: run_cycle() got an unexpected keyword argument 'executor'
reached LAZILY inside each test (via _run_repair_cycle) so every test fails
individually rather than collapsing the file into a collection error. The
backward-compat / existing-behavior guards below already pass and must STAY green.
`SelfHealingOrchestrator`, `RepairCycle`, `FixerExecutor`, `RepairAction`,
`FixerWorkItem`, `Executor` and the gates all already exist and are imported
normally. No production code is added or modified in this phase.

Fully offline: a RecordingFixer / SpyExecutor stand in for the real collaborators,
real propose()/apply() run only in-memory (or write to a recording spy), and
subprocess.run is monkeypatched to guard the no-command path. No model, no
network, no real shell.
"""

import subprocess

import pytest

from brains.acceptance_brain import FixerWorkItem
from brains.fixer_executor import FixerExecutor, RepairAction
from brains.self_healing_orchestrator import SelfHealingOrchestrator, RepairCycle
from safety.executor import Executor, WriteResult, CommandResult
from safety.gate import AllowAllGate


# --------------------------------------------------------------------------- #
# Local fake collaborators - fully offline, deterministic, call-recording.
# --------------------------------------------------------------------------- #
class RecordingFixer:
    """Offline spy exposing the FixerExecutor propose()/apply() surface.

    Records every call and appends to an ordered `events` trace so the loop's
    propose -> apply -> verify wiring can be observed. Touches no model, file,
    subprocess, or shell - apply() only flips status in memory."""

    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.propose_calls = []          # one work_items list per propose() call
        self.apply_calls = []            # one (actions, executor) per apply() call
        self.last_proposed = None

    def propose(self, work_items):
        self.propose_calls.append(list(work_items or []))
        self.events.append("propose")
        actions = [
            RepairAction(kind="write",
                         content=getattr(item, "description", None),
                         status="proposed")
            for item in (work_items or [])
        ]
        self.last_proposed = actions
        return actions

    def apply(self, actions, executor):
        self.apply_calls.append((list(actions or []), executor))
        self.events.append("apply")
        for action in (actions or []):
            action.status = "applied"
        return list(actions or [])


class OrderedVerify:
    """Scripted re-verification that also logs its call into a shared `events`
    list, so apply-before-verify ordering can be asserted. Pops the next bool per
    call; defaults to False once exhausted."""

    def __init__(self, results, events=None):
        self._results = list(results)
        self.events = events
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.events is not None:
            self.events.append("verify")
        return self._results.pop(0) if self._results else False


class SpyExecutor:
    """Records write_file / run_command calls without touching disk or shell."""

    def __init__(self):
        self.writes = []
        self.runs = []

    def write_file(self, path, content):
        self.writes.append((path, content))
        return WriteResult(path, written=True)

    def run_command(self, command, cwd):
        self.runs.append(command)
        return CommandResult(command, returncode=0)


class _RunProposingFixer:
    """A fixer whose propose() emits a NON-write ("run") action, applied via the
    REAL FixerExecutor.apply - which must reject it with a controlled ValueError
    (never executing a command)."""

    def __init__(self):
        self._fe = FixerExecutor()

    def propose(self, work_items):
        return [RepairAction(kind="run", command="echo hi")]

    def apply(self, actions, executor):
        return self._fe.apply(actions, executor)


def _run_repair_cycle(orch, work_items, fixer, executor, verify, max_attempts=3):
    """Lazy access to the not-yet-built proposal-mode API: RED until GREEN adds the
    optional `executor=` parameter to run_cycle(). Until then this raises
    TypeError (unexpected keyword argument 'executor') - missing implementation
    only - so each test fails individually at its own call site."""
    return orch.run_cycle(
        work_items=work_items,
        fixer=fixer,
        verify=verify,
        executor=executor,
        max_attempts=max_attempts,
    )


# --------------------------------------------------------------------------- #
# 1. run_cycle invokes fixer.propose()                          (requirement 1)
# --------------------------------------------------------------------------- #
def test_run_cycle_invokes_propose():
    events = []
    fixer = RecordingFixer(events)
    items = [FixerWorkItem("Dashboard missing export button")]
    _run_repair_cycle(SelfHealingOrchestrator(), items, fixer, SpyExecutor(),
                      OrderedVerify([True], events))
    assert len(fixer.propose_calls) == 1
    assert fixer.propose_calls[0] == items          # the work items are proposed


# --------------------------------------------------------------------------- #
# 2. Proposed RepairAction objects are passed to apply()        (requirement 2)
# --------------------------------------------------------------------------- #
def test_proposed_actions_are_passed_to_apply():
    fixer = RecordingFixer()
    _run_repair_cycle(SelfHealingOrchestrator(),
                      [FixerWorkItem("CSV export not visible")],
                      fixer, SpyExecutor(), OrderedVerify([True]))
    assert len(fixer.apply_calls) == 1
    applied_actions, _executor = fixer.apply_calls[0]
    # The EXACT objects produced by propose() are what apply() received.
    assert applied_actions == fixer.last_proposed
    assert all(isinstance(a, RepairAction) for a in applied_actions)


# --------------------------------------------------------------------------- #
# 3. apply() receives the injected executor                     (requirement 3)
# --------------------------------------------------------------------------- #
def test_apply_receives_the_executor():
    fixer = RecordingFixer()
    executor = SpyExecutor()
    _run_repair_cycle(SelfHealingOrchestrator(),
                      [FixerWorkItem("Dashboard missing export button")],
                      fixer, executor, OrderedVerify([True]))
    assert len(fixer.apply_calls) == 1
    _actions, passed_executor = fixer.apply_calls[0]
    assert passed_executor is executor              # the gated executor, untouched


# --------------------------------------------------------------------------- #
# 4. Verification callback runs AFTER apply()                   (requirement 4)
# --------------------------------------------------------------------------- #
def test_verify_runs_after_apply():
    events = []
    fixer = RecordingFixer(events)
    verify = OrderedVerify([True], events)
    _run_repair_cycle(SelfHealingOrchestrator(),
                      [FixerWorkItem("Dashboard missing export button")],
                      fixer, SpyExecutor(), verify)
    # One full attempt's ordered trace: propose, then apply, then verify.
    assert events == ["propose", "apply", "verify"]


# --------------------------------------------------------------------------- #
# 5. Successful verification marks cycle.healed = True           (requirement 5)
# --------------------------------------------------------------------------- #
def test_successful_verification_marks_healed():
    fixer = RecordingFixer()
    cycle = _run_repair_cycle(SelfHealingOrchestrator(),
                              [FixerWorkItem("Dashboard missing export button")],
                              fixer, SpyExecutor(), OrderedVerify([True]))
    assert isinstance(cycle, RepairCycle)
    assert cycle.healed is True
    assert cycle.retry_state.attempts == 1
    assert cycle.retry_state.exhausted is False
    # Applied actions are recorded on the cycle as the executed results.
    assert len(cycle.execution_results) == 1
    assert cycle.execution_results[0].status == "applied"


# --------------------------------------------------------------------------- #
# 6. Failed verification continues the RetryController flow      (requirement 6)
# --------------------------------------------------------------------------- #
def test_failed_verification_then_heals():
    fixer = RecordingFixer()
    cycle = _run_repair_cycle(SelfHealingOrchestrator(),
                              [FixerWorkItem("CSV export not visible")],
                              fixer, SpyExecutor(),
                              OrderedVerify([False, True]))   # fails once, then heals
    assert cycle.healed is True
    assert cycle.retry_state.attempts == 2
    assert cycle.retry_state.exhausted is False
    assert len(fixer.propose_calls) == 2            # re-proposed each retry
    assert len(fixer.apply_calls) == 2              # re-applied each retry


def test_persistent_failure_exhausts_budget():
    fixer = RecordingFixer()
    cycle = _run_repair_cycle(SelfHealingOrchestrator(),
                              [FixerWorkItem("Dashboard missing export button")],
                              fixer, SpyExecutor(),
                              OrderedVerify([False, False, False]),  # never heals
                              max_attempts=3)
    assert cycle.healed is False
    assert cycle.retry_state.attempts == 3
    assert cycle.retry_state.exhausted is True
    assert len(fixer.propose_calls) == 3
    assert len(fixer.apply_calls) == 3


# --------------------------------------------------------------------------- #
# 7. Empty proposal lists are handled safely                    (requirement 7)
# --------------------------------------------------------------------------- #
def test_empty_work_items_is_noop():
    fixer = RecordingFixer()
    cycle = _run_repair_cycle(SelfHealingOrchestrator(), [], fixer,
                              SpyExecutor(), OrderedVerify([True]))
    # Nothing to repair: propose/apply/verify never run; no attempt consumed.
    assert fixer.propose_calls == []
    assert fixer.apply_calls == []
    assert cycle.retry_state.attempts == 0
    assert cycle.execution_results == []


def test_empty_proposal_list_applied_safely():
    # Non-empty work items but a fixer that proposes nothing actionable: apply()
    # is still driven with an empty list (a documented no-op) and nothing breaks.
    class _EmptyProposingFixer(RecordingFixer):
        def propose(self, work_items):
            self.propose_calls.append(list(work_items or []))
            self.events.append("propose")
            self.last_proposed = []
            return []

    fixer = _EmptyProposingFixer()
    cycle = _run_repair_cycle(SelfHealingOrchestrator(),
                              [FixerWorkItem("Dashboard missing export button")],
                              fixer, SpyExecutor(), OrderedVerify([True]))
    assert len(fixer.apply_calls) == 1
    applied_actions, _executor = fixer.apply_calls[0]
    assert applied_actions == []                    # apply([]) is a documented no-op
    assert cycle.execution_results == []            # nothing applied
    assert cycle.healed is True                     # verify still consulted


# --------------------------------------------------------------------------- #
# 8. Unsupported actions fail safely                            (requirement 8)
# --------------------------------------------------------------------------- #
def test_unsupported_run_action_rejected_safely():
    spy = SpyExecutor()
    # A "run" action reaches the REAL apply(), which rejects it with ValueError;
    # run_cycle must let that controlled error propagate (never swallow it or run
    # a command). RED today: the missing `executor=` parameter raises TypeError
    # instead, so pytest.raises(ValueError) is not satisfied -> failing.
    with pytest.raises(ValueError):
        _run_repair_cycle(SelfHealingOrchestrator(),
                          [FixerWorkItem("Dashboard missing export button")],
                          _RunProposingFixer(), spy, OrderedVerify([True]))
    assert spy.runs == []                           # no command ever executed
    assert spy.writes == []


# --------------------------------------------------------------------------- #
# 9. Command execution remains impossible                       (requirement 9)
# --------------------------------------------------------------------------- #
def test_command_execution_impossible(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: runs.append(a))
    spy = SpyExecutor()
    # Drive the REAL FixerExecutor through run_cycle (proposal mode): apply must
    # touch only write_file, never run_command or subprocess.
    _run_repair_cycle(SelfHealingOrchestrator(),
                      [FixerWorkItem("Dashboard missing export button")],
                      FixerExecutor(), spy, OrderedVerify([True]))
    assert spy.runs == []                           # no gated command
    assert runs == []                               # no subprocess
    assert spy.writes != []                         # write path is the only effect


# --------------------------------------------------------------------------- #
# 10. Existing propose() behavior remains unchanged             (requirement 10)
#     (green guard - must STAY passing)
# --------------------------------------------------------------------------- #
def test_existing_propose_behavior_unchanged():
    proposals = FixerExecutor().propose(
        [FixerWorkItem("Dashboard missing export button")])
    assert [p.status for p in proposals] == ["proposed"]
    assert proposals[0].kind == "write"
    assert proposals[0].content == "Dashboard missing export button"


# --------------------------------------------------------------------------- #
# 11. Existing apply() behavior remains unchanged               (requirement 11)
#     (green guard - must STAY passing)
# --------------------------------------------------------------------------- #
def test_existing_apply_behavior_unchanged(tmp_path):
    action = RepairAction(kind="write", path=str(tmp_path / "main.py"),
                          content="x = 1\n")
    applied = FixerExecutor().apply([action], Executor(AllowAllGate()))
    assert applied[0].status == "applied"
    assert (tmp_path / "main.py").read_text() == "x = 1\n"
    # And a non-write action is still rejected safely.
    with pytest.raises(ValueError):
        FixerExecutor().apply([RepairAction(kind="run", command="echo hi")],
                              SpyExecutor())


# --------------------------------------------------------------------------- #
# 12. Existing execute()-mode run_cycle is unchanged (default, no executor)
#     (green guard - proves the new parameter is additive; requirement 12)
# --------------------------------------------------------------------------- #
def test_execute_mode_run_cycle_unchanged():
    # Legacy callers pass NO executor: run_cycle must still drive only execute(),
    # exactly as Phase 8O does today. This is the contract the existing 459 tests
    # rely on, so it must stay green both before AND after GREEN.
    class _ExecuteOnlyFixer:
        def __init__(self):
            self.execute_calls = []

        def execute(self, work_items):
            self.execute_calls.append(list(work_items or []))
            from brains.fixer_executor import FixExecutionResult
            return [FixExecutionResult(description=i.description, status="fixed")
                    for i in (work_items or [])]

    fixer = _ExecuteOnlyFixer()
    cycle = SelfHealingOrchestrator().run_cycle(
        [FixerWorkItem("Dashboard missing export button")],
        fixer=fixer, verify=lambda: True, max_attempts=3)
    assert cycle.healed is True
    assert len(fixer.execute_calls) == 1
    assert cycle.execution_results[0].status == "fixed"
