#!/usr/bin/env python3
"""
Acceptance -> Repair Adapter  (AutoCorp CLI - brains)  [Phase 8P]
================================================================

The keystone seam connecting the acceptance GATE's output to the repair
pipeline's input. The gate emits an AcceptanceReport (`accepted` + a `results`
list of dicts); the repair chain consumes an AcceptanceResult (`passed` +
`failures` strings). This adapter bridges the two and runs the existing, unchanged
AcceptanceBrain chain (plan_repairs -> to_fix_requests -> to_fixer_work_items) to
produce the FixerWorkItem objects that Phase 8O's run_cycle drives.

ADAPTER ONLY: it BUILDS the run_cycle inputs. It invokes no Fixer, runs no repair
loop, calls run_cycle nowhere, touches no orchestrator, and performs no execution,
retry, or rebuild. Wiring this into the live Session.run is a separate, later,
flag-guarded phase. Fully deterministic and offline.
"""

from brains.acceptance_brain import AcceptanceBrain, AcceptanceResult


class AcceptanceRepairAdapter:
    """Converts an AcceptanceReport into repair-pipeline inputs. Holds no state."""

    def __init__(self):
        self._brain = AcceptanceBrain()

    def to_acceptance_result(self, report) -> AcceptanceResult:
        """Map the gate's AcceptanceReport onto the brain's AcceptanceResult.

        `report.accepted` -> `passed`; only results whose status is "fail" become
        `failures` (their `criterion` text, in order). "pass" and "unverified"
        results are not repair work and are excluded - mirroring the gate, where
        unverified never blocks. A None report is a defensive pass with no
        failures."""
        if report is None:
            return AcceptanceResult(passed=True, failures=[])
        failures = [
            row.get("criterion")
            for row in (report.results or [])
            if row.get("status") == "fail"
        ]
        return AcceptanceResult(passed=report.accepted, failures=failures)

    def to_work_items(self, report, plan=None) -> list:
        """Convert an AcceptanceReport into FixerWorkItem objects by reusing the
        existing AcceptanceBrain chain. An accepted (or None/empty) report yields
        an empty list; a failed report yields one FixerWorkItem per failed
        criterion, order and description preserved verbatim.

        TARGET-AWARE (8V): each work item is paired with its originating failed
        row (same order) and gets a `target_path` resolved from the first non-empty
        file hint on that row (`file`, then `path`, then `filename`).

        PLAN-AWARE (DS10): `plan` is OPTIONAL (default None, so legacy callers are
        unchanged). When a failed row has NO direct hint AND a plan is supplied,
        `target_path` is resolved from the plan instead of staying None. When
        resolution fails completely, `target_path` stays None - the valid,
        backward-compatible fallback (GatedRepairFixer's repairs/repair_N.txt)."""
        result = self.to_acceptance_result(report)
        tasks = self._brain.plan_repairs(result)
        fix_requests = self._brain.to_fix_requests(tasks)
        work_items = self._brain.to_fixer_work_items(fix_requests)

        failed_rows = [
            row for row in (getattr(report, "results", None) or [])
            if row.get("status") == "fail"
        ]
        for item, row in zip(work_items, failed_rows):
            target = self._resolve_target_path(row)
            if target is None and plan is not None:
                target = self._resolve_from_plan(plan)
            item.target_path = target
        return work_items

    @staticmethod
    def _resolve_target_path(row):
        """Return the first non-empty file hint on a result row, else None."""
        for key in ("file", "path", "filename"):
            value = row.get(key)
            if value:
                return value
        return None

    @staticmethod
    def _resolve_from_plan(plan):
        """Resolve a target file from the plan: the first entry in
        `plan["build_order"]` whose path also appears in `plan["files"]`
        (build_order order wins; entries absent from files are skipped). Returns
        None when no build_order entry matches a planned file."""
        if not isinstance(plan, dict):
            return None
        file_paths = {
            f.get("path") for f in (plan.get("files") or [])
            if isinstance(f, dict) and f.get("path")
        }
        for name in (plan.get("build_order") or []):
            if name in file_paths:
                return name
        return None
