#!/usr/bin/env python3
"""
Fixer Executor  (AutoCorp CLI - brains)  [Phase 8L]
===================================================

The structural seam between Phase 8K handoff objects and a future Fixer run. A
FixerExecutor consumes FixerWorkItem objects and returns one FixExecutionResult
per item, preserving order.

PLANNING ONLY (Phase 8L): `execute` does NOT invoke the Builder Brain, the Fixer
Brain, the Executor, or any model; it runs no command, edits no file, and adds no
autonomous execution or retry logic. Each result is recorded with status
"planned" - it only DESCRIBES the work that would be handed to the Fixer. Real
execution semantics are a deliberate, separately-approved future phase.

GATED DRY-RUN PROPOSALS (Phase 8R): `propose` is an ADDITIVE seam that turns
FixerWorkItem objects into inert, gate-reviewable RepairAction objects with status
"proposed". It builds pure data only - no file write, no command, no subprocess,
no Executor call, no workspace mutation. APPLYING a proposal (and submitting it to
the gate) is a separate, later phase. `execute`/FixExecutionResult are unchanged.
"""

from dataclasses import dataclass


@dataclass
class FixExecutionResult:
    """The outcome record for a single FixerWorkItem.

    Pure data. `description` is preserved verbatim from the work item; `status`
    is the execution state ("planned" in this phase - nothing is actually run)."""
    description: str
    status: str


@dataclass
class RepairAction:
    """A single PROPOSED repair - pure, inert, gate-reviewable data.

    `kind` is "write" or "run"; a "write" carries `path`/`content`, a "run"
    carries `command`. `status` defaults to "proposed": the action only DESCRIBES
    a repair that would later be applied through the gated Executor. Constructing
    or holding a RepairAction runs nothing and writes nothing."""
    kind: str
    path: str = None
    command: str = None
    content: str = None
    status: str = "proposed"


class FixerExecutor:
    """Turns FixerWorkItem objects into FixExecutionResult records.

    Does not run anything: see the module docstring. Constructed without
    dependencies so it stays decoupled from the Builder/Fixer/Executor."""

    def execute(self, work_items) -> list:
        """Return one FixExecutionResult per work item, in input order, each with
        status "planned". An empty input yields an empty list (no-op)."""
        return [
            FixExecutionResult(description=item.description, status="planned")
            for item in (work_items or [])
        ]

    def propose(self, work_items) -> list:
        """Return one PROPOSED RepairAction per work item, in input order. An empty
        input yields an empty list (no-op).

        Pure data only: this builds and returns RepairAction objects and performs
        NO file write, command, subprocess, or Executor call. A work item without a
        `description` is an unsupported type and raises TypeError (it never yields a
        malformed or dangerous action)."""
        actions = []
        for item in (work_items or []):
            description = getattr(item, "description", None)
            if not isinstance(description, str):
                raise TypeError(
                    f"Unsupported work item {item!r}: expected a FixerWorkItem with "
                    f"a string `description`."
                )
            actions.append(
                RepairAction(kind="write", content=description, status="proposed")
            )
        return actions

    def apply(self, actions, executor) -> list:
        """Apply proposed WRITE actions through the gated `executor.write_file`,
        flipping each from "proposed" to "applied", in input order. An empty input
        yields an empty list (no-op; the executor is untouched).

        Write actions ONLY: a non-write action (e.g. kind "run") is rejected with a
        controlled ValueError and an unsupported object with a controlled TypeError.
        This method uses ONLY `executor.write_file`; it runs no command, no
        subprocess, and no shell. The gate inside write_file stays authoritative."""
        for action in (actions or []):
            if not isinstance(action, RepairAction):
                raise TypeError(
                    f"Unsupported action {action!r}: expected a RepairAction."
                )
            if action.kind != "write":
                raise ValueError(
                    f"apply only handles write actions; got kind {action.kind!r}."
                )
            result = executor.write_file(action.path, action.content)
            # Gate stays authoritative: only a successful (allowed) write flips the
            # action to "applied"; a blocked or failed write leaves it untouched.
            if result.written:
                action.status = "applied"
        return list(actions or [])
