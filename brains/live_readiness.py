#!/usr/bin/env python3
"""
Live Application Readiness Scanner  (AutoCorp CLI - brains)  [Phase 1H + 1I]
==============================================================================

A read-only, static-inspection diagnostic that classifies evidence by source
quality and distinguishes production implementations from documentation,
tests, examples, and incidental text matches.

Public API:
    run_live_readiness(repo_path: str) -> LiveReadinessReport
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from dataclasses import dataclass, field

from brains import scanner, analyzer

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

_CATEGORIES = (
    "repository", "launchability", "backend_api", "ui_wiring",
    "workflow", "external_services", "production_blockers",
)

_SOURCE_TYPES = (
    "production_source", "application_template", "application_static_asset",
    "test_source", "documentation", "specification", "example", "fixture",
    "generated", "dependency", "unknown",
)

_STATUSES = (
    "pass", "fail", "blocked", "warning", "unknown",
)

_GRADES = (
    "confirmed", "strong", "supporting", "mention_only", "conflicting",
)

_WF_STATUSES = (
    "implemented", "partially_implemented", "referenced_only",
    "not_found", "conflicting_evidence",
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
# File classification
# --------------------------------------------------------------------------- #

_TEST_DIRS = {"tests", "test", "spec", "specs"}
_TEST_NAME_RE = re.compile(r"^test_|_test\.py$|^conftest\.py$")
_DOC_EXTS = {".md", ".rst", ".txt", ".adoc"}
_SPEC_EXTS = {".md"}
_DOC_DIRS = {"docs", "doc", "documentation"}
_EXAMPLE_DIRS = {"examples", "example", "demos", "demo", "samples", "sample"}
_FIXTURE_DIRS = {"fixtures", "fixture"}
_GENERATED_DIRS = {"build", "dist", "__pycache__", ".pytest_cache"}

_TEXT_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm",
    ".css", ".scss", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    ".md", ".rst", ".txt", ".sql", ".sh", ".bash", ".xml", ".env",
}

_BINARY_EXTS = {
    ".pb", ".onnx", ".pt", ".pth", ".safetensors", ".bin", ".so",
    ".dll", ".dylib", ".pyc", ".pyo", ".db", ".sqlite", ".sqlite3",
    ".wav", ".mp3", ".flac", ".ogg", ".mp4", ".mkv", ".avi", ".png",
    ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".7z",
}

_MAX_TEXT_FILE_SIZE = 5 * 1024 * 1024


def _classify_file(rel_path: str, name: str) -> str:
    """Classify a file by its source type. Deterministic."""
    ext = os.path.splitext(name)[1].lower()
    parts = rel_path.replace(os.sep, "/").split("/")

    if any(d in parts for d in _GENERATED_DIRS):
        return "generated"
    if any(d in parts for d in _FIXTURE_DIRS):
        return "fixture"
    if _TEST_NAME_RE.search(name):
        return "test_source"
    if any(d in parts for d in _TEST_DIRS):
        return "test_source"
    if any(d in parts for d in _EXAMPLE_DIRS):
        return "example"
    if any(part.endswith(".d.ts") for part in parts):
        return "generated"
    if name in ("Dockerfile", "Makefile") or "requirements" in name.lower():
        return "production_source"
    if any(d in parts for d in _DOC_DIRS):
        if ext in _SPEC_EXTS and "spec" in rel_path.lower():
            return "specification"
        if ext in _DOC_EXTS:
            return "documentation"
    if ext in _DOC_EXTS:
        if "build_spec" in name.lower() or "spec" in rel_path.lower() or "readme" in name.lower():
            return "specification"
        return "documentation"
    if ext == ".html" or ext == ".htm":
        return "application_template"
    if ext in (".js", ".jsx", ".ts", ".tsx", ".css", ".scss"):
        return "application_static_asset"
    if ext == ".py":
        return "production_source"
    return "unknown"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _id(title: str) -> str:
    return hashlib.sha256(title.encode()).hexdigest()[:12]


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (UnicodeDecodeError, PermissionError, OSError):
        return ""


def _iter_text_files(repo_path):
    for root, dirs, files in os.walk(repo_path, followlinks=False):
        dirs[:] = [d for d in dirs
                   if not scanner._should_skip_dir(d, scanner.IGNORE_DIRS)]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            name_lower = fn.lower()
            if ext in _BINARY_EXTS:
                continue
            if ext not in _TEXT_EXTS and fn not in ("Dockerfile", "Makefile") and "requirements" not in name_lower:
                continue
            full = os.path.join(root, fn)
            try:
                if os.path.getsize(full) > _MAX_TEXT_FILE_SIZE:
                    continue
            except OSError:
                continue
            yield full


def _is_production_source(src_type: str) -> bool:
    return src_type in ("production_source", "application_template",
                         "application_static_asset")


def _grade_evidence(src_type: str) -> str:
    if src_type == "production_source":
        return "strong"
    if src_type == "specification":
        return "supporting"
    if src_type in ("application_template", "application_static_asset"):
        return "strong"
    if src_type == "test_source":
        return "supporting"
    if src_type == "documentation":
        return "mention_only"
    if src_type == "fixture":
        return "supporting"
    if src_type == "example":
        return "supporting"
    return "mention_only"


# --------------------------------------------------------------------------- #
# AST-based Python analysis (production only)
# --------------------------------------------------------------------------- #

_PLACEHOLDER_RETURN_RE = re.compile(
    r"return\s*\{\s*['\"]status['\"]\s*:\s*['\"]ok['\"]\s*\}|"
    r"return\s*\{\s*['\"]message['\"].*['\"]success['\"]|"
    r"return\s*\{\s*['\"]success['\"]\s*:\s*True\s*\}|"
    r"return\s*True\s*$|return\s*\{\s*\}\s*$",
    re.IGNORECASE,
)


def _py_ast_findings(content: str) -> dict:
    """Analyze Python source via AST. Returns dict with production-only findings."""
    findings = {
        "has_not_implemented": False,
        "has_pass_body": 0,
        "has_ellipsis_body": 0,
        "placeholder_return": 0,
        "has_main_block": False,
        "is_fastapi_app": False,
        "fastapi_routes": [],
        "flask_routes": [],
        "django_urls": [],
    }
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        if isinstance(node, ast.Raise):
            if isinstance(node.exc, ast.Name) and node.exc.id == "NotImplementedError":
                findings["has_not_implemented"] = True
            elif isinstance(node.exc, ast.Call):
                exc_name = ""
                if isinstance(node.exc.func, ast.Name):
                    exc_name = node.exc.func.id
                elif isinstance(node.exc.func, ast.Attribute):
                    exc_name = node.exc.func.attr
                if exc_name == "NotImplementedError":
                    findings["has_not_implemented"] = True

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                findings["has_pass_body"] += 1
            elif len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis:
                findings["has_ellipsis_body"] += 1

        if isinstance(node, ast.Return):
            line = ast.get_source_segment(content, node) or ""
            if _PLACEHOLDER_RETURN_RE.search(line):
                findings["placeholder_return"] += 1

        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Compare):
                left = ast.get_source_segment(content, test.left) if hasattr(ast, 'get_source_segment') else ""
                if left == "__name__" and ast.get_source_segment(content, test) == '__name__ == "__main__"':
                    findings["has_main_block"] = True

    # Route detection via AST (decorators)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                dec_str = ast.get_source_segment(content, dec) if hasattr(ast, 'get_source_segment') else ""
                if not dec_str:
                    continue
                dec_str_clean = dec_str.strip()
                if dec_str_clean.startswith("app.route("):
                    m = re.search(r"""app\.route\(['\"]([^'\"]+)""", dec_str_clean)
                    if m:
                        findings["flask_routes"].append(m.group(1))
                elif "app." in dec_str_clean and "(" in dec_str_clean:
                    m = re.search(r"""app\.(get|post|put|delete|patch)\(['\"]([^'\"]+)""", dec_str_clean)
                    if m:
                        findings["fastapi_routes"].append(m.group(2))
                        findings["is_fastapi_app"] = True

    # Detect FastAPI app instantiation
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = node.value
            if isinstance(value, ast.Call):
                func = value.func
                if isinstance(func, ast.Name) and func.id == "FastAPI":
                    findings["is_fastapi_app"] = True

    return findings


def _find_entry_points(repo_path: str, analysis) -> list[str]:
    """Find application entry points using AST and config analysis."""
    entry_points = list(analysis.entry_points) if analysis.entry_points else []

    # Check pyproject.toml for [project.scripts]
    pyproject = os.path.join(repo_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        content = _read(pyproject)
        if "[project.scripts]" in content:
            entry_points.append("pyproject.toml [project.scripts]")

    # Check setup.cfg for console_scripts
    setup_cfg = os.path.join(repo_path, "setup.cfg")
    if os.path.isfile(setup_cfg):
        content = _read(setup_cfg)
        if "console_scripts" in content:
            entry_points.append("setup.cfg console_scripts")

    # AST: find if __name__ == "__main__" blocks in production source
    for full in _iter_text_files(repo_path):
        rel = os.path.relpath(full, repo_path)
        name = os.path.basename(full)
        src_type = _classify_file(rel, name)
        if src_type != "production_source":
            continue
        content = _read(full)
        findings = _py_ast_findings(content)
        if findings["has_main_block"]:
            entry_points.append(f"{rel} (__main__ block)")
        if findings["is_fastapi_app"]:
            entry_points.append(f"{rel} (FastAPI app)")

    return sorted(set(entry_points))


# --------------------------------------------------------------------------- #
# Inspection functions
# --------------------------------------------------------------------------- #


def _inspect_repository(repo_path, git_info) -> list[ReadinessCheck]:
    checks = []
    branch, wt = git_info
    checks.append(ReadinessCheck(
        check_id=_id("git-repo"), category="repository",
        title="Git repository", status="pass",
        reason="Repository is a Git working tree.", confidence=100,
    ))
    status = "warning" if wt == "dirty" else "pass"
    reason = "Uncommitted changes exist." if wt == "dirty" else "Working tree is clean."
    checks.append(ReadinessCheck(
        check_id=_id("clean-tree"), category="repository",
        title="Clean working tree", status=status, reason=reason, confidence=95,
    ))
    return checks


def _inspect_launchability(repo_path, analysis) -> list[ReadinessCheck]:
    checks = []
    launch_candidates = []

    for full in _iter_text_files(repo_path):
        rel = os.path.relpath(full, repo_path)
        name = os.path.basename(full)
        src_type = _classify_file(rel, name)
        if not _is_production_source(src_type):
            continue
        content = _read(full)
        launch_markers = {
            "FastAPI": re.compile(r"from\s+fastapi\s+import|FastAPI\(\)"),
            "Flask": re.compile(r"from\s+flask\s+import|Flask\(__name__\)"),
            "Django": re.compile(r"from\s+django|DJANGO_SETTINGS"),
            "Tkinter": re.compile(r"from\s+tkinter|Tk\(\)"),
            "PySide/Qt": re.compile(r"PySide|PyQt|QApplication"),
        }
        for label, pat in launch_markers.items():
            if pat.search(content) and label not in launch_candidates:
                launch_candidates.append(label)

    if launch_candidates:
        checks.append(ReadinessCheck(
            check_id=_id("launch-framework"), category="launchability",
            title="Launch framework detected", status="pass",
            reason=f"Found: {', '.join(launch_candidates)}",
            evidence=(f"Production source imports: {', '.join(launch_candidates)}",),
            confidence=85,
        ))

    entry_points = _find_entry_points(repo_path, analysis)
    if entry_points:
        checks.append(ReadinessCheck(
            check_id=_id("entry-points"), category="launchability",
            title="Entry points", status="pass",
            reason=f"Found {len(entry_points)} entry point(s).",
            evidence=tuple(entry_points[:10]),
            affected_paths=tuple(entry_points[:10]),
            confidence=90,
        ))
    else:
        checks.append(ReadinessCheck(
            check_id=_id("entry-points"), category="launchability",
            title="Entry points", status="warning",
            reason="No entry points found in production source.",
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
    prod_routes = []
    placeholder_routes = []

    for full in _iter_text_files(repo_path):
        rel = os.path.relpath(full, repo_path)
        name = os.path.basename(full)
        src_type = _classify_file(rel, name)
        if src_type != "production_source":
            continue
        content = _read(full)
        findings = _py_ast_findings(content)

        for route in findings["fastapi_routes"]:
            prod_routes.append(f"fastapi:{route} ({rel})")
            if re.search(r"health|ping|status|ready|alive|heartbeat", route, re.IGNORECASE):
                health_routes.append(f"fastapi:{route} ({rel})")
        for route in findings["flask_routes"]:
            prod_routes.append(f"flask:{route} ({rel})")

        if _PLACEHOLDER_RETURN_RE.search(content):
            placeholder_routes.append(rel)

    if health_routes:
        checks.append(ReadinessCheck(
            check_id=_id("health-routes"), category="backend_api",
            title="Health endpoints", status="pass",
            reason=f"Found {len(health_routes)} health route(s) in production code.",
            evidence=tuple(health_routes[:10]), confidence=85,
        ))

    if prod_routes:
        checks.append(ReadinessCheck(
            check_id=_id("api-routes"), category="backend_api",
            title="API routes detected", status="pass",
            reason=f"Found {len(prod_routes)} production web route(s).",
            evidence=tuple(prod_routes[:10]), confidence=80,
        ))

    if placeholder_routes:
        checks.append(ReadinessCheck(
            check_id=_id("placeholder-routes"), category="backend_api",
            title="Placeholder/stub routes", status="fail",
            reason=f"{len(placeholder_routes)} route(s) in production code return static responses.",
            evidence=tuple(placeholder_routes[:5]), confidence=90,
        ))

    return checks


def _inspect_ui_wiring(repo_path) -> list[ReadinessCheck]:
    checks = []
    fetch_targets = []
    form_actions = []

    for full in _iter_text_files(repo_path):
        rel = os.path.relpath(full, repo_path)
        name = os.path.basename(full)
        src_type = _classify_file(rel, name)
        ok_types = ("application_template", "application_static_asset")
        if src_type not in ok_types:
            continue
        content = _read(full)

        for m in re.finditer(r"""fetch\(['\"]([^'\"]+)['\"]""", content):
            fetch_targets.append((m.group(1), rel))
        for m in re.finditer(r"""\baction\s*=\s*["']([^"']+)["']""", content):
            form_actions.append((m.group(1), rel))

    has_ui = bool(fetch_targets or form_actions)
    if has_ui:
        checks.append(ReadinessCheck(check_id=_id("ui-detected"), category="ui_wiring",
            title="UI components detected", status="pass",
            reason="Application UI code found with interactive elements.",
            confidence=80,
        ))

    if fetch_targets:
        checks.append(ReadinessCheck(check_id=_id("ui-fetch-targets"), category="ui_wiring",
            title="UI fetch/api targets", status="pass",
            reason=f"Found {len(fetch_targets)} real fetch/API target(s).",
            evidence=tuple(f"fetch({t!r}) in {r}" for t, r in fetch_targets[:10]),
            confidence=80,
        ))

    if form_actions:
        checks.append(ReadinessCheck(check_id=_id("ui-form-actions"), category="ui_wiring",
            title="UI form actions", status="pass",
            reason=f"Found {len(form_actions)} form action(s).",
            evidence=tuple(f"action={t!r} in {r}" for t, r in form_actions[:10]),
            confidence=80,
        ))

    return checks


_WORKFLOW_STAGES = [
    ("research", re.compile(r"research|search\-tool|scrape|crawl|web_?search")),
    ("script_generation", re.compile(r"script_?gener|generate.*script")),
    ("voice_generation", re.compile(r"voice_?gen|tts|text.to.speech|speech|elevenlabs|edge.tts|chatterbox")),
    ("audio_assembly", re.compile(r"audio.*(assembl|mix|merg|combin|concatenat|process)|assembl.*audio")),
    ("video_rendering", re.compile(r"video.*(render|generat|creat|composit|encod|process)|render.*video|ffmpeg|moviepy")),
    ("quality_control", re.compile(r"\bqa\b|quality.*(check|control|verif|assur)|qc_pipeline|review.*output|validat.*output")),
    ("publishing", re.compile(r"publish|upload.*youtube|youtube.*upload|social.*post|content.*distribut")),
]


def _inspect_workflow(repo_path) -> list[ReadinessCheck]:
    stages = []
    for stage_id, pat in _WORKFLOW_STAGES:
        prod_evidence = []
        other_evidence = []
        for full in _iter_text_files(repo_path):
            rel = os.path.relpath(full, repo_path)
            name = os.path.basename(full)
            src_type = _classify_file(rel, name)
            content = _read(full)
            if pat.search(content):
                if _is_production_source(src_type):
                    prod_evidence.append(rel)
                else:
                    other_evidence.append(rel)

        if prod_evidence:
            stages.append({"stage": stage_id, "status": "implemented",
                            "evidence": prod_evidence[:5]})
        elif other_evidence:
            stages.append({"stage": stage_id, "status": "referenced_only",
                            "evidence": other_evidence[:3]})
        else:
            stages.append({"stage": stage_id, "status": "not_found",
                            "evidence": []})

    implemented = sum(1 for s in stages if s["status"] == "implemented")
    referenced = sum(1 for s in stages if s["status"] == "referenced_only")
    status = "pass" if implemented >= 4 else ("warning" if implemented >= 2 else "fail")
    reason = f"{implemented} implemented, {referenced} referenced-only, {len(stages)-implemented-referenced} not found."
    checks = [ReadinessCheck(
        check_id=_id("workflow-stages"), category="workflow",
        title="Workflow stages", status=status, reason=reason,
        evidence=tuple(f"{s['stage']}: {s['status']}" for s in stages),
        confidence=75,
    )]
    return checks, stages


_EXTERNAL_SERVICES = [
    ("ollama", re.compile(r"ollama|OLLAMA_URL|AUTOCORP_MODEL")),
    ("chatterbox", re.compile(r"chatterbox")),
    ("ffmpeg", re.compile(r"ffmpeg|subprocess.*ffmpeg|moviepy")),
    ("youtube_oauth", re.compile(r"youtube.*api|google.*oauth|client_secret.*json|youtube.*credentials")),
    ("database", re.compile(r"sqlite|postgres|mysql|mongodb|DATABASE_URL|DB_PATH|sqlalchemy")),
    ("file_storage", re.compile(r"upload|file.*storage|S3|bucket|cdn")),
]


def _inspect_external_services(repo_path) -> list[ReadinessCheck]:
    checks = []
    services_found = {}

    for svc_id, pat in _EXTERNAL_SERVICES:
        prod_files = []
        other_files = []
        for full in _iter_text_files(repo_path):
            rel = os.path.relpath(full, repo_path)
            name = os.path.basename(full)
            src_type = _classify_file(rel, name)
            content = _read(full)
            if pat.search(content):
                if _is_production_source(src_type):
                    prod_files.append(rel)
                else:
                    other_files.append(rel)
        if prod_files:
            services_found[svc_id] = ("production_integration", prod_files[:3])
        elif other_files:
            services_found[svc_id] = ("documentation_only", [])

    prod_svc = [(s, d, e) for s, (d, e) in services_found.items() if d == "production_integration"]
    if prod_svc:
        checks.append(ReadinessCheck(
            check_id=_id("external-services"), category="external_services",
            title="External service integrations", status="pass",
            reason=f"{len(prod_svc)} service(s) with production code evidence.",
            evidence=tuple(f"{s}: {', '.join(e[:2])}" for s, d, e in prod_svc[:5]),
            confidence=75,
        ))
    return checks


def _inspect_production_blockers(repo_path) -> list[ReadinessCheck]:
    checks = []
    not_implemented_files = []
    fixme_files = []
    todo_files = []

    for full in _iter_text_files(repo_path):
        rel = os.path.relpath(full, repo_path)
        name = os.path.basename(full)
        src_type = _classify_file(rel, name)
        if src_type != "production_source":
            continue
        content = _read(full)
        findings = _py_ast_findings(content)

        if findings["has_not_implemented"]:
            not_implemented_files.append(rel)

        # Text-based FIXME/TODO in production source only
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                if "FIXME" in stripped:
                    fixme_files.append(f"{rel}:{stripped[:80]}")
                    break
                if "TODO" in stripped:
                    todo_files.append(f"{rel}:{stripped[:80]}")
                    break

    if not_implemented_files:
        checks.append(ReadinessCheck(
            check_id=_id("not-implemented"), category="production_blockers",
            title="NotImplementedError in production code", status="fail",
            reason=f"{len(not_implemented_files)} production file(s) contain raise NotImplementedError.",
            evidence=tuple(not_implemented_files[:10]),
            affected_paths=tuple(not_implemented_files[:10]),
            confidence=95,
        ))
    if fixme_files:
        checks.append(ReadinessCheck(
            check_id=_id("fixme-markers"), category="production_blockers",
            title="FIXME markers in production code", status="warning",
            reason=f"{len(fixme_files)} production file(s) contain FIXME.",
            evidence=tuple(fixme_files[:10]), confidence=85,
        ))
    if todo_files:
        checks.append(ReadinessCheck(
            check_id=_id("todo-markers"), category="production_blockers",
            title="TODO markers in production code", status="warning",
            reason=f"{len(todo_files)} production file(s) contain TODO.",
            evidence=tuple(todo_files[:10]), confidence=80,
        ))

    return checks


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def run_live_readiness(repo_path: str) -> LiveReadinessReport:
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
    all_checks.extend(_inspect_backend_api(repo_path))
    all_checks.extend(_inspect_ui_wiring(repo_path))
    wf_checks, stages = _inspect_workflow(repo_path)
    all_checks.extend(wf_checks)
    report.workflow_stages = tuple(stages)
    all_checks.extend(_inspect_external_services(repo_path))
    all_checks.extend(_inspect_production_blockers(repo_path))

    # Launch candidates
    lc_set = set()
    for ch in all_checks:
        if ch.category == "launchability" and ch.status == "pass":
            for ev in ch.evidence:
                lc_set.add(ev)
    report.launch_candidates = tuple(sorted(lc_set))

    # Health endpoints
    he_set = set()
    for ch in all_checks:
        if ch.category == "backend_api" and ch.title == "Health endpoints":
            for ev in ch.evidence:
                he_set.add(ev)
    report.health_endpoints = tuple(sorted(he_set))

    # Order
    cat_order = {c: i for i, c in enumerate(_CATEGORIES)}
    st_order = {"fail": 0, "blocked": 1, "warning": 2, "unknown": 3, "pass": 4}
    report.checks = tuple(sorted(all_checks, key=lambda c: (
        cat_order.get(c.category, 99), st_order.get(c.status, 9), c.title, c.check_id)))

    # Blockers
    blockers = []
    for ch in report.checks:
        if ch.status in ("fail", "blocked"):
            blockers.append(f"[{ch.category}] {ch.title}: {ch.reason}")
    report.blockers = tuple(blockers)

    if not blockers:
        report.overall_status = "ready"
    elif sum(1 for b in blockers) >= 5:
        report.overall_status = "not_ready"
    else:
        report.overall_status = "needs_attention"

    if report.checks:
        report.confidence = round(sum(c.confidence for c in report.checks) / len(report.checks))
    return report
