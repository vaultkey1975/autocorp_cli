#!/usr/bin/env python3
"""
Gated Repair Fixer  (AutoCorp CLI - brains)  [Phase 8T]
=======================================================

A thin, additive adapter that lets the self-healing loop drive REAL, gated,
write-only repairs without changing the run_cycle contract. A GatedRepairFixer is
constructed with a gated Executor and exposes the `.execute(work_items)` interface
that `SelfHealingOrchestrator.run_cycle` already calls - so it is a drop-in
replacement for the inert FixerExecutor inside the loop.

`execute` runs the existing two-step chain on the existing FixerExecutor:
    FixerExecutor.propose(work_items)  ->  RepairAction[] (status "proposed")
    FixerExecutor.apply(actions, executor)  ->  gated write_file, status "applied"

WRITE-ONLY + GATED: every write goes through `executor.write_file`, whose gate
(CommandGate / WatchdogGate) stays authoritative - a blocked write leaves the
action NOT "applied". This adapter runs no command, no subprocess, and no shell.
Each proposed action is given a deterministic, workspace-RELATIVE path before it
is applied (propose() leaves the path unset), so the gated write lands under the
current working directory rather than at an absolute location.

Session activation (wiring this into Session.run) is deliberately DEFERRED to a
later phase; this phase only makes the adapter available to run_cycle.
"""

from brains.fixer_executor import FixerExecutor


class GatedRepairFixer:
    """Drives propose() -> apply() through an injected gated Executor.

    Constructed with the executor whose gate must approve every write; holds a
    plain FixerExecutor to reuse the existing proposal/application logic."""

    def __init__(self, executor, generator=None):
        self.executor = executor
        self.generator = generator
        self._fixer = FixerExecutor()

    def execute(self, work_items) -> list:
        """Propose a repair per work item, give each a deterministic relative path,
        and apply them through the gated executor - returning the resulting
        RepairAction objects in input order. An empty input is a no-op ([]).

        Uses ONLY `executor.write_file` (via FixerExecutor.apply); no command,
        subprocess, or shell. The gate decides whether each write succeeds."""
        actions = self._fixer.propose(work_items)

        for index, action in enumerate(actions):
            if not action.path:
                action.path = f"repairs/repair_{index}.txt"
            if self.generator is not None:
                generated = self.generator.generate(action.path, action.content)
                if generated:
                    action.content = generated

        return self._fixer.apply(actions, self.executor)
