"""Human-readable and JSON rendering for the Fast Pytest Engine."""

from __future__ import annotations

import json

from autocorp_testing.schemas import EngineReport, TestPlan


def to_json(obj) -> str:
    return json.dumps(obj.to_dict(), indent=2)


def render_plan(plan: TestPlan) -> str:
    lines = [
        "Fast Pytest Engine — Test Plan",
        "==============================",
        "",
        f"Repository: {plan.repository}",
        f"Fingerprint: {plan.repository_fingerprint[:16]}...",
        f"Changed files: {len(plan.changed_files)}",
    ]
    for rel in plan.changed_files[:20]:
        lines.append(f"  - {rel}")
    lines += [
        "",
        f"Strict full command: {' '.join(plan.strict_full_command) if plan.strict_full_command else '(undetermined)'}",
        f"Strict full command confidence: {plan.strict_full_command_confidence}",
        f"Compile command: {' '.join(plan.compile_command)}",
        "",
        f"Proposed FAST tests ({len(plan.fast_tests)}):",
    ]
    for t in plan.fast_tests:
        lines.append(f"  - {t.node_id} [{t.category}]")
        for reason in t.reasons:
            lines.append(f"      reason: {reason}")
    lines += ["", f"Proposed FOCUSED tests ({len(plan.focused_tests)}):"]
    for t in plan.focused_tests:
        lines.append(f"  - {t.node_id} [{t.category}]")
    lines += [
        "",
        f"Deferred to FULL: {len(plan.deferred_to_full)} test file(s)",
        "",
        f"Estimated duration: {plan.estimated_duration_seconds:.2f}s (cached/estimated; not proof of a pass)",
        f"Confidence: {plan.confidence}",
        f"Parallelism: {'enabled' if plan.parallelism.get('enabled') else 'disabled'} — {plan.parallelism.get('reason')}",
        f"GPU safety: {len(plan.gpu_safety.get('gpu_tests_detected', []))} test(s) deferred",
        f"Database safety: {len(plan.database_safety.get('database_tests_detected', []))} test(s) require disposable data",
    ]
    if plan.uncertainty_warnings:
        lines += ["", "Uncertainty warnings:"]
        for w in plan.uncertainty_warnings:
            lines.append(f"  - {w}")
    lines += ["", "No tests were run. No changes were made."]
    return "\n".join(lines)


_HEADERS = {
    "fast": ("FAST CHECK", "Full production verification still required"),
    "focused": ("FOCUSED VERIFICATION", "Complete strict suite still required before final approval"),
    "full": ("FULL VERIFICATION", None),
}


def render_report(report: EngineReport) -> str:
    label, tagline = _HEADERS.get(report.mode, (report.mode.upper(), None))
    if report.blocked:
        status = "BLOCKED"
    elif any("NOT VERIFIED" in e for e in report.errors):
        status = "NOT VERIFIED"
    elif report.exit_code == 0:
        status = "PASSED"
    else:
        status = "FAILED"
    lines = [f"{label} {status}"]
    if tagline:
        lines.append(tagline)
    lines += [
        "",
        f"Repository: {report.repository}",
        f"Changed files: {len(report.changed_files)}",
        f"Selected tests: {len(report.selected_tests)}",
        f"Requested explicit paths: {len(report.requested_paths)}",
        f"Accepted explicit paths: {len(report.accepted_paths)}",
        f"Collected tests: {report.collected}",
        f"Passed: {report.passed}",
        f"Failed: {report.failed}",
        f"Skipped: {report.skipped}",
        f"Deselected: {report.deselected}",
        f"Duration: {report.duration_seconds:.1f} seconds",
        "",
    ]
    if report.requested_paths or report.accepted_paths or report.rejected_paths:
        lines.append("Preflight:")
        lines.append(f"- Intended selection mode: {report.selection_mode or '(unspecified)'}")
        if report.requested_paths:
            lines.append("- Requested explicit paths:")
            for p in report.requested_paths:
                lines.append(f"  - {p}")
        if report.normalized_paths:
            lines.append("- Normalized paths:")
            for p in report.normalized_paths:
                lines.append(f"  - {p}")
        if report.accepted_paths:
            lines.append("- Accepted paths:")
            for p in report.accepted_paths:
                lines.append(f"  - {p}")
        if report.duplicate_paths:
            lines.append("- Duplicate paths removed:")
            for p in report.duplicate_paths:
                lines.append(f"  - {p}")
        if report.rejected_paths:
            lines.append("- Rejected paths:")
            for p in report.rejected_paths:
                lines.append(f"  - {p}")
        command = report.commands[-1] if report.commands else []
        lines.append(f"- Final pytest command: {' '.join(command) if command else '(verification did not run)'}")
        lines.append("")
    if report.represented_test_files:
        lines.append("Actually represented test files:")
        for p in report.represented_test_files:
            lines.append(f"- {p}")
        lines.append("")
    if report.slowest_tests:
        lines.append("Slowest tests:")
        for item in report.slowest_tests[:10]:
            lines.append(f"- {item.get('duration_seconds', 0):.2f}s {item.get('phase')} {item.get('test')}")
        lines.append("")
    if report.performance_warnings:
        lines.append("Performance warnings:")
        for warning in report.performance_warnings:
            lines.append(f"- {warning}")
        lines.append("")
    if report.blocked:
        lines += [f"Blocked reason: {report.blocked_reason}", ""]
    reasons_seen = []
    for t in report.selected_tests:
        for r in t.reasons:
            tag = r.split(":")[0].split("(")[0].strip()
            if tag not in reasons_seen:
                reasons_seen.append(tag)
    if reasons_seen:
        lines.append("Selection evidence:")
        for r in reasons_seen[:8]:
            lines.append(f"- {r}")
        lines.append("")
    if report.deferred_checks:
        lines.append("Deferred:")
        for d in report.deferred_checks:
            lines.append(f"- {d}")
        lines.append("")
    if report.errors:
        lines.append("Errors:")
        for e in report.errors:
            lines.append(f"- {e}")
        lines.append("")
    if report.uncertainty_warnings:
        lines.append("Uncertainty warnings:")
        for w in report.uncertainty_warnings:
            lines.append(f"- {w}")
        lines.append("")
    approval = "GRANTED" if (report.mode == "full" and report.exit_code == 0 and not report.blocked) else "NOT GRANTED"
    lines.append(f"Production approval: {approval}")
    next_action = {"fast": "test-full", "focused": "test-full", "full": "(none — this was FULL)"}[report.mode]
    lines.append(f"Next required action: {next_action}")
    return "\n".join(lines)
