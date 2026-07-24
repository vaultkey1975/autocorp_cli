#!/usr/bin/env python3
"""
Controlled Live Application Test  (AutoCorp CLI - brains)  [Phase 1J-1L]
=========================================================================

Live-test engine: resolves correct uvicorn launch target, adaptively polls
port readiness, retrieves full OpenAPI schema, inventories every route
with safety classification, maps workflow stages, and proposes a
disposable end-to-end test plan — all without modifying the target.

Public API:
    run_live_test(repo_path, timeout, port) -> LiveTestReport
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from brains import scanner, analyzer

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: str
    title: str
    findings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class LiveTestResult:
    stage: str
    label: str
    url: str = ""
    method: str = "GET"
    status_code: int = 0
    response_time_ms: float = 0.0
    content_type: str = ""
    body_preview: str = ""
    error: str = ""
    passed: bool = False


@dataclass
class RouteEntry:
    path: str = ""
    method: str = "GET"
    operation_id: str = ""
    summary: str = ""
    tags: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    has_request_body: bool = False
    deprecated: bool = False
    security: tuple[str, ...] = ()
    safety: str = "UNKNOWN"
    safety_reason: str = ""
    tested_live: bool = False
    live_status: int = 0


@dataclass
class WorkflowMapping:
    stage: str
    source_module: str = ""
    route: str = ""
    method: str = ""
    mutation_risk: str = "unknown"
    publishing_risk: str = "no"
    safe_diagnostic: str = ""
    confidence: int = 0


@dataclass
class LiveTestReport:
    repo_path: str
    project_type: str = ""
    launch_target: str = ""
    launch_args: tuple[str, ...] = ()
    port: int = 8000
    stages: tuple[StageResult, ...] = ()
    health_results: tuple[LiveTestResult, ...] = ()
    route_inventory: tuple[RouteEntry, ...] = ()
    route_comparison: tuple[dict, ...] = ()
    workflow_mappings: tuple[WorkflowMapping, ...] = ()
    phase_1m_plan: tuple[str, ...] = ()
    openapi_info: dict = field(default_factory=dict)
    overall_status: str = "INCONCLUSIVE"
    exit_code: int = 0
    duration_seconds: float = 0.0
    before_sha256: dict = field(default_factory=dict)
    after_sha256: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers (unchanged)
# ---------------------------------------------------------------------------

_MAX_LOG_BYTES = 500 * 1024
_STARTUP_TIMEOUT = 30
_REQUEST_TIMEOUT = 10
_SHUTDOWN_TIMEOUT = 10
_FORCE_KILL_TIMEOUT = 5
_POLL_INTERVAL = 0.5
_MAX_OPENAPI_BYTES = 10 * 1024 * 1024

_SECRET_ENV_KEYS = {"DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ACCESS_TOKEN", "API_KEY", "SECRET_KEY"}


def _redact_env(key: str, value: str) -> str:
    if key.upper() in _SECRET_ENV_KEYS or any(
        s in key.upper() for s in ("SECRET", "TOKEN", "KEY", "PASSWORD")):
        return "[REDACTED]"
    return value


def _find_executable(name: str) -> str | None:
    return shutil.which(name)


def _sha256_file(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _run_cmd(args: list[str], cwd: str = None, env: dict = None, timeout: int = 30) -> dict:
    result = {"pid": None, "stdout": "", "stderr": "", "exit_code": None, "timed_out": False, "error": ""}
    try:
        proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        result["pid"] = proc.pid
        try:
            out, err = proc.communicate(timeout=timeout)
            result["stdout"] = (out or "")[:_MAX_LOG_BYTES]
            result["stderr"] = (err or "")[:_MAX_LOG_BYTES]
            result["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
            proc.kill()
            out, err = proc.communicate()
            result["stdout"] = (out or "")[:_MAX_LOG_BYTES]
            result["stderr"] = (err or "")[:_MAX_LOG_BYTES]
            result["exit_code"] = proc.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
    return result


def _http_get(url: str, timeout: int = _REQUEST_TIMEOUT) -> dict:
    result = {"status_code": 0, "content_type": "", "body_preview": "", "body": "", "error": "", "elapsed_ms": 0.0}
    try:
        req = urllib.request.Request(url, method="GET")
        start = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status_code"] = resp.status
            result["content_type"] = resp.headers.get("Content-Type", "")
            body = resp.read(_MAX_OPENAPI_BYTES)
            text = body.decode("utf-8", errors="replace")
            result["body"] = text
            result["body_preview"] = text[:500]
            result["elapsed_ms"] = (time.time() - start) * 1000
    except urllib.error.HTTPError as exc:
        result["status_code"] = exc.code
        result["error"] = f"HTTP {exc.code}"
    except Exception as exc:
        result["error"] = str(exc)[:200]
    return result


def _is_port_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


def _wait_port(host: str, port: int, timeout: float, proc) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_port_listening(host, port):
            return "port_listening"
        if proc and proc.poll() is not None:
            return "process_exited_early"
        time.sleep(_POLL_INTERVAL)
    return "startup_timed_out"


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _py_ast_findings(content: str) -> dict:
    findings = {"fastapi_app": False, "fastapi_factory": False, "app_name": "", "factory_name": ""}
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return findings
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets if isinstance(node.targets, list) else [node.targets]:
                if isinstance(target, ast.Name):
                    if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "FastAPI":
                        findings["fastapi_app"] = True
                        findings["app_name"] = target.id
        if isinstance(node, ast.FunctionDef) and node.name == "create_app":
            if node.returns and isinstance(node.returns, ast.Name) and node.returns.id == "FastAPI":
                findings["fastapi_factory"] = True
                findings["factory_name"] = node.name
            elif _returns_fastapi(node):
                findings["fastapi_factory"] = True
                findings["factory_name"] = node.name
    return findings


def _returns_fastapi(func_node: ast.FunctionDef) -> bool:
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == "FastAPI":
                return True
    return func_node.name == "create_app"


def _detect_launch_target(repo_path: str, venv_python: str) -> tuple[str, list[str], str]:
    web_app = os.path.join(repo_path, "src", "clonecast", "web_app.py")
    if not os.path.isfile(web_app):
        return "", [], "No web_app.py found."
    content = _read_file(web_app)
    findings = _py_ast_findings(content)
    if findings["fastapi_factory"] and findings["fastapi_app"]:
        return "clonecast.web_app:create_app", [venv_python, "-m", "uvicorn", "clonecast.web_app:create_app", "--factory", "--host", "127.0.0.1"], "FastAPI factory create_app() detected"
    if findings["fastapi_app"]:
        return "clonecast.web_app:app", [venv_python, "-m", "uvicorn", "clonecast.web_app:app", "--host", "127.0.0.1"], "FastAPI app detected"
    return "", [], "No FastAPI target found."


# ---------------------------------------------------------------------------
# Route safety classification
# ---------------------------------------------------------------------------

_MUTATING_PATH_KEYWORDS = ("create", "delete", "update", "edit", "remove", "publish", "upload", "generate", "render", "execute", "run", "start", "stop", "approve", "reject", "save", "submit", "post", "clone", "copy", "move", "reset", "clear")
_SAFE_PATH_KEYWORDS = ("health", "ping", "status", "docs", "openapi", "static", "favicon", "robots")
_GET_ROUTES_NEVER_SAFE = ("generate", "render", "publish", "build", "produce")

_MUTATING_VERBS = {"POST", "PUT", "PATCH", "DELETE"}


def _classify_route(path: str, method: str, operation_id: str,
                    summary: str, has_body: bool) -> str:
    path_lower = path.lower()
    opid_lower = operation_id.lower()
    summary_lower = summary.lower()

    if "health" in path_lower or "health" in opid_lower:
        return "SAFE_HEALTH"
    if any(k in path_lower for k in ("/docs", "/openapi", "/favicon", "/robots", "/static/")):
        return "SAFE_METADATA"
    if method == "GET" and any(k in path_lower for k in ("/static/",)):
        return "SAFE_STATIC"
    if "publish" in path_lower or "publish" in opid_lower or "publish" in summary_lower:
        return "PUBLISHING"
    if "oauth" in path_lower or "oauth" in opid_lower or "oauth" in summary_lower:
        return "EXTERNAL_SIDE_EFFECT"
    if any(k in path_lower for k in ("/render", "/generate", "/build", "/produce")):
        return "EXPENSIVE"

    if method in _MUTATING_VERBS:
        if "delete" in path_lower or "delete" in opid_lower or "remove" in path_lower:
            return "DESTRUCTIVE"
        return "MUTATING"

    if method == "GET":
        for word in _GET_ROUTES_NEVER_SAFE:
            if word in path_lower or word in opid_lower or word in summary_lower:
                return "EXPENSIVE"
        if any(w in path_lower for w in _MUTATING_PATH_KEYWORDS):
            return "REQUIRES_EXISTING_DATA"
        if has_body:
            return "REQUIRES_EXISTING_DATA"
        return "SAFE_GET"

    return "UNKNOWN"


def _parse_openapi_routes(schema: dict) -> list[RouteEntry]:
    routes = []
    paths = schema.get("paths", {})
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            if not isinstance(spec, dict):
                continue
            method_upper = method.upper()
            op_id = spec.get("operationId", "")
            summary = spec.get("summary", "") or ""
            tags = tuple(spec.get("tags", []))
            params = tuple(p.get("name", "") for p in spec.get("parameters", []) if isinstance(p, dict))
            has_body = "requestBody" in spec
            deprecated = spec.get("deprecated", False)
            security = tuple(sorted(spec.get("security", {}).keys() if isinstance(spec.get("security"), dict) else []) if spec.get("security") else [])

            safety = _classify_route(path, method_upper, op_id, summary, has_body)
            routes.append(RouteEntry(
                path=path, method=method_upper, operation_id=op_id,
                summary=summary[:200], tags=tags,
                parameters=params, has_request_body=has_body,
                deprecated=deprecated, security=security,
                safety=safety, safety_reason="",
            ))
    return routes


# ---------------------------------------------------------------------------
# Workflow mapping
# ---------------------------------------------------------------------------

_WORKFLOW_STAGES = [
    ("show_creation", re.compile(r"show|studio|series", re.IGNORECASE)),
    ("episode_creation", re.compile(r"episode|create.*episode", re.IGNORECASE)),
    ("research", re.compile(r"research|search|scrape", re.IGNORECASE)),
    ("script_generation", re.compile(r"script|write.*content", re.IGNORECASE)),
    ("character_creation", re.compile(r"character|caller|guest|voice.*profile", re.IGNORECASE)),
    ("conversation", re.compile(r"conversation|segment|talk", re.IGNORECASE)),
    ("voice_generation", re.compile(r"voice|tts|generate.*audio|chatterbox", re.IGNORECASE)),
    ("audio_assembly", re.compile(r"audio.*assembl|mix.*audio|merg.*audio|process.*audio", re.IGNORECASE)),
    ("video_rendering", re.compile(r"video|render.*episode|ffmpeg|moviepy", re.IGNORECASE)),
    ("quality_control", re.compile(r"qc|quality|review|approve|validate", re.IGNORECASE)),
    ("release", re.compile(r"release|finalize|ready", re.IGNORECASE)),
    ("publishing", re.compile(r"publish|upload|youtube|social", re.IGNORECASE)),
    ("job_status", re.compile(r"jobs|status|progress", re.IGNORECASE)),
    ("settings", re.compile(r"settings|config|profile", re.IGNORECASE)),
    ("help", re.compile(r"help|guidance|tutorial|onboarding", re.IGNORECASE)),
]


_WORKFLOW_STAGES = [
    ("show_creation", re.compile(r"show|studio|series", re.IGNORECASE)),
    ("episode_creation", re.compile(r"episode|create.*episode", re.IGNORECASE)),
    ("research", re.compile(r"research|search|scrape", re.IGNORECASE)),
    ("script_generation", re.compile(r"script|write.*content", re.IGNORECASE)),
    ("character_creation", re.compile(r"character|caller|guest|voice.*profile", re.IGNORECASE)),
    ("conversation", re.compile(r"conversation|segment|talk", re.IGNORECASE)),
    ("voice_generation", re.compile(r"voice|tts|generate.*audio|chatterbox", re.IGNORECASE)),
    ("audio_assembly", re.compile(r"audio.*assembl|mix.*audio|merg.*audio|process.*audio", re.IGNORECASE)),
    ("video_rendering", re.compile(r"video|render.*episode|ffmpeg|moviepy", re.IGNORECASE)),
    ("quality_control", re.compile(r"qc|quality|review|approve|validate", re.IGNORECASE)),
    ("release", re.compile(r"release|finalize|ready", re.IGNORECASE)),
    ("publishing", re.compile(r"publish|upload|youtube|social", re.IGNORECASE)),
    ("job_status", re.compile(r"jobs|status|progress", re.IGNORECASE)),
    ("settings", re.compile(r"settings|config|profile", re.IGNORECASE)),
    ("help", re.compile(r"help|guidance|tutorial|onboarding", re.IGNORECASE)),
]



def _map_workflows(routes: list[RouteEntry], repo_path: str) -> list[WorkflowMapping]:
    mappings = []
    for stage_name, pat in _WORKFLOW_STAGES:
        matched_routes = [r for r in routes if pat.search(r.path) or pat.search(r.operation_id) or pat.search(r.summary)]
        if matched_routes:
            r = matched_routes[0]
            mutation = "mutating" if r.method in _MUTATING_VERBS else "read_only"
            pub_risk = "yes" if "publish" in r.path.lower() or "publish" in r.operation_id.lower() else "no"
            safe_diag = f"GET {r.path} with safe parameter" if r.method == "GET" else "no safe diagnostic available"
            mappings.append(WorkflowMapping(
                stage=stage_name, route=r.path, method=r.method,
                mutation_risk=mutation, publishing_risk=pub_risk,
                safe_diagnostic=safe_diag, confidence=70,
            ))
        else:
            mappings.append(WorkflowMapping(
                stage=stage_name, mutation_risk="no_route_found",
                confidence=0,
            ))
    return mappings


def _phase_1m_plan(routes: list[RouteEntry]) -> tuple[str, ...]:
    safe = [r for r in routes if r.safety.startswith("SAFE_")]
    mutating = [r for r in routes if r.safety in ("MUTATING", "DESTRUCTIVE", "PUBLISHING")]
    return (
        f"Safe routes available for diagnostics: {len(safe)}",
        f"Mutating/destructive routes to avoid: {len(mutating)}",
        "Phase 1M plan requires:",
        "  - Disposable database (temp copy of schema only)",
        "  - Disposable output directory",
        "  - External integrations disabled (YouTube, chatterbox, ollama for generation)",
        "  - Test sequence: create show -> create episode -> research (dry-run) -> script (preview)",
        "  - Stop before voice generation, audio rendering, or publishing",
        "  - Clean up all temp files and temp database",
        "  - Verify production database unchanged throughout",
    )


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _run_preflight(repo_path: str) -> StageResult:
    findings, errors = [], []
    if not os.path.isdir(repo_path):
        return StageResult(stage="1", status="PREFLIGHT_FAILED", title="Preflight",
                           errors=("Repository path does not exist.",))
    git_info = scanner._git_info(repo_path)
    findings.append(f"Git: branch={git_info[0]}, tree={git_info[1]}")
    venvs = [e for e in os.listdir(repo_path) if os.path.isfile(os.path.join(repo_path, e, "bin", "python"))]
    findings.append(f"Venvs: {', '.join(venvs) or 'none'}")
    if not venvs:
        errors.append("No venv found.")
    for exe in ("ffmpeg", "ffprobe", "ollama"):
        f = shutil.which(exe)
        findings.append(f"{exe}: {'found' if f else 'missing'}")
    status = "PREFLIGHT_PASSED" if not errors else "PREFLIGHT_FAILED"
    return StageResult(stage="1", status=status, title="Preflight",
                       findings=tuple(findings), errors=tuple(errors))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_live_test(repo_path: str, timeout: int = _STARTUP_TIMEOUT,
                  port: int = 8000) -> LiveTestReport:
    repo_path = os.path.abspath(repo_path)
    t0 = time.time()
    analysis = analyzer.run_analysis(repo_path)
    report = LiveTestReport(repo_path=repo_path, project_type=analysis.project_type, port=port)
    stages, health_results, route_inventory = [], [], []
    before_state = {"databases": {}}
    db_path = os.path.join(repo_path, "db", "cloneshow.db")
    if os.path.isfile(db_path):
        before_state["databases"]["db/cloneshow.db"] = _sha256_file(db_path)
    report.before_sha256 = dict(before_state["databases"])

    s1 = _run_preflight(repo_path)
    stages.append(s1)
    if s1.status == "PREFLIGHT_FAILED":
        report.stages = tuple(stages); report.overall_status = "UNSAFE_TO_LAUNCH"; report.duration_seconds = time.time() - t0; return report

    venv_python = os.path.join(repo_path, ".venv", "bin", "python")
    if not os.path.isfile(venv_python):
        stages.append(StageResult(stage="2", status="LAUNCH_TARGET_INVALID", title="Launch", errors=("No .venv found.",)))
        report.stages = tuple(stages); report.overall_status = "LAUNCH_TARGET_INVALID"; report.duration_seconds = time.time() - t0; return report

    target, args, reason = _detect_launch_target(repo_path, venv_python)
    if not target:
        stages.append(StageResult(stage="2", status="LAUNCH_TARGET_INVALID", title="Launch", errors=(reason,)))
        report.stages = tuple(stages); report.overall_status = "LAUNCH_TARGET_INVALID"; report.duration_seconds = time.time() - t0; return report

    if _is_port_listening("127.0.0.1", port):
        for alt in range(port + 1, port + 100):
            if not _is_port_listening("127.0.0.1", alt):
                port = alt; report.port = alt; break
    args.append(f"--port={port}")
    report.launch_target = target; report.launch_args = tuple(args)

    env = os.environ.copy()
    env["PATH"] = os.path.join(repo_path, ".venv", "bin") + ":" + env.get("PATH", "")
    env.pop("DEEPSEEK_API_KEY", None)
    stages.append(StageResult(stage="2", status="LAUNCH_PLAN_READY", title="Launch",
                   findings=(f"Target: {target}", f"Reason: {reason}", f"Port: {port}")))

    try:
        proc = subprocess.Popen(args, cwd=repo_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        stages.append(StageResult(stage="3", status="STARTUP_FAILED", title="Startup", errors=(str(exc),)))
        report.stages = tuple(stages); report.overall_status = "STARTUP_FAILED"; report.duration_seconds = time.time() - t0; return report

    port_status = _wait_port("127.0.0.1", port, timeout, proc)
    if port_status == "process_exited_early":
        out, err = proc.communicate()
        stages.append(StageResult(stage="3", status="PROCESS_EXITED_EARLY", title="Startup",
                       findings=(f"Exit: {proc.returncode}",), errors=((err or "")[:500],)))
        report.stages = tuple(stages); report.overall_status = "PROCESS_EXITED_EARLY"; report.duration_seconds = time.time() - t0; return report
    if port_status == "startup_timed_out":
        proc.kill(); proc.communicate()
        stages.append(StageResult(stage="3", status="PORT_NOT_LISTENING", title="Startup",
                       errors=(f"Port {port} did not listen within {timeout}s.",)))
        report.stages = tuple(stages); report.overall_status = "PORT_NOT_LISTENING"; report.duration_seconds = time.time() - t0; return report
    stages.append(StageResult(stage="3", status="APPLICATION_RESPONDING", title="Startup",
                   findings=(f"PID: {proc.pid}", f"Port {port} listening")))

    # Stage 4: Health checks + full OpenAPI retrieval
    base_url = f"http://127.0.0.1:{port}"
    openapi_data = {}
    for path, label in (("/health", "Health"), ("/docs", "API docs"),
                         ("/openapi.json", "OpenAPI"), ("/", "Root"),
                         ("/api/jobs/nonexistent", "Safe API route")):
        res = _http_get(f"{base_url}{path}", _REQUEST_TIMEOUT)
        if path == "/openapi.json" and res["status_code"] == 200 and res["body"]:
            try:
                openapi_data = json.loads(res["body"])
            except json.JSONDecodeError:
                pass
        health_results.append(LiveTestResult(
            stage="4", label=label, url=f"{base_url}{path}", method="GET",
            status_code=res["status_code"], response_time_ms=res.get("elapsed_ms", 0),
            content_type=res.get("content_type", ""),
            body_preview=res.get("body_preview", ""),
            error=res.get("error", ""),
            passed=200 <= res["status_code"] < 500,
        ))
    passed = sum(1 for r in health_results if r.passed)
    report.openapi_info = {"title": openapi_data.get("info", {}).get("title", ""),
                           "version": openapi_data.get("info", {}).get("version", ""),
                           "openapi": openapi_data.get("openapi", "")}

    # Parse routes from full OpenAPI
    if openapi_data:
        route_inventory = _parse_openapi_routes(openapi_data)
    report.route_inventory = tuple(route_inventory)

    stages.append(StageResult(stage="4", status="HEALTH_CHECKS_COMPLETE", title="Health + Routes",
                   findings=(f"{passed}/{len(health_results)} healthy",
                             f"OpenAPI parsed: {'yes' if openapi_data else 'no'}",
                             f"Runtime routes: {len(route_inventory)}")))

    # Stage 5: Route inventory and classification
    safety_counts = {}
    for r in route_inventory:
        safety_counts[r.safety] = safety_counts.get(r.safety, 0) + 1
    method_counts = {}
    for r in route_inventory:
        method_counts[r.method] = method_counts.get(r.method, 0) + 1
    safe_routes = [r for r in route_inventory if r.safety.startswith("SAFE_")]
    unsafe_routes = [r for r in route_inventory if not r.safety.startswith("SAFE_")]
    stages.append(StageResult(stage="5", status="ROUTE_INVENTORY_COMPLETE", title="Route Inventory",
                   findings=(
                       f"Methods: " + ", ".join(f"{m}: {c}" for m, c in sorted(method_counts.items())),
                       f"Safe: {len(safe_routes)}, Unsafe: {len(unsafe_routes)}",
                       f"Safety classes: " + ", ".join(f"{k}: {v}" for k, v in sorted(safety_counts.items())),
                   )))

    # Additional safe GET verification
    for r in safe_routes[:5]:
        if r.path in ("/health", "/", "/docs", "/openapi.json"):
            continue
        url = f"{base_url}{r.path}"
        res = _http_get(url, _REQUEST_TIMEOUT)
        r.tested_live = True; r.live_status = res["status_code"]

    # Stage 6: Workflow mapping
    workflow_mappings = _map_workflows(route_inventory, repo_path)
    report.workflow_mappings = tuple(workflow_mappings)
    mapped = sum(1 for w in workflow_mappings if w.route)
    stages.append(StageResult(stage="6", status="WORKFLOW_MAPPED", title="Workflow",
                   findings=(f"{mapped}/{len(workflow_mappings)} stages have live routes",)))

    # Stage 7: Phase 1M plan
    report.phase_1m_plan = _phase_1m_plan(route_inventory)
    stages.append(StageResult(stage="7", status="PHASE_1M_PLAN_READY", title="Phase 1M Plan",
                   findings=report.phase_1m_plan))

    # Services
    svcs = []
    for name, exe in (("ollama", "ollama"), ("ffmpeg", "ffmpeg"), ("ffprobe", "ffprobe")):
        f = shutil.which(exe)
        svcs.append({"service": name, "state": "installed" if f else "missing"})
    ollama_res = _http_get("http://127.0.0.1:11434/api/tags", timeout=3)
    svcs.append({"service": "ollama_endpoint", "state": "ready" if ollama_res["status_code"] == 200 else "not_running"})
    chatterbox_py = "/home/larry/clonecast/.venv-chatterbox/bin/python"
    svcs.append({"service": "chatterbox_venv", "state": "installed" if os.path.isfile(chatterbox_py) else "missing"})
    report.dependency_status = tuple(svcs)
    stages.append(StageResult(stage="8", status="SERVICES_CHECKED", title="Services",
                   findings=(f"{sum(1 for s in svcs if s['state'] in ('installed','ready'))}/{len(svcs)} available",)))

    # Shutdown
    shutdown = {"method": "SIGTERM", "killed": False}
    try:
        proc.terminate()
        try:
            proc.wait(timeout=_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill(); shutdown["method"] = "SIGKILL"; shutdown["killed"] = True
            proc.wait(timeout=_FORCE_KILL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        shutdown["status"] = "already_dead"
    stages.append(StageResult(stage="9", status="SHUTDOWN_COMPLETE", title="Shutdown",
                   findings=(f"Method: {shutdown['method']}", f"Killed: {shutdown['killed']}")))

    # Verification
    if os.path.isfile(db_path):
        report.after_sha256 = {"db/cloneshow.db": _sha256_file(db_path)}
    db_ok = report.before_sha256 == report.after_sha256
    git_r = _run_cmd(["git", "status", "--short"], cwd=repo_path, timeout=5)
    git_clean = not git_r["stdout"].strip()
    stages.append(StageResult(stage="10", status="VERIFIED", title="Integrity",
                   findings=(f"DB unchanged: {db_ok}", f"Git clean: {git_clean}")))

    report.stages = tuple(stages); report.health_results = tuple(health_results)
    report.duration_seconds = time.time() - t0

    if passed == len(health_results):
        report.overall_status = "LIVE_HTTP_READY"
    elif passed >= 2:
        report.overall_status = "LIVE_HTTP_READY_WITH_WARNINGS"
    elif passed >= 1:
        report.overall_status = "HEALTH_ENDPOINT_FAILED"
    else:
        report.overall_status = "INCONCLUSIVE"
    return report
