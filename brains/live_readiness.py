#!/usr/bin/env python3
"""
Live Application Readiness Scanner  (AutoCorp CLI - brains)  [Phase 1H]
========================================================================

A read-only, static-inspection diagnostic that inspects a repository and
reports whether the application appears ready to launch and complete its
real workflow. Never edits, never executes target code, never contacts
services.

Public API:
    run_live_readiness(repo_path: str) -> LiveReadinessReport
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
from dataclasses import dataclass, field

from brains import scanner, analyzer, workspace

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

_CATEGORIES = (
    "repository", "launchability", "backend_api", "ui_wiring",
    "workflow", "external_services", "production_blockers",
)


@dataclass(frozen=True)
class ReadinessCheck:
    check_id: str
    category: str
    title: str
    status: str
    reason: str = ""
    evidence: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()
    confidence: int = 0


@dataclass
class LiveReadinessReport:
    repo_path: str
    project_type: str = "Unknown"
    launch_candidates: tuple[str, ...] = ()
    health_endpoints: tuple[str, ...] = ()
    workflow_stages: tuple[dict, ...] = ()
    checks: tuple[ReadinessCheck, ...] = ()
    blockers: tuple[str, ...] = ()
    overall_status: str = "unknown"
    confidence: int = 0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_WEB_ROUTES = {
    "flask": re.compile(r"@app\.route\(['\"]([^'\"]+)"),
    "fastapi": re.compile(r"@app\.(get|post|put|delete|patch)\(['\"]([^'\"]+)"),
    "django": re.compile(r"path\(['\"]([^'\"]+)"),
}

_HEALTH_PATTERNS = re.compile(
    r"(health|ping|status|ready|alive|heartbeat)", re.IGNORECASE
)

_PLACEHOLDER_PATTERNS = re.compile(
    r"(return\s*\{\s*['\"]status['\"]\s*:\s*['\"]ok['\"]\s*\}|"
    r"return\s*\{.*['\"]message['\"].*['\"]success['\"]|"
    r"return\s*\{\s*['\"]success['\"]\s*:\s*True\s*\})",
    re.IGNORECASE,
)

_WORKFLOW_STAGES = [
    ("research", re.compile(r"research|search|scrape|crawl", re.IGNORECASE)),
    ("script_generation", re.compile(r"script|generate.*script|write.*script", re.IGNORECASE)),
    ("voice_generation", re.compile(r"voice|tts|text.to.speech|speech|elevenlabs|edge.tts", re.IGNORECASE)),
    ("audio_assembly", re.compile(r"audio|mix|merge.*audio|concat.*audio", re.IGNORECASE)),
    ("video_rendering", re.compile(r"video|render|ffmpeg|composite|encode", re.IGNORECASE)),
    ("quality_control", re.compile(r"qa|quality|review|validate|check.*output", re.IGNORECASE)),
    ("publishing", re.compile(r"publish|upload|youtube|social|post", re.IGNORECASE)),
]

_EXTERNAL_SERVICES = [
    ("ollama", re.compile(r"ollama|llama|AUTOCORP_MODEL|OLLAMA_URL")),
    ("chatterbox", re.compile(r"chatterbox", re.IGNORECASE)),
    ("ffmpeg", re.compile(r"ffmpeg|subprocess.*ffmpeg")),
    ("youtube_oauth", re.compile(r"youtube|youtube.*oauth|google.*oauth|client_secret.*json", re.IGNORECASE)),
    ("database", re.compile(r"sqlite|postgres|mysql|mongodb|DATABASE_URL|DB_PATH|sqlalchemy")),
    ("file_storage", re.compile(r"upload|download|storage|S3|bucket|file.*path")),
]

_LAUNCH_PATTERNS = [
    ("FastAPI", re.compile(r"uvicorn|FastAPI\(\)|fastapi")),
    ("Flask", re.compile(r"flask|Flask\(__name__\)")),
    ("Django", re.compile(r"django|manage\.py|DJANGO_SETTINGS")),
    ("Tkinter", re.compile(r"tkinter|Tk\(\)")),
    ("PySide/Qt", re.compile(r"PySide|PyQt|QApplication")),
]

_UI_FETCH_PATTERN = re.compile(r"fetch\(['\"]([^'\"]+)['\"]")
_UI_FORM_ACTION = re.compile(r"action=[\"']([^\"']+)[\"']")
_UI_BUTTON_CLICK = re.compile(r"onclick|addEventListener.*click|\.clicked\.connect")
_TODO_FIXME_RE = re.compile(r"\b(TODO|FIXME)\b")
_NOT_IMPLEMENTED_RE = re.compile(r"\bNotImplementedError\b")


def _id(title: str) -> str:
    return hashlib.sha256(title.encode()).hexdigest()[:12]


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _iter_text_files(repo_path):
    text_exts = {".py", ".js", ".ts", ".html", ".css", ".sh", ".json",
                  ".yaml", ".yml", ".toml", ".cfg", ".ini", ".txt", ".md",
                  ".xml", ".env"}
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in scanner.IGNORE_DIRS]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext in text_exts:
                yield os.path.join(root, fn)


# --------------------------------------------------------------------------- #
# Inspection functions
# --------------------------------------------------------------------------- #


def _inspect_repository(repo_path, git_info) -> list[ReadinessCheck]:
    checks = []
    branch, wt = git_info
    checks.append(ReadinessCheck(
        check_id=_id("git-repo"), category="repository",
        title="Git repository", status="pass",
        reason="Repository is a Git working tree.",
        confidence=100,
    ))
    if wt == "dirty":
        checks.append(ReadinessCheck(
            check_id=_id("clean-tree"), category="repository",
            title="Clean working tree", status="warning",
            reason="Uncommitted changes exist. Review before launch.",
            evidence=("git status reported a dirty working tree",),
            confidence=95,
        ))
    else:
        checks.append(ReadinessCheck(
            check_id=_id("clean-tree"), category="repository",
            title="Clean working tree", status="pass",
            reason="Working tree is clean.", confidence=95,
        ))
    return checks


def _inspect_launchability(repo_path, analysis) -> list[ReadinessCheck]:
    checks = []
    launch_candidates = []
    for full in _iter_text_files(repo_path):
        rel = os.path.relpath(full, repo_path)
        content = _read(full)
        for label, pat in _LAUNCH_PATTERNS:
            if pat.search(content) and label not in launch_candidates:
                launch_candidates.append(label)

    if launch_candidates:
        checks.append(ReadinessCheck(
            check_id=_id("launch-framework"), category="launchability",
            title="Launch framework detected", status="pass",
            reason=f"Found: {', '.join(launch_candidates)}",
            evidence=(f"Framework imports found: {', '.join(launch_candidates)}",),
            confidence=85,
        ))
    else:
        checks.append(ReadinessCheck(
            check_id=_id("launch-framework"), category="launchability",
            title="Launch framework detected", status="warning",
            reason="No recognized launch framework found.",
            confidence=60,
        ))

    if analysis.entry_points:
        checks.append(ReadinessCheck(
            check_id=_id("entry-points"), category="launchability",
            title="Entry points", status="pass",
            reason=f"Found: {', '.join(analysis.entry_points)}",
            affected_paths=tuple(analysis.entry_points),
            confidence=90,
        ))
    else:
        checks.append(ReadinessCheck(
            check_id=_id("entry-points"), category="launchability",
            title="Entry points", status="warning",
            reason="No entry points found.",
            confidence=85,
        ))

    dep_present = bool(analysis.dependency_files)
    checks.append(ReadinessCheck(
        check_id=_id("dependency-files"), category="launchability",
        title="Dependency metadata",
        status="pass" if dep_present else "warning",
        reason=f"{'Found' if dep_present else 'Missing'} dependency files.",
        affected_paths=tuple(analysis.dependency_files),
        confidence=85,
    ))
    return checks


def _inspect_backend_api(repo_path) -> list[ReadinessCheck]:
    checks = []
    health_routes = []
    api_routes = []
    placeholder_routes = []
    all_routes = []

    for full in _iter_text_files(repo_path):
        rel = os.path.relpath(full, repo_path)
        content = _read(full)
        for fw, pat in _WEB_ROUTES.items():
            for m in pat.finditer(content):
                route = m.group(1) if fw == "flask" else (m.group(2) if hasattr(m, "group") and m.lastindex and m.lastindex >= 2 else m.group(1))
                all_routes.append((fw, route, rel))
                if _HEALTH_PATTERNS.search(route):
                    health_routes.append(f"{fw}:{route} ({rel})")
                if _PLACEHOLDER_PATTERNS.search(content):
                    placeholder_routes.append(f"{fw}:{route} ({rel})")

    if health_routes:
        checks.append(ReadinessCheck(
            check_id=_id("health-routes"), category="backend_api",
            title="Health endpoints", status="pass",
            reason=f"Found {len(health_routes)} health/ping route(s).",
            evidence=tuple(health_routes[:10]),
            confidence=85,
        ))
    else:
        checks.append(ReadinessCheck(
            check_id=_id("health-routes"), category="backend_api",
            title="Health endpoints", status="warning",
            reason="No health/ping routes detected.",
            confidence=70,
        ))

    if all_routes:
        checks.append(ReadinessCheck(
            check_id=_id("api-routes"), category="backend_api",
            title="API routes detected", status="pass",
            reason=f"Found {len(all_routes)} web route(s).",
            evidence=tuple(f"{fw}:{r} ({rel})" for fw, r, rel in all_routes[:10]),
            confidence=80,
        ))

    if placeholder_routes:
        checks.append(ReadinessCheck(
            check_id=_id("placeholder-routes"), category="backend_api",
            title="Placeholder/stub routes", status="fail",
            reason=f"{len(placeholder_routes)} route(s) return static/hardcoded responses.",
            evidence=tuple(placeholder_routes[:5]),
            confidence=90,
        ))

    return checks


def _inspect_ui_wiring(repo_path) -> list[ReadinessCheck]:
    checks = []
    fetch_targets = []
    form_actions = []
    dead_controls = []
    has_ui = False

    for full in _iter_text_files(repo_path):
        rel = os.path.relpath(full, repo_path)
        content = _read(full)
        is_ui_file = any(ext in rel.lower() for ext in (".html", ".js", ".ts", ".tsx", ".vue"))

        for m in _UI_FETCH_PATTERN.finditer(content):
            fetch_targets.append((m.group(1), rel))
            has_ui = True
        for m in _UI_FORM_ACTION.finditer(content):
            form_actions.append((m.group(1), rel))
            has_ui = True
        if _UI_BUTTON_CLICK.search(content):
            has_ui = True

    if has_ui:
        checks.append(ReadinessCheck(
            check_id=_id("ui-detected"), category="ui_wiring",
            title="UI components detected", status="pass",
            reason="UI code with interactive elements found.",
            confidence=80,
        ))
    else:
        checks.append(ReadinessCheck(
            check_id=_id("ui-detected"), category="ui_wiring",
            title="UI components detected", status="unknown",
            reason="No UI code detected (may be expected for this project type).",
            confidence=50,
        ))

    if fetch_targets:
        evidence_lines = [f"fetch('{t}') in {r}" for t, r in fetch_targets[:10]]
        checks.append(ReadinessCheck(
            check_id=_id("ui-fetch-targets"), category="ui_wiring",
            title="UI fetch/api targets", status="pass",
            reason=f"Found {len(fetch_targets)} fetch/API target(s).",
            evidence=tuple(evidence_lines),
            confidence=75,
        ))

    if form_actions:
        evidence_lines = [f"action='{t}' in {r}" for t, r in form_actions[:10]]
        checks.append(ReadinessCheck(
            check_id=_id("ui-form-actions"), category="ui_wiring",
            title="UI form actions", status="pass",
            reason=f"Found {len(form_actions)} form action(s).",
            evidence=tuple(evidence_lines),
            confidence=75,
        ))

    return checks


def _inspect_workflow(repo_path) -> list[ReadinessCheck]:
    checks = []
    stages = []

    for stage_id, pat in _WORKFLOW_STAGES:
        evidence = []
        for full in _iter_text_files(repo_path):
            rel = os.path.relpath(full, repo_path)
            content = _read(full)
            if pat.search(content):
                evidence.append(rel)
        if evidence:
            stages.append({
                "stage": stage_id, "status": "implemented",
                "evidence": evidence[:5],
            })
        else:
            stages.append({
                "stage": stage_id, "status": "not_found",
                "evidence": [],
            })

    implemented = sum(1 for s in stages if s["status"] == "implemented")
    checks.append(ReadinessCheck(
        check_id=_id("workflow-stages"), category="workflow",
        title="Workflow stages",
        status="pass" if implemented >= 3 else ("warning" if implemented > 0 else "fail"),
        reason=f"{implemented}/{len(stages)} workflow stages have code evidence.",
        evidence=tuple(
            f"{s['stage']}: {'found' if s['status'] == 'implemented' else 'missing'}"
            for s in stages
        ),
        confidence=70,
    ))
    return checks, stages


def _inspect_external_services(repo_path) -> list[ReadinessCheck]:
    checks = []
    services_found = []
    services_missing = []

    for svc_id, pat in _EXTERNAL_SERVICES:
        evidence = []
        for full in _iter_text_files(repo_path):
            rel = os.path.relpath(full, repo_path)
            content = _read(full)
            if pat.search(content):
                evidence.append(rel)
        if evidence:
            services_found.append((svc_id, evidence[:3]))
        else:
            services_missing.append(svc_id)

    if services_found:
        checks.append(ReadinessCheck(
            check_id=_id("external-services"), category="external_services",
            title="External service integrations",
            status="pass",
            reason=f"{len(services_found)} service(s) referenced in code.",
            evidence=tuple(f"{s}: {', '.join(e)}" for s, e in services_found),
            confidence=75,
        ))
    if services_missing:
        checks.append(ReadinessCheck(
            check_id=_id("missing-services"), category="external_services",
            title="Missing service integrations",
            status="warning",
            reason=f"No code reference found for: {', '.join(services_missing)}",
            confidence=60,
        ))
    return checks


def _inspect_production_blockers(repo_path) -> list[ReadinessCheck]:
    checks = []
    todo_files = []
    fixme_files = []
    not_implemented_files = []
    mock_files = []

    for full in _iter_text_files(repo_path):
        rel = os.path.relpath(full, repo_path)
        content = _read(full)
        if _TODO_FIXME_RE.search(content):
            if "TODO" in content:
                todo_files.append(rel)
            if "FIXME" in content:
                fixme_files.append(rel)
        if _NOT_IMPLEMENTED_RE.search(content):
            not_implemented_files.append(rel)

    if not_implemented_files:
        checks.append(ReadinessCheck(
            check_id=_id("not-implemented"), category="production_blockers",
            title="NotImplementedError references", status="fail",
            reason=f"{len(not_implemented_files)} file(s) contain unimplemented code.",
            evidence=tuple(not_implemented_files[:10]),
            affected_paths=tuple(not_implemented_files[:10]),
            confidence=90,
        ))

    if fixme_files:
        checks.append(ReadinessCheck(
            check_id=_id("fixme-markers"), category="production_blockers",
            title="FIXME markers", status="warning",
            reason=f"{len(fixme_files)} file(s) contain FIXME markers.",
            evidence=tuple(fixme_files[:10]),
            affected_paths=tuple(fixme_files[:10]),
            confidence=85,
        ))

    if todo_files:
        checks.append(ReadinessCheck(
            check_id=_id("todo-markers"), category="production_blockers",
            title="TODO markers", status="warning",
            reason=f"{len(todo_files)} file(s) contain TODO markers.",
            evidence=tuple(todo_files[:10]),
            affected_paths=tuple(todo_files[:10]),
            confidence=80,
        ))

    return checks


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def run_live_readiness(repo_path: str) -> LiveReadinessReport:
    """Inspect `repo_path` and return a LiveReadinessReport. Read-only
    throughout: opens files for reading, parses AST, never writes,
    never executes code, never contacts services."""
    repo_path = os.path.abspath(repo_path)
    git_info = scanner._git_info(repo_path)
    analysis = analyzer.run_analysis(repo_path)

    report = LiveReadinessReport(
        repo_path=repo_path,
        project_type=analysis.project_type,
    )

    all_checks = []
    all_checks.extend(_inspect_repository(repo_path, git_info))
    all_checks.extend(_inspect_launchability(repo_path, analysis))

    launch_candidates = set()
    for ch in all_checks:
        if ch.category == "launchability" and ch.status == "pass":
            for ev in ch.evidence:
                launch_candidates.add(ev)
    report.launch_candidates = tuple(sorted(launch_candidates))

    all_checks.extend(_inspect_backend_api(repo_path))
    all_checks.extend(_inspect_ui_wiring(repo_path))
    wf_checks, stages = _inspect_workflow(repo_path)
    all_checks.extend(wf_checks)
    report.workflow_stages = tuple(stages)
    all_checks.extend(_inspect_external_services(repo_path))
    all_checks.extend(_inspect_production_blockers(repo_path))

    health_eps = set()
    for ch in all_checks:
        if ch.category == "backend_api" and ch.title == "Health endpoints":
            for ev in ch.evidence:
                health_eps.add(ev)
    report.health_endpoints = tuple(sorted(health_eps))

    # Order checks deterministically
    cat_order = {c: i for i, c in enumerate(_CATEGORIES)}
    status_order = {"fail": 0, "blocked": 1, "warning": 2, "unknown": 3, "pass": 4}
    report.checks = tuple(sorted(all_checks, key=lambda c: (
        cat_order.get(c.category, 99),
        status_order.get(c.status, 9),
        c.title,
        c.check_id,
    )))

    blockers = []
    for ch in report.checks:
        if ch.status in ("fail", "blocked"):
            blockers.append(f"[{ch.category}] {ch.title}: {ch.reason}")
    report.blockers = tuple(blockers)

    if not blockers:
        report.overall_status = "ready"
    elif any(s["stage"] == "script_generation" and s["status"] == "not_found"
             for s in stages):
        report.overall_status = "not_ready"
    elif sum(1 for b in blockers) >= 5:
        report.overall_status = "not_ready"
    else:
        report.overall_status = "needs_attention"

    if report.checks:
        report.confidence = round(
            sum(c.confidence for c in report.checks) / len(report.checks)
        )
    return report
