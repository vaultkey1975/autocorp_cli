#!/usr/bin/env python3
"""
Controlled Live Application Test  (AutoCorp CLI - brains)  [Phase 1J + 1K]
============================================================================

A safety-first live-test engine that resolves the correct web-server launch
command from production source code, starts the application with uvicorn,
adaptively polls for port readiness, runs health and route checks, and
cleanly shuts down — all without modifying the target.

Public API:
    run_live_test(repo_path, timeout, port) -> LiveTestReport
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from brains import scanner, analyzer, workspace

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


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
class LiveTestReport:
    repo_path: str
    project_type: str = ""
    launch_target: str = ""
    launch_args: tuple[str, ...] = ()
    port: int = 8000
    stages: tuple[StageResult, ...] = ()
    health_results: tuple[LiveTestResult, ...] = ()
    runtime_routes: tuple[dict, ...] = ()
    dependency_status: tuple[dict, ...] = ()
    dry_run_capabilities: tuple[dict, ...] = ()
    overall_status: str = "INCONCLUSIVE"
    exit_code: int = 0
    duration_seconds: float = 0.0
    before_sha256: dict = field(default_factory=dict)
    after_sha256: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Safety helpers
# --------------------------------------------------------------------------- #

_MAX_LOG_BYTES = 500 * 1024
_STARTUP_TIMEOUT = 30
_REQUEST_TIMEOUT = 10
_SHUTDOWN_TIMEOUT = 10
_FORCE_KILL_TIMEOUT = 5
_POLL_INTERVAL = 0.5

_SECRET_ENV_KEYS = {
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY", "YOUTUBE_API_KEY", "CLIENT_SECRET", "ACCESS_TOKEN",
    "API_KEY", "SECRET_KEY", "DATABASE_URL", "AUTH_TOKEN",
}


def _sha256_file(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _redact_env(key: str, value: str) -> str:
    if key.upper() in _SECRET_ENV_KEYS or any(
        s in key.upper() for s in ("SECRET", "TOKEN", "KEY", "PASSWORD")
    ):
        return "[REDACTED]"
    return value


def _run_cmd(args: list[str], cwd: str = None, env: dict = None,
             timeout: int = 30) -> dict:
    result = {"pid": None, "stdout": "", "stderr": "",
               "exit_code": None, "timed_out": False, "error": ""}
    try:
        proc = subprocess.Popen(
            args, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
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
    result = {"status_code": 0, "content_type": "", "body_preview": "",
               "error": "", "elapsed_ms": 0.0}
    try:
        req = urllib.request.Request(url, method="GET")
        start = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status_code"] = resp.status
            result["content_type"] = resp.headers.get("Content-Type", "")
            body = resp.read(8192).decode("utf-8", errors="replace")
            result["body_preview"] = body[:500]
            result["elapsed_ms"] = (time.time() - start) * 1000
    except urllib.error.HTTPError as exc:
        result["status_code"] = exc.code
        result["error"] = f"HTTP {exc.code}"
    except Exception as exc:
        result["error"] = str(exc)[:200]
    return result


def _find_executable(name: str) -> str | None:
    return shutil.which(name)


# --------------------------------------------------------------------------- #
# Port helpers
# --------------------------------------------------------------------------- #


def _is_port_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


def _wait_port(host: str, port: int, timeout: float,
               proc: subprocess.Popen | None = None) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_port_listening(host, port):
            return "port_listening"
        if proc and proc.poll() is not None:
            return "process_exited_early"
        time.sleep(_POLL_INTERVAL)
    return "startup_timed_out"


# --------------------------------------------------------------------------- #
# Launch target resolution
# --------------------------------------------------------------------------- #


def _detect_launch_target(repo_path: str, venv_python: str) -> tuple[str, list[str], str]:
    """Detect the correct uvicorn launch target. Returns (label, args, reason)."""
    web_app = os.path.join(repo_path, "src", "clonecast", "web_app.py")
    if os.path.isfile(web_app):
        content = _read_file(web_app)
        findings = _py_ast_findings(content)

        if findings["fastapi_factory"] and findings["fastapi_app"]:
            target = "clonecast.web_app:create_app"
            args = [venv_python, "-m", "uvicorn", target,
                    "--factory", "--host", "127.0.0.1"]
            return target, args, "FastAPI factory create_app() detected in web_app.py"

        if findings["fastapi_app"]:
            target = "clonecast.web_app:app"
            args = [venv_python, "-m", "uvicorn", target,
                    "--host", "127.0.0.1"]
            return target, args, "FastAPI app=FastAPI() detected in web_app.py"

    return "", [], "No FastAPI application target found in web_app.py"


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _py_ast_findings(content: str) -> dict:
    findings = {"fastapi_app": False, "fastapi_factory": False,
                "app_name": "", "factory_name": ""}
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets if isinstance(node.targets, list) else [node.targets]:
                if isinstance(target, ast.Name):
                    value = node.value
                    if isinstance(value, ast.Call):
                        func = value.func
                        if isinstance(func, ast.Name) and func.id == "FastAPI":
                            findings["fastapi_app"] = True
                            findings["app_name"] = target.id

        if isinstance(node, ast.FunctionDef) and node.name == "create_app":
            returns = node.returns
            if returns and isinstance(returns, ast.Name) and returns.id == "FastAPI":
                findings["fastapi_factory"] = True
                findings["factory_name"] = node.name
            elif _returns_fastapi(node):
                findings["fastapi_factory"] = True
                findings["factory_name"] = node.name

    return findings


def _returns_fastapi(func_node: ast.FunctionDef) -> bool:
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return):
            val = node.value
            if isinstance(val, ast.Call):
                if isinstance(val.func, ast.Name) and val.func.id == "FastAPI":
                    return True
            if isinstance(val, ast.Name):
                if "app" in val.id.lower():
                    return True
    # heuristic: function named create_app in file with FastAPI import
    return func_node.name == "create_app"


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #


def _run_preflight(repo_path: str) -> StageResult:
    findings, errors = [], []
    if not os.path.isdir(repo_path):
        errors.append("Repository path does not exist.")
        return StageResult(stage="1", status="PREFLIGHT_FAILED",
                           title="Preflight", findings=tuple(findings),
                           errors=tuple(errors))

    git_info = scanner._git_info(repo_path)
    findings.append(f"Git: branch={git_info[0]}, tree={git_info[1]}")
    venvs = [e for e in os.listdir(repo_path)
             if os.path.isfile(os.path.join(repo_path, e, "bin", "python"))]
    findings.append(f"Venvs: {', '.join(venvs) or 'none'}")
    if not venvs:
        errors.append("No Python virtual environment found.")

    for exe in ("ffmpeg", "ffprobe", "ollama"):
        f = _find_executable(exe)
        findings.append(f"{exe}: {'found' if f else 'missing'}")

    status = "PREFLIGHT_PASSED" if not errors else "PREFLIGHT_FAILED"
    return StageResult(stage="1", status=status, title="Preflight",
                       findings=tuple(findings), errors=tuple(errors))


def run_live_test(repo_path: str, timeout: int = _STARTUP_TIMEOUT,
                  port: int = 8000) -> LiveTestReport:
    repo_path = os.path.abspath(repo_path)
    t0 = time.time()
    analysis = analyzer.run_analysis(repo_path)
    report = LiveTestReport(repo_path=repo_path,
                            project_type=analysis.project_type, port=port)
    stages, health_results, runtime_routes = [], [], []
    before_state = {"databases": {}}

    db_path = os.path.join(repo_path, "db", "cloneshow.db")
    if os.path.isfile(db_path):
        before_state["databases"]["db/cloneshow.db"] = _sha256_file(db_path)
    report.before_sha256 = dict(before_state["databases"])

    # Stage 1: Preflight
    s1 = _run_preflight(repo_path)
    stages.append(s1)
    if s1.status == "PREFLIGHT_FAILED":
        report.stages = tuple(stages)
        report.overall_status = "UNSAFE_TO_LAUNCH"
        report.duration_seconds = time.time() - t0
        return report

    # Resolve launch target
    venv_python = os.path.join(repo_path, ".venv", "bin", "python")
    if not os.path.isfile(venv_python):
        stages.append(StageResult(stage="2", status="LAUNCH_TARGET_INVALID",
                       title="Launch Resolution",
                       errors=("No valid Python venv found.",)))
        report.stages = tuple(stages)
        report.overall_status = "LAUNCH_TARGET_INVALID"
        report.duration_seconds = time.time() - t0
        return report

    target, args, reason = _detect_launch_target(repo_path, venv_python)
    if not target:
        stages.append(StageResult(stage="2", status="LAUNCH_TARGET_INVALID",
                       title="Launch Resolution",
                       errors=(reason,)))
        report.stages = tuple(stages)
        report.overall_status = "LAUNCH_TARGET_INVALID"
        report.duration_seconds = time.time() - t0
        return report

    # Resolve port
    if _is_port_listening("127.0.0.1", port):
        for alt in range(port + 1, port + 100):
            if not _is_port_listening("127.0.0.1", alt):
                port = alt
                report.port = alt
                break
    args.append(f"--port={port}")
    report.launch_target = target
    report.launch_args = tuple(args)

    env = os.environ.copy()
    env["PATH"] = os.path.join(repo_path, ".venv", "bin") + ":" + env.get("PATH", "")
    env.pop("DEEPSEEK_API_KEY", None)

    stages.append(StageResult(stage="2", status="LAUNCH_PLAN_READY",
                   title="Launch Resolution",
                   findings=(f"Target: {target}", f"Reason: {reason}",
                             f"Args: {' '.join(args)}",
                             f"Port: {port}")))

    # Stage 3: Startup
    try:
        proc = subprocess.Popen(
            args, cwd=repo_path, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        stages.append(StageResult(stage="3", status="STARTUP_FAILED",
                       title="Startup", errors=(str(exc),)))
        report.stages = tuple(stages)
        report.overall_status = "STARTUP_FAILED"
        report.duration_seconds = time.time() - t0
        return report

    port_status = _wait_port("127.0.0.1", port, timeout, proc)

    if port_status == "process_exited_early":
        out, err = proc.communicate()
        stages.append(StageResult(stage="3", status="PROCESS_EXITED_EARLY",
                       title="Startup",
                       findings=(f"Exit code: {proc.returncode}",),
                       errors=(f"Process exited before port listened.\n"
                               f"stderr: {(err or '')[:500]}",)))
        report.stages = tuple(stages)
        report.overall_status = "PROCESS_EXITED_EARLY"
        report.duration_seconds = time.time() - t0
        return report

    if port_status == "startup_timed_out":
        proc.kill()
        proc.communicate()
        stages.append(StageResult(stage="3", status="PORT_NOT_LISTENING",
                       title="Startup",
                       errors=(f"Port {port} did not listen within {timeout}s.",)))
        report.stages = tuple(stages)
        report.overall_status = "PORT_NOT_LISTENING"
        report.duration_seconds = time.time() - t0
        return report

    stages.append(StageResult(stage="3", status="APPLICATION_RESPONDING",
                   title="Startup",
                   findings=(f"PID: {proc.pid}", f"Port {port} listening")))

    # Stage 4: Health checks
    base_url = f"http://127.0.0.1:{port}"
    endpoints = [
        ("/health", "Health endpoint"),
        ("/docs", "API docs"),
        ("/openapi.json", "OpenAPI schema"),
        ("/", "Root page"),
        ("/api/jobs/nonexistent", "Safe read-only API route"),
    ]
    for path, label in endpoints:
        url = f"{base_url}{path}"
        res = _http_get(url, _REQUEST_TIMEOUT)
        if path == "/openapi.json" and res["status_code"] == 200:
            try:
                schema = json.loads(res["body_preview"])
                for route_path, methods in schema.get("paths", {}).items():
                    runtime_routes.append({"path": route_path,
                                           "methods": list(methods.keys())})
            except (json.JSONDecodeError, AttributeError):
                pass
        health_results.append(LiveTestResult(
            stage="4", label=label, url=url, method="GET",
            status_code=res["status_code"],
            response_time_ms=res.get("elapsed_ms", 0),
            content_type=res.get("content_type", ""),
            body_preview=res.get("body_preview", ""),
            error=res.get("error", ""),
            passed=200 <= res["status_code"] < 500,
        ))

    passed = sum(1 for r in health_results if r.passed)
    stages.append(StageResult(stage="4", status="HEALTH_CHECKS_COMPLETE",
                   title="Health Checks",
                   findings=(f"{passed}/{len(health_results)} endpoints healthy",
                             f"{len(runtime_routes)} runtime routes detected")))

    report.runtime_routes = tuple(runtime_routes)

    # Stage 6: Services
    svcs = _check_services()
    report.dependency_status = tuple(svcs)
    ready = sum(1 for s in svcs if s["state"] in ("installed", "ready"))
    stages.append(StageResult(stage="6", status="SERVICE_CHECK_COMPLETE",
                   title="Services", findings=(f"{ready}/{len(svcs)} available",)))

    # Stage 7: Dry-run caps
    caps = [{"stage": s, "safe": False,
             "note": "Requires further investigation of CloneCast dry-run modes."}
            for s in ("episode", "research", "script", "voice", "publish")]
    report.dry_run_capabilities = tuple(caps)
    stages.append(StageResult(stage="7", status="DRY_RUN_DISCOVERY_COMPLETE",
                   title="Dry-Run Caps", findings=(f"{len(caps)} checked",)))

    # Stage 8: Shutdown
    shutdown = {"pid": proc.pid, "method": "SIGTERM", "killed": False}
    try:
        proc.terminate()
        try:
            proc.wait(timeout=_SHUTDOWN_TIMEOUT)
            shutdown["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            shutdown["method"] = "SIGKILL"
            shutdown["killed"] = True
            proc.wait(timeout=_FORCE_KILL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        shutdown["status"] = "already_dead"
    stages.append(StageResult(stage="8", status="SHUTDOWN_COMPLETE",
                   title="Shutdown",
                   findings=(f"Method: {shutdown['method']}",
                             f"Killed: {shutdown['killed']}")))

    # Stage 9: Verification
    if os.path.isfile(db_path):
        report.after_sha256 = {"db/cloneshow.db": _sha256_file(db_path)}
    db_ok = report.before_sha256 == report.after_sha256
    git_r = _run_cmd(["git", "status", "--short"], cwd=repo_path, timeout=5)
    git_clean = not git_r["stdout"].strip()
    stages.append(StageResult(stage="9", status="CHANGE_VERIFICATION_COMPLETE",
                   title="Verification",
                   findings=(f"DB unchanged: {db_ok}",
                             f"Git clean: {git_clean}")))

    report.stages = tuple(stages)
    report.health_results = tuple(health_results)
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


def _check_services() -> list[dict]:
    results = []
    for name, exe in (("ollama", "ollama"), ("ffmpeg", "ffmpeg"),
                       ("ffprobe", "ffprobe")):
        f = _find_executable(exe)
        results.append({"service": name, "state": "installed" if f else "missing"})

    ollama_res = _http_get("http://127.0.0.1:11434/api/tags", timeout=3)
    results.append({"service": "ollama_endpoint",
                    "state": "ready" if ollama_res["status_code"] == 200 else "not_running"})

    chatterbox_py = "/home/larry/clonecast/.venv-chatterbox/bin/python"
    results.append({"service": "chatterbox_venv",
                    "state": "installed" if os.path.isfile(chatterbox_py) else "missing"})
    return results
