#!/usr/bin/env python3
"""
Acceptance Brain  (AutoCorp CLI - brains)  [Phase 8H-8J, wired into Session]
=============================================================================

The Acceptance -> Fix Feedback Loop: converts acceptance failures into
structured repair work for the existing Fixer, running AFTER the Tester and
Reviewer in the orchestration flow (see core/orchestrator.py's
`self.acceptance_brain`).

  * AcceptanceResult - did acceptance pass, and the human-readable failure
    strings to address.
  * AcceptanceBrain - converts an AcceptanceResult through a pipeline of pure
    data transforms (RepairTask -> FixRequest -> FixerWorkItem) that the
    existing Fixer consumes; it builds these objects but does not itself
    invoke the Fixer, execute a repair, or trigger a retry/rebuild - that
    remains the caller's responsibility.

The existing deterministic Acceptance Gate (brains/acceptance.py) is
unrelated and untouched by this module.
"""

from dataclasses import dataclass, field


@dataclass
class AcceptanceResult:
    """Outcome of an acceptance evaluation: pass/fail + the failures to address.

    Pure data (no behaviour). `failures` is a list of human-readable strings such
    as "Dashboard missing export button"."""
    passed: bool = False
    failures: list = field(default_factory=list)


@dataclass
class RepairTask:
    """A structured planning object for a single acceptance failure.

    Pure data: it DESCRIBES repair work for the existing Fixer to consume in a
    future phase. It carries no behaviour, runs nothing, and triggers no fix,
    retry, or rebuild. `description` is the original failure text, preserved
    verbatim."""
    description: str


@dataclass
class FixRequest:
    """A structured request for the existing Fixer, converted from a RepairTask.

    Pure data: it REQUESTS a fix; it carries no behaviour, invokes no Fixer, and
    triggers no execution, retry, or rebuild. `description` is preserved verbatim
    from the originating RepairTask."""
    description: str


@dataclass
class FixerWorkItem:
    """A handoff object for the existing Fixer, converted from a FixRequest.

    Pure data: it represents a single unit of repair work to hand to the Fixer in
    a future phase. It carries no behaviour, invokes no Fixer, and triggers no
    execution, retry, or rebuild. `description` is preserved verbatim from the
    originating FixRequest. `target_path` is an OPTIONAL hint naming the workspace
    file a repair should land in (None when unknown); it defaults to None so legacy
    single-argument construction is unchanged."""
    description: str
    target_path: str = None


class AcceptanceBrain:
    """Turns acceptance failures into fix requests for the existing Fixer
    (Phase 8H-8J). Wired into Session as `self.acceptance_brain`."""

    def fix_requests(self, result: "AcceptanceResult") -> list:
        """Convert an AcceptanceResult into a list of fix-request strings:
        the failures when acceptance did NOT pass, otherwise an empty list.

        This only REPORTS the work to be done; it triggers no fixing, no retry,
        and no rebuild - those remain out of scope by design."""
        if result is None or result.passed:
            return []
        return list(result.failures or [])

    def record_failures(self, project, result: "AcceptanceResult") -> None:
        """Attach the acceptance failures to project state as
        `project.acceptance_failures` (always a list). Non-destructive: it only
        records what failed; it does not act on it."""
        project.acceptance_failures = self.fix_requests(result)

    def plan_repairs(self, result: "AcceptanceResult") -> list:
        """Convert acceptance failures into a list of structured RepairTask
        planning objects (one per failure) for the existing Fixer to consume in a
        FUTURE phase.

        Planning ONLY: it executes no fixes, invokes no Fixer, and triggers no
        retry, rerun, or rebuild. Returns one RepairTask per failure when
        acceptance did not pass, else an empty list."""
        if result is None or result.passed:
            return []
        return [RepairTask(description) for description in (result.failures or [])]

    def to_fix_requests(self, repair_tasks) -> list:
        """Convert RepairTask planning objects into structured FixRequest objects
        (one per task) for the existing Fixer to consume in a FUTURE phase.

        Conversion ONLY: it builds request objects; it invokes no Fixer, executes
        no repairs, and triggers no retry, rerun, or rebuild. Returns one
        FixRequest per RepairTask, preserving the description and order; an empty
        input yields an empty list."""
        return [FixRequest(task.description) for task in (repair_tasks or [])]

    def to_fixer_work_items(self, fix_requests) -> list:
        """Hand FixRequest objects to the existing Fixer by converting them into
        FixerWorkItem objects (one per request) - the first execution-ADJACENT
        seam between planning objects and the Fixer.

        HANDOFF SHAPING ONLY: it builds work items; it does NOT invoke the Fixer,
        execute repairs, retry, rerun, or rebuild, and it changes no orchestrator
        flow. Returns one FixerWorkItem per FixRequest, preserving the description
        and order; an empty input yields an empty list."""
        return [FixerWorkItem(request.description) for request in (fix_requests or [])]
