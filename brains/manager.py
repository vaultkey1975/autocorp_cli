#!/usr/bin/env python3
"""Autonomous Engineering Manager for AutoCorp CLI.

The manager is a read-only coordinator over existing AutoCorp evidence
sources. It does not scan, analyze, plan, test, repair, or chat by itself;
it calls the existing modules that already own those responsibilities and
turns their outputs into one engineering decision record.
"""

from __future__ import annotations

import datetime as _dt
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field

from brains import analyzer, engine_registry, live_readiness, project_planner, scanner


_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class ManagerScore:
    name: str
    score: int
    explanation: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagerTask:
    priority: str
    title: str
    reason: str
    evidence: tuple[str, ...] = ()
    next_step: str = ""
    category: str = ""
    action_id: str = ""
    recommended_ai: str = "Codex"
    ai_reason: str = ""
    local_model_safe: bool = False
    use_reliability_engine: str = "No"
    use_disposable_mode: bool = False
    review_before_merge: bool = True
    commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagerRoadmap:
    critical: tuple[ManagerTask, ...] = ()
    high: tuple[ManagerTask, ...] = ()
    medium: tuple[ManagerTask, ...] = ()
    low: tuple[ManagerTask, ...] = ()
    completed: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    waiting_on_owner: tuple[str, ...] = ()
    future_ideas: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagerReport:
    repo_path: str
    generated_at: str
    scan: scanner.ScanResult
    analysis: analyzer.ProjectAnalysis
    plan: project_planner.ProjectPlan
    readiness: live_readiness.LiveReadinessReport | None
    readiness_error: str = ""
    git_recent: tuple[str, ...] = ()
    git_status: str = ""
    current_phase: str = "Unable to determine from repository evidence."
    healthy: tuple[str, ...] = ()
    broken: tuple[str, ...] = ()
    highest_risk_code: tuple[str, ...] = ()
    scores: tuple[ManagerScore, ...] = ()
    roadmap: ManagerRoadmap = field(default_factory=ManagerRoadmap)
    next_task: ManagerTask | None = None
    production_commands: tuple[str, ...] = ()
    available_engines: tuple[str, ...] = ()
    reliability_engine_status: str = "Unable to determine from repository evidence."


def run_manager(repo_path: str, autocorp_root: str | None = None) -> ManagerReport:
    """Build a read-only engineering-management report for `repo_path`."""
    repo_path = os.path.abspath(repo_path)
    autocorp_root = os.path.abspath(autocorp_root or os.path.dirname(os.path.dirname(__file__)))

    scan = scanner.run_scan(repo_path)
    analysis = analyzer.run_analysis(repo_path)
    plan = project_planner.run_project_plan(repo_path)
    readiness, readiness_error = _readiness(repo_path)
    git_recent = tuple(_git_lines(repo_path, ["log", "--oneline", "--max-count=5"]))
    git_status = "\n".join(_git_lines(repo_path, ["status", "--short", "--branch"]))
    current_phase = _current_phase(repo_path)
    healthy = _healthy(scan, analysis, plan, readiness)
    broken = _broken(plan, readiness, readiness_error)
    highest_risk = _highest_risk_code(analysis)
    reliability_status = _reliability_status(autocorp_root)

    tasks = _tasks(repo_path, plan, readiness, reliability_status)
    roadmap = _roadmap(tasks, plan, readiness, current_phase, reliability_status)
    scores = _scores(scan, analysis, plan, readiness, readiness_error, current_phase)
    next_task = _next_task(tasks)

    repo_q = shlex.quote(repo_path)
    python = shlex.quote(sys.executable)
    production_commands = (
        f"{python} autocorp.py live-readiness --repo {repo_q}",
        f"{python} autocorp.py workflow-test --repo {repo_q} --disposable",
        f"{python} autocorp.py publish-test --repo {repo_q} --disposable",
    )

    return ManagerReport(
        repo_path=repo_path,
        generated_at=_dt.datetime.now().replace(microsecond=0).isoformat(),
        scan=scan,
        analysis=analysis,
        plan=plan,
        readiness=readiness,
        readiness_error=readiness_error,
        git_recent=git_recent,
        git_status=git_status,
        current_phase=current_phase,
        healthy=healthy,
        broken=broken,
        highest_risk_code=highest_risk,
        scores=scores,
        roadmap=roadmap,
        next_task=next_task,
        production_commands=production_commands,
        available_engines=tuple(engine_registry.available_engines()),
        reliability_engine_status=reliability_status,
    )


def render_summary(report: ManagerReport) -> str:
    lines = [
        "Autonomous Engineering Manager",
        "==============================",
        "",
        f"Repository: {report.repo_path}",
        f"Generated: {report.generated_at}",
        f"Branch: {report.scan.branch}",
        f"Working Tree: {report.scan.working_tree}",
        f"Project Type: {report.analysis.project_type}",
        f"Overall Health: {report.analysis.overall_health}",
        "",
        "Current Repository Phase",
        "------------------------",
        report.current_phase,
        "",
        "What Is Healthy",
        "---------------",
    ]
    lines.extend(_bullet(report.healthy))
    lines.extend(["", "What Is Broken", "--------------"])
    lines.extend(_bullet(report.broken))
    lines.extend(["", "Recent Changes", "--------------"])
    lines.extend(_bullet(report.git_recent or ("Unable to determine from repository evidence.",)))
    lines.extend(["", "Highest-Risk Code", "-----------------"])
    lines.extend(_bullet(report.highest_risk_code))
    lines.extend(["", "Recommended Next Task", "---------------------"])
    lines.extend(_task_lines(report.next_task))
    lines.extend(["", "Production Readiness", "--------------------"])
    lines.extend(_score_lines(report.scores))
    return "\n".join(lines)


def render_roadmap(report: ManagerReport) -> str:
    sections = (
        ("Critical", report.roadmap.critical),
        ("High", report.roadmap.high),
        ("Medium", report.roadmap.medium),
        ("Low", report.roadmap.low),
    )
    lines = ["Live Engineering Roadmap", "========================", ""]
    for title, tasks in sections:
        lines.extend([title, "-" * len(title)])
        if tasks:
            for task in tasks:
                lines.extend(_task_lines(task, compact=True))
        else:
            lines.append("- (none)")
        lines.append("")

    for title, items in (
        ("Completed", report.roadmap.completed),
        ("Blocked", report.roadmap.blocked),
        ("Waiting on Owner", report.roadmap.waiting_on_owner),
        ("Future Ideas", report.roadmap.future_ideas),
    ):
        lines.extend([title, "-" * len(title)])
        lines.extend(_bullet(items))
        lines.append("")
    return "\n".join(lines).rstrip()


def render_next_task(report: ManagerReport) -> str:
    lines = ["Recommended Next Task", "=====================", ""]
    lines.extend(_task_lines(report.next_task))
    return "\n".join(lines)


def render_production(report: ManagerReport) -> str:
    lines = ["Production Readiness", "====================", ""]
    lines.extend(_score_lines(report.scores))
    lines.extend(["", "Release Estimate", "----------------"])
    estimate = _release_estimate(report.scores)
    lines.append(estimate)
    lines.extend(["", "Required Verification Commands", "------------------------------"])
    lines.extend(_bullet(report.production_commands))
    if report.readiness_error:
        lines.extend(["", "Readiness Scanner Error", "-----------------------", report.readiness_error])
    return "\n".join(lines)


def _readiness(repo_path: str):
    try:
        return live_readiness.run_live_readiness(repo_path), ""
    except Exception as exc:
        return None, str(exc) or exc.__class__.__name__


def _git_lines(repo_path: str, args: list[str]) -> list[str]:
    try:
        proc = subprocess.run(["git", *args], cwd=repo_path, text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.strip().splitlines() if line.strip()]


def _current_phase(repo_path: str) -> str:
    candidates = [
        os.path.join(repo_path, "AI_ENGINEERING", "CURRENT_PHASE.md"),
        os.path.join(repo_path, "CURRENT_PHASE.md"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            text = _read_limited(path, 1600).strip()
            if text:
                return text
    return "Unable to determine from repository evidence."


def _read_limited(path: str, limit: int) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _healthy(scan_result, analysis_result, plan_result, readiness_report) -> tuple[str, ...]:
    items = []
    if scan_result.working_tree == "clean":
        items.append("Git working tree is clean.")
    if analysis_result.test_framework != "unknown":
        items.append(f"Test framework detected: {analysis_result.test_framework}.")
    if analysis_result.entry_points:
        items.append(f"Entry point evidence: {', '.join(analysis_result.entry_points[:3])}.")
    if analysis_result.dependency_files:
        items.append(f"Dependency metadata present: {', '.join(analysis_result.dependency_files)}.")
    if plan_result.actions and all(a.priority in {"low", "medium"} for a in plan_result.actions):
        items.append("Project planner found no critical or high-priority actions.")
    if readiness_report and readiness_report.overall_status in {"pass", "ready"}:
        items.append(f"Live readiness scanner status: {readiness_report.overall_status}.")
    return tuple(items or ("Unable to determine healthy areas from repository evidence.",))


def _broken(plan_result, readiness_report, readiness_error: str) -> tuple[str, ...]:
    items = list(plan_result.blockers)
    items.extend(f"[{a.priority}] {a.title}: {a.reason}" for a in plan_result.actions if a.priority in {"critical", "high"})
    if readiness_report:
        items.extend(f"[{c.status}] {c.title}: {c.reason}" for c in readiness_report.checks if c.status in {"fail", "blocked"})
        items.extend(readiness_report.blockers)
    if readiness_error:
        items.append(f"Live readiness scanner failed: {readiness_error}")
    return tuple(items or ("No critical/high blockers found by scanner, analyzer, planner, or live readiness.",))


def _highest_risk_code(analysis_result) -> tuple[str, ...]:
    items = []
    if analysis_result.largest_module:
        items.append(
            f"{analysis_result.largest_module} ({analysis_result.largest_module_lines} lines) - largest module by analyzer evidence."
        )
    if analysis_result.largest_package:
        items.append(
            f"{analysis_result.largest_package} ({analysis_result.largest_package_lines} lines) - largest package by analyzer evidence."
        )
    for stat in analysis_result.top_directories[:3]:
        items.append(f"{stat.name}/ ({stat.python_files} Python files, {stat.python_lines} lines)")
    return tuple(items or ("Unable to determine from repository evidence.",))


def _reliability_status(autocorp_root: str) -> str:
    src = os.path.isdir(os.path.join(autocorp_root, "reliability_engine"))
    tests = os.path.isfile(os.path.join(autocorp_root, "tests", "test_reliability_engine.py"))
    if src and tests:
        return "Available as committed source with tests; no dedicated CLI command is evidenced."
    if src:
        return "Source present, but test evidence is missing."
    return "Unable to determine from repository evidence."


def _tasks(repo_path: str, plan_result, readiness_report, reliability_status: str) -> tuple[ManagerTask, ...]:
    tasks: list[ManagerTask] = []
    for action in plan_result.actions:
        tasks.append(_task_from_action(repo_path, action, reliability_status))
    if readiness_report:
        for check in readiness_report.checks:
            if check.status in {"fail", "blocked", "warning"}:
                priority = "critical" if check.status in {"fail", "blocked"} else "medium"
                tasks.append(ManagerTask(
                    priority=priority,
                    category=f"readiness:{check.category}",
                    title=check.title,
                    reason=check.reason,
                    evidence=check.evidence or (f"live-readiness status: {check.status}",),
                    next_step="Run live-readiness and inspect the affected paths before changing code.",
                    recommended_ai="Codex",
                    ai_reason="Readiness findings require repository edits plus verification.",
                    local_model_safe=False,
                    use_disposable_mode=check.category in {"workflow", "external_services", "production_blockers"},
                    review_before_merge=True,
                    commands=(f"{shlex.quote(sys.executable)} autocorp.py live-readiness --repo {shlex.quote(repo_path)}",),
                ))
    return tuple(sorted(tasks, key=lambda t: (_PRIORITY_ORDER.get(t.priority, 99), t.category, t.title)))


def _task_from_action(repo_path: str, action, reliability_status: str) -> ManagerTask:
    ai, reason, local_safe = _recommend_ai(action)
    reliability = _recommend_reliability(action, reliability_status)
    disposable = action.category in {"workflow", "production", "external_services"}
    review = action.priority in {"critical", "high"} or not action.safe_to_automate
    commands = []
    if action.action_id:
        commands.append(
            f"{shlex.quote(sys.executable)} autocorp.py repair --repo {shlex.quote(repo_path)} --action {shlex.quote(action.action_id)} --dry-run"
        )
        commands.append(
            f"{shlex.quote(sys.executable)} autocorp.py propose-repair --repo {shlex.quote(repo_path)} --action {shlex.quote(action.action_id)} --provider local"
        )
    return ManagerTask(
        priority=action.priority,
        title=action.title,
        reason=action.reason,
        evidence=action.evidence,
        next_step=action.recommended_next_step,
        category=action.category,
        action_id=action.action_id,
        recommended_ai=ai,
        ai_reason=reason,
        local_model_safe=local_safe,
        use_reliability_engine=reliability,
        use_disposable_mode=disposable,
        review_before_merge=review,
        commands=tuple(commands),
    )


def _recommend_ai(action) -> tuple[str, str, bool]:
    if action.safe_to_automate and action.priority == "low":
        return ("Local model", "The planner marks the action safe to automate and low priority.", True)
    if action.category in {"repository", "testing", "dependencies"}:
        return ("Codex", "The task needs repository-aware edits and local verification.", False)
    if action.category in {"architecture", "maintainability"} and action.priority in {"critical", "high"}:
        return ("Claude", "The task needs broad review before implementation.", False)
    if action.category in {"incomplete-code", "maintainability"}:
        return ("Codex", "The task needs source inspection, focused edits, and regression tests.", False)
    return ("Codex", "Codex is the default for local code changes with verification.", False)


def _recommend_reliability(action, reliability_status: str) -> str:
    if "no dedicated CLI" in reliability_status.casefold():
        if action.category in {"incomplete-code", "testing", "maintainability"}:
            return "Candidate for surgical edit workflows after owner authorizes Reliability Engine CLI/product integration."
        return "No - no dedicated CLI integration is evidenced."
    return "Unable to determine from repository evidence."


def _roadmap(tasks, plan_result, readiness_report, current_phase: str, reliability_status: str) -> ManagerRoadmap:
    grouped = {"critical": [], "high": [], "medium": [], "low": []}
    for task in tasks:
        grouped.setdefault(task.priority, []).append(task)
    completed = []
    if plan_result.actions:
        completed.append("Scanner, analyzer, and project planner produced repository evidence.")
    if readiness_report:
        completed.append("Live readiness static inspection completed.")
    blocked = list(plan_result.blockers)
    if readiness_report:
        blocked.extend(readiness_report.blockers)
    waiting = []
    if "owner" in current_phase.casefold():
        waiting.append("Current phase documentation contains owner-gated completion authority.")
    if "no dedicated CLI" in reliability_status.casefold():
        waiting.append("Reliability Engine dedicated CLI/product integration remains owner-defined.")
    future = []
    if current_phase == "Unable to determine from repository evidence.":
        future.append("Create repository engineering documentation before claiming a phase roadmap.")
    else:
        future.append("Future phases beyond repository-documented work require owner planning.")
    return ManagerRoadmap(
        critical=tuple(grouped.get("critical", ())),
        high=tuple(grouped.get("high", ())),
        medium=tuple(grouped.get("medium", ())),
        low=tuple(grouped.get("low", ())),
        completed=tuple(completed or ("No completed manager checks beyond data collection.",)),
        blocked=tuple(dict.fromkeys(blocked)) or ("No blockers found by manager evidence.",),
        waiting_on_owner=tuple(dict.fromkeys(waiting)) or ("No owner-waiting item found by manager evidence.",),
        future_ideas=tuple(future),
    )


def _next_task(tasks: tuple[ManagerTask, ...]) -> ManagerTask | None:
    return tasks[0] if tasks else None


def _scores(scan_result, analysis_result, plan_result, readiness_report, readiness_error: str, current_phase: str):
    return (
        _repository_score(scan_result, plan_result),
        _testing_score(analysis_result),
        _safety_score(scan_result, plan_result, readiness_report),
        _documentation_score(current_phase),
        _architecture_score(analysis_result),
        _production_score(readiness_report, readiness_error),
    )


def _deduct(base: int, deductions: list[tuple[int, str]]) -> tuple[int, str]:
    score = max(0, base - sum(value for value, _ in deductions))
    if deductions:
        reason = "; ".join(f"-{value} {why}" for value, why in deductions)
    else:
        reason = "No deductions from repository evidence."
    return score, reason


def _repository_score(scan_result, plan_result) -> ManagerScore:
    deductions = []
    if scan_result.working_tree == "dirty":
        deductions.append((30, "working tree is dirty"))
    high = sum(1 for a in plan_result.actions if a.priority == "high")
    critical = sum(1 for a in plan_result.actions if a.priority == "critical")
    if critical:
        deductions.append((40, f"{critical} critical planner action(s)"))
    if high:
        deductions.append((20, f"{high} high-priority planner action(s)"))
    score, reason = _deduct(100, deductions)
    return ManagerScore("Repository Health", score, reason, (f"Working tree: {scan_result.working_tree}", plan_result.summary))


def _testing_score(analysis_result) -> ManagerScore:
    deductions = []
    if analysis_result.test_framework == "unknown":
        deductions.append((50, "no test framework detected"))
    if analysis_result.python_file_count and analysis_result.average_file_size > 450:
        deductions.append((10, "large average Python module size increases regression risk"))
    score, reason = _deduct(100, deductions)
    return ManagerScore("Testing", score, reason, (f"Test framework: {analysis_result.test_framework}",))


def _safety_score(scan_result, plan_result, readiness_report) -> ManagerScore:
    deductions = []
    if scan_result.working_tree == "dirty":
        deductions.append((25, "dirty working tree prevents safe automated repair"))
    unsafe = sum(1 for a in plan_result.actions if not a.safe_to_automate and a.priority in {"critical", "high"})
    if unsafe:
        deductions.append((20, f"{unsafe} high-priority action(s) are not safe to automate"))
    if readiness_report:
        blocked = sum(1 for c in readiness_report.checks if c.status == "blocked")
        if blocked:
            deductions.append((25, f"{blocked} blocked readiness check(s)"))
    score, reason = _deduct(100, deductions)
    return ManagerScore("Safety", score, reason, (f"Working tree: {scan_result.working_tree}",))


def _documentation_score(current_phase: str) -> ManagerScore:
    if current_phase == "Unable to determine from repository evidence.":
        return ManagerScore("Documentation", 45, "-55 no repository phase documentation found", (current_phase,))
    return ManagerScore("Documentation", 90, "-10 phase status still requires repository-owner interpretation", ("CURRENT_PHASE.md found",))


def _architecture_score(analysis_result) -> ManagerScore:
    deductions = []
    if not analysis_result.entry_points:
        deductions.append((25, "no entry point detected"))
    if not analysis_result.dependency_files:
        deductions.append((20, "no dependency metadata detected"))
    if analysis_result.overall_health in {"Fair", "Needs Attention"}:
        deductions.append((20, f"analyzer health is {analysis_result.overall_health}"))
    score, reason = _deduct(100, deductions)
    return ManagerScore("Architecture", score, reason, (f"Project type: {analysis_result.project_type}",))


def _production_score(readiness_report, readiness_error: str) -> ManagerScore:
    if readiness_error:
        return ManagerScore("Production", 35, f"-65 live readiness scanner failed: {readiness_error}", (readiness_error,))
    if not readiness_report:
        return ManagerScore("Production", 35, "-65 live readiness scanner did not produce evidence", ())
    deductions = []
    fail = sum(1 for c in readiness_report.checks if c.status == "fail")
    blocked = sum(1 for c in readiness_report.checks if c.status == "blocked")
    warning = sum(1 for c in readiness_report.checks if c.status == "warning")
    if fail:
        deductions.append((35, f"{fail} failing readiness check(s)"))
    if blocked:
        deductions.append((30, f"{blocked} blocked readiness check(s)"))
    if warning:
        deductions.append((10, f"{warning} warning readiness check(s)"))
    score, reason = _deduct(100, deductions)
    return ManagerScore("Production", score, reason, (f"Live readiness status: {readiness_report.overall_status}",))


def _release_estimate(scores: tuple[ManagerScore, ...]) -> str:
    low = min(s.score for s in scores) if scores else 0
    avg = round(sum(s.score for s in scores) / len(scores)) if scores else 0
    if low < 50:
        label = "Not ready for release"
    elif low < 75 or avg < 80:
        label = "Conditionally ready after blockers are resolved"
    else:
        label = "Locally ready subject to owner approval and production validation"
    return f"{label}. Lowest score: {low}; average score: {avg}. See score explanations above."


def _bullet(items) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- (none)"]


def _task_lines(task: ManagerTask | None, compact: bool = False) -> list[str]:
    if task is None:
        return ["- Unable to determine from repository evidence."]
    prefix = f"- [{task.priority.upper()}] {task.title}" if compact else f"Priority: {task.priority.upper()}\nTitle: {task.title}"
    lines = [prefix]
    fields = [
        ("Category", task.category),
        ("Reason", task.reason),
        ("Next Step", task.next_step),
        ("Recommended AI", f"{task.recommended_ai} - {task.ai_reason}"),
        ("Local Model Safe", "Yes" if task.local_model_safe else "No"),
        ("Use Reliability Engine", task.use_reliability_engine),
        ("Use Disposable Mode", "Yes" if task.use_disposable_mode else "No"),
        ("Review Before Merge", "Yes" if task.review_before_merge else "No"),
    ]
    for label, value in fields:
        if value:
            lines.append(f"  {label}: {value}" if compact else f"{label}: {value}")
    if task.evidence:
        lines.append("  Evidence:" if compact else "Evidence:")
        lines.extend((f"  - {ev}" if compact else f"- {ev}") for ev in task.evidence)
    if task.commands:
        lines.append("  Commands:" if compact else "Commands:")
        lines.extend((f"  - {cmd}" if compact else f"- {cmd}") for cmd in task.commands)
    return lines


def _score_lines(scores: tuple[ManagerScore, ...]) -> list[str]:
    lines = []
    for score in scores:
        lines.append(f"{score.name}: {score.score}/100")
        lines.append(f"  Why: {score.explanation}")
        for ev in score.evidence:
            lines.append(f"  Evidence: {ev}")
    lines.append(f"Estimated Release Readiness: {_release_estimate(scores)}")
    return lines
