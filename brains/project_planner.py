#!/usr/bin/env python3
"""
Project Action Planner  (AutoCorp CLI - brains)  [Phase 1C]
==============================================================

A read-only planning brain that converts existing scanner (Phase 1A) and
analyzer (Phase 1B) evidence into a deterministic, prioritized project
action plan. Every action is supported by collected evidence - nothing
is guessed, hardcoded, or fabricated.

READ-ONLY: only calls the existing scanner and analyzer APIs (which
themselves only open files for reading and run non-mutating git
plumbing). Never writes, never calls a model.

Public API:
    run_project_plan(repo_path: str) -> ProjectPlan

Ordering:
    1. priority  (critical > high > medium > low)
    2. category
    3. title
    4. action_id  (deterministic SHA-256 hex, no randomness)

ID generation:
    action_id = sha256("priority:category:title".encode())[:12]
    Guaranteed deterministic across runs; no UUIDs, timestamps, or
    global counters.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from brains import scanner, analyzer

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _action_id(priority: str, category: str, title: str) -> str:
    return hashlib.sha256(
        f"{priority}:{category}:{title}".encode()
    ).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# Result types (immutable / safely structured)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProjectAction:
    action_id: str
    priority: str
    category: str
    title: str
    reason: str
    evidence: tuple[str, ...] = ()
    recommended_next_step: str = ""
    affected_paths: tuple[str, ...] = ()
    safe_to_automate: bool = False
    confidence: int = 0


@dataclass(frozen=True)
class ProjectPlan:
    repo_path: str
    project_type: str
    overall_health: str
    summary: str
    actions: tuple[ProjectAction, ...]
    blockers: tuple[str, ...]
    confidence: int


# --------------------------------------------------------------------------- #
# Rule-based action builders
# --------------------------------------------------------------------------- #
def _build_dirty_tree_action(scan: scanner.ScanResult) -> ProjectAction:
    title = "Review working tree changes before beginning repairs"
    return ProjectAction(
        action_id=_action_id("high", "repository", title),
        priority="high",
        category="repository",
        title=title,
        reason="The working tree contains uncommitted changes. Repairs should "
               "not begin until existing changes are reviewed, committed, or "
               "stashed.",
        evidence=("git status reported a dirty working tree",),
        recommended_next_step="Run 'git status' and review any uncommitted "
                              "changes before proceeding.",
        safe_to_automate=False,
        confidence=95,
    )


def _build_missing_tests_action() -> ProjectAction:
    title = "Establish a test framework"
    return ProjectAction(
        action_id=_action_id("high", "testing", title),
        priority="high",
        category="testing",
        title=title,
        reason="No test framework was detected. Without tests, there is no "
               "safety net for verifying that changes do not introduce "
               "regressions.",
        evidence=("No test files found in the repository",),
        recommended_next_step="Choose a test framework (e.g. pytest) and add "
                              "at least one test file.",
        safe_to_automate=False,
        confidence=85,
    )


def _build_missing_entry_points_action() -> ProjectAction:
    title = "Identify or create a project entry point"
    return ProjectAction(
        action_id=_action_id("high", "architecture", title),
        priority="high",
        category="architecture",
        title=title,
        reason="No entry-point file was detected. An entry point makes the "
               "project discoverable and runnable without guesswork.",
        evidence=("No entry-point files found at the repository root",),
        recommended_next_step="Add a runnable entry point (e.g. main.py, "
                              "app.py, or a console_scripts entry in "
                              "pyproject.toml).",
        safe_to_automate=False,
        confidence=90,
    )


def _build_not_implemented_action(count: int) -> ProjectAction:
    title = "Review incomplete implementations"
    return ProjectAction(
        action_id=_action_id("medium", "incomplete-code", title),
        priority="medium",
        category="incomplete-code",
        title=title,
        reason=f"{count} source file(s) contain NotImplementedError "
               "references. Each indicates a code path that is intentionally "
               "unfinished. These should be investigated in context - "
               "NotImplementedError is a marker, not a guarantee of a defect.",
        evidence=(f"{count} NotImplementedError occurrences found in the "
                   "repository",),
        recommended_next_step="Open each file containing NotImplementedError "
                              "and evaluate whether the feature is still "
                              "needed.",
        safe_to_automate=False,
        confidence=90,
    )


def _build_pass_statements_action(count: int) -> ProjectAction:
    title = "Review bare pass statements"
    return ProjectAction(
        action_id=_action_id("low", "maintainability", title),
        priority="low",
        category="maintainability",
        title=title,
        reason=f"{count} bare pass statement(s) found. Some may be "
               "intentional placeholders (e.g. abstract method stubs or "
               "empty exception handlers), while others may indicate "
               "unfinished logic. Each should be reviewed in context.",
        evidence=(f"{count} bare pass statement(s) detected",),
        recommended_next_step="Review each pass statement to confirm it is "
                              "intentional.",
        safe_to_automate=False,
        confidence=75,
    )


def _build_todo_action(count: int) -> ProjectAction:
    title = "Review outstanding TODO markers"
    return ProjectAction(
        action_id=_action_id("medium", "maintainability", title),
        priority="medium",
        category="maintainability",
        title=title,
        reason=f"{count} TODO marker(s) found. These are noted improvements "
               "or deferred work items, not confirmed defects. Each should "
               "be triaged to determine whether it is still relevant.",
        evidence=(f"{count} TODO marker(s) found in source files",),
        recommended_next_step="Triage each TODO to determine whether it "
                              "should be actioned, deferred, or removed.",
        safe_to_automate=False,
        confidence=80,
    )


def _build_fixme_action(count: int) -> ProjectAction:
    title = "Address FIXME markers"
    return ProjectAction(
        action_id=_action_id("high", "maintainability", title),
        priority="high",
        category="maintainability",
        title=title,
        reason=f"{count} FIXME marker(s) found. Unlike TODOs, FIXMEs signal "
               "known issues that need correcting. Each should be "
               "investigated.",
        evidence=(f"{count} FIXME marker(s) found in source files",),
        recommended_next_step="Open each file containing FIXME markers and "
                              "investigate the referenced issue.",
        safe_to_automate=False,
        confidence=85,
    )


def _build_missing_deps_action() -> ProjectAction:
    title = "Add dependency management files"
    return ProjectAction(
        action_id=_action_id("high", "dependencies", title),
        priority="high",
        category="dependencies",
        title=title,
        reason="No dependency file was found. Without one, it is unclear "
               "which packages this project requires.",
        evidence=("No dependency files found at the repository root",),
        recommended_next_step="Add a requirements.txt, pyproject.toml, or "
                              "equivalent dependency manifest.",
        safe_to_automate=False,
        confidence=85,
    )


def _build_health_review_action(health: str) -> ProjectAction:
    title = "Perform a maintainability review"
    return ProjectAction(
        action_id=_action_id("medium", "architecture", title),
        priority="medium",
        category="architecture",
        title=title,
        reason=f"Overall health is '{health}', which suggests the repository "
               "may benefit from targeted maintainability work.",
        evidence=(f"Analyzer overall health: {health}",),
        recommended_next_step="Prioritize the highest-impact issues from the "
                              "action plan and address them in priority order.",
        safe_to_automate=False,
        confidence=75,
    )


def _build_healthy_verification_action() -> ProjectAction:
    title = "Project appears healthy — verify and monitor"
    return ProjectAction(
        action_id=_action_id("low", "repository", title),
        priority="low",
        category="repository",
        title=title,
        reason="No material findings were detected. A verification pass "
               "confirms the analysis was thorough and nothing was missed.",
        evidence=("No significant findings from scanner or analyzer data",),
        recommended_next_step="Spot-check a few source files to confirm "
                              "quality, then monitor over time.",
        safe_to_automate=True,
        confidence=70,
    )


# --------------------------------------------------------------------------- #
# Action collection and ordering
# --------------------------------------------------------------------------- #
def _plan_actions(scan: scanner.ScanResult,
                  analysis: analyzer.ProjectAnalysis) -> list[ProjectAction]:
    """Collects all evidence-supported actions. Order is not applied here."""
    actions: list[ProjectAction] = []

    if scan.working_tree == "dirty":
        actions.append(_build_dirty_tree_action(scan))

    if analysis.test_framework == "unknown":
        actions.append(_build_missing_tests_action())

    if not analysis.entry_points:
        actions.append(_build_missing_entry_points_action())

    if scan.not_implemented_count > 0:
        actions.append(_build_not_implemented_action(scan.not_implemented_count))

    if scan.pass_count > 0:
        actions.append(_build_pass_statements_action(scan.pass_count))

    if scan.todo_count > 0:
        actions.append(_build_todo_action(scan.todo_count))

    if scan.fixme_count > 0:
        actions.append(_build_fixme_action(scan.fixme_count))

    if not analysis.dependency_files:
        actions.append(_build_missing_deps_action())

    if analysis.overall_health in ("Fair", "Needs Attention"):
        actions.append(_build_health_review_action(analysis.overall_health))

    if not actions:
        actions.append(_build_healthy_verification_action())

    return actions


def _order_actions(actions: list[ProjectAction]) -> list[ProjectAction]:
    """Deterministic ordering: priority > category > title > action_id."""
    return sorted(
        actions,
        key=lambda a: (
            _PRIORITY_ORDER.get(a.priority, 99),
            a.category,
            a.title,
            a.action_id,
        ),
    )


def _build_summary(actions: list[ProjectAction]) -> str:
    if not actions:
        return "No material findings. The project appears healthy."
    critical = sum(1 for a in actions if a.priority == "critical")
    high = sum(1 for a in actions if a.priority == "high")
    medium = sum(1 for a in actions if a.priority == "medium")
    low = sum(1 for a in actions if a.priority == "low")
    parts = []
    for count, label in ((critical, "critical"), (high, "high-priority"),
                          (medium, "medium-priority"), (low, "low-priority")):
        if count:
            parts.append(f"{count} {label}")
    return f"Found {', '.join(parts)} action(s) based on repository evidence."


def _build_blockers(actions: list[ProjectAction],
                    scan: scanner.ScanResult) -> tuple[str, ...]:
    blockers: list[str] = []
    if scan.working_tree == "dirty":
        blockers.append(
            "Working tree is dirty — review and commit or stash "
            "uncommitted changes before beginning any repair work."
        )
    if not actions:
        blockers.append("No actions generated — nothing blocks progress.")
    return tuple(blockers)


def _plan_confidence(actions: list[ProjectAction],
                     analysis: analyzer.ProjectAnalysis) -> int:
    if not actions:
        return 50
    avg = sum(a.confidence for a in actions) / len(actions)
    return round(min(98, max(50, avg)))


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def run_project_plan(repo_path: str) -> ProjectPlan:
    """Collect scanner + analyzer evidence and produce a deterministic,
    evidence-based project action plan. Read-only throughout."""
    repo_path = __import__("os").path.abspath(repo_path)
    scan = scanner.run_scan(repo_path)
    analysis = analyzer.run_analysis(repo_path)

    raw_actions = _plan_actions(scan, analysis)
    ordered = _order_actions(raw_actions)
    summary = _build_summary(raw_actions)
    blockers = _build_blockers(raw_actions, scan)
    confidence = _plan_confidence(raw_actions, analysis)

    return ProjectPlan(
        repo_path=repo_path,
        project_type=analysis.project_type,
        overall_health=analysis.overall_health,
        summary=summary,
        actions=tuple(ordered),
        blockers=blockers,
        confidence=confidence,
    )
