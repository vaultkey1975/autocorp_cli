#!/usr/bin/env python3
"""
Self-Healing Orchestrator  (AutoCorp CLI - brains)  [Phase 8O]
=============================================================

Orchestration-layer data structures (8N) PLUS the first driven repair loop (8O):
`run_cycle` runs one `fix -> verify -> retry` loop over FixerWorkItem objects and
returns a populated RepairCycle recording the outcome.

INJECTED-COLLABORATORS ONLY: `run_cycle` talks only to its injected `fixer`
(`.execute(work_items)`) and `verify` (`() -> bool`) collaborators plus the pure
RetryController. It owns no engine, runs no model, touches no network, and starts
no subprocess of its own - every real action happens behind the injected
collaborators, which in production route through the existing safety gate.
"""

from dataclasses import dataclass, field

from brains.retry_controller import RetryController


@dataclass
class RepairCycle:
    """A container for one repair cycle's state.

    Pure data: `fix_requests` and `execution_results` default to empty lists,
    `retry_state` to None, and `healed` to False. It holds the chain (FixRequest
    -> FixExecutionResult plus a RetryState) and the terminal outcome (`healed`)
    of a driven cycle."""
    fix_requests: list = field(default_factory=list)
    execution_results: list = field(default_factory=list)
    retry_state: object = None
    healed: bool = False


class SelfHealingOrchestrator:
    """Creates and populates RepairCycle containers. Holds no state of its own."""

    def create_cycle(self) -> RepairCycle:
        """Return a fresh, empty RepairCycle."""
        return RepairCycle()

    def attach_fix_requests(self, cycle: RepairCycle, fix_requests) -> RepairCycle:
        """Store `fix_requests` on the cycle. Mutates in place, returns the cycle."""
        cycle.fix_requests = fix_requests
        return cycle

    def attach_execution_results(self, cycle: RepairCycle, results) -> RepairCycle:
        """Store `results` on the cycle. Mutates in place, returns the cycle."""
        cycle.execution_results = results
        return cycle

    def attach_retry_state(self, cycle: RepairCycle, state) -> RepairCycle:
        """Store `state` on the cycle. Mutates in place, returns the cycle."""
        cycle.retry_state = state
        return cycle

    def run_cycle(self, work_items, fixer, verify, max_attempts,
                  executor=None) -> RepairCycle:
        """Drive one repair cycle and return a populated RepairCycle.

        Loop, bounded by a fresh RetryController budget of `max_attempts`. With no
        `work_items` there is nothing to repair, so the loop is skipped entirely:
        the fixer is never called and no attempt is consumed.

        LEGACY MODE (`executor` is None, the default) - per iteration:
            record_attempt -> fixer.execute(work_items) -> verify()
        This is the unchanged Phase 8O behavior.

        PROPOSAL MODE (`executor` provided) - per iteration the propose/apply
        layers are driven end-to-end:
            record_attempt -> fixer.propose(work_items)
                           -> fixer.apply(proposed_actions, executor)
                           -> verify()
        The applied RepairAction objects are recorded on `cycle.execution_results`.
        A controlled error from propose()/apply() (e.g. a non-write action ->
        ValueError) is allowed to propagate; it is never swallowed.

        Either way: stop as soon as `verify()` returns True (healed) or the retry
        budget is exhausted. All work happens through the injected
        `fixer`/`verify`/`executor` collaborators - this method never reaches a
        real engine, subprocess, or network itself, and runs no command or shell:
        proposal mode applies repairs only through `fixer.apply(..., executor)`,
        whose gated `write_file` stays authoritative."""
        retry = RetryController()
        state = retry.create(max_attempts)
        cycle = RepairCycle(retry_state=state)

        if not work_items:
            return cycle

        while not cycle.healed and not retry.is_exhausted(state):
            retry.record_attempt(state)
            if executor is None:
                cycle.execution_results = fixer.execute(work_items)
            else:
                actions = fixer.propose(work_items)
                cycle.execution_results = fixer.apply(actions, executor)
            cycle.healed = bool(verify())

        return cycle
