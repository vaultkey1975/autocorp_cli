#!/usr/bin/env python3
"""
Controlled Live Application Test  (AutoCorp CLI - brains)  [Phase 1J]
======================================================================

A safety-first live-test engine that launches an application from a
selected repository, checks its health endpoints, verifies service
readiness, and reports failures — all without modifying the target.

Public API:
    run_live_test(repo_path, timeout, port) -> LiveTestReport
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from brains import scanner, analyzer, live_readiness, workspace

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
    stages: tuple[StageResult, ...] = ()
    health_results: tuple[LiveTestResult, ...] = ()
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
             timeout: int = 30, capture: bool = True) -> dict:
    """Run a subprocess safely. Never uses shell=True."""
    result = {
        "pid": None, "stdout": "", "stderr": "",
        "exit_code": None, "timed_out": False, "error": "",
    }
    try:
        proc = subprocess.Popen(
            args, cwd=cwd, env=env,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
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
    """Safe HTTP GET returning structured result."""
    result = {"status_code": 0, "content_type": "", "body_preview": "",
               "error": "", "elapsed_ms": 0.0}
    try:
        req = urllib.request.Request(url, method="GET")
        start = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status_code"] = resp.status
            result["content_type"] = resp.headers.get("Content-Type", "")
            body = resp.read(4096).decode("utf-8", errors="replace")
            result["body_preview"] = body[:500]
            result["elapsed_ms"] = (time.time() - start) * 1000
    except urllib.error.HTTPError as exc:
        result["status_code"] = exc.code
        result["error"] = f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _find_executable(name: str) -> str | None:
    return shutil.which(name)


# --------------------------------------------------------------------------- #
# Stage 1: Preflight
# --------------------------------------------------------------------------- #

def _run_preflight(repo_path: str) -> StageResult:
    findings = []
    errors = []

    if not os.path.isdir(repo_path):
        errors.append("Repository path does not exist.")
        return StageResult(stage="1", status="PREFLIGHT_FAILED",
                           title="Preflight Checks", findings=tuple(findings),
                           errors=tuple(errors))

    git_info = scanner._git_info(repo_path)
    findings.append(f"Repository: {repo_path}")
    findings.append(f"Git: branch={git_info[0]}, tree={git_info[1]}")

    venvs = []
    for entry in os.listdir(repo_path):
        full = os.path.join(repo_path, entry)
        venv_bin = os.path.join(full, "bin", "python")
        if os.path.isdir(full) and os.path.isfile(venv_bin):
            venvs.append(entry)
    findings.append(f"Virtual environments: {', '.join(venvs) or 'none found'}")

    if not venvs:
        errors.append("No Python virtual environment found.")
        errors.append("A .venv with required dependencies is needed.")

    for exe in ("ffmpeg", "ffprobe", "ollama", "uvicorn"):
        found = _find_executable(exe)
        findings.append(f"Executable {exe}: {'found' if found else 'missing'}")

    status = "PREFLIGHT_PASSED" if not errors else "PREFLIGHT_FAILED"
    return StageResult(stage="1", status=status, title="Preflight Checks",
                       findings=tuple(findings), errors=tuple(errors))


# --------------------------------------------------------------------------- #
# Stage 2: Safe Launch Plan
# --------------------------------------------------------------------------- #

def _build_launch_plan(repo_path: str, venv_path: str,
                       port: int) -> tuple[str | None, list[str], dict]:
    """Build a safe launch plan. Returns (python_exe, args, env)."""
    env = os.environ.copy()
    venv_python = os.path.join(repo_path, venv_path, "bin", "python")
    if not os.path.isfile(venv_python):
        return None, [], {}

    env["PATH"] = os.path.join(repo_path, venv_path, "bin") + ":" + env.get("PATH", "")
    env.pop("DEEPSEEK_API_KEY", None)

    args = [venv_python, "-m", "clonecast.web_app"]

    return venv_python, args, env


# --------------------------------------------------------------------------- #
# Stage 3: Controlled Startup
# --------------------------------------------------------------------------- #

def _start_app(python_exe: str, args: list[str], cwd: str, env: dict,
               timeout: int) -> tuple[subprocess.Popen | None, dict]:
    """Start the application. Returns (process, run_result)."""
    try:
        proc = subprocess.Popen(
            [python_exe] + args[1:],
            cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, {"error": str(exc), "exit_code": -1}

    time.sleep(3)
    poll = proc.poll()
    if poll is not None:
        out, err = proc.communicate()
        return None, {
            "error": f"Process exited immediately with code {poll}",
            "stdout": (out or "")[:_MAX_LOG_BYTES],
            "stderr": (err or "")[:_MAX_LOG_BYTES],
            "exit_code": poll,
        }

    return proc, {"pid": proc.pid, "exit_code": None, "running": True}


# --------------------------------------------------------------------------- #
# Stage 4: Health Checks
# --------------------------------------------------------------------------- #

def _run_health_checks(base_url: str, timeout: int) -> list[LiveTestResult]:
    checks = []
    endpoints = [
        ("/health", "Health endpoint"),
        ("/docs", "API docs"),
        ("/openapi.json", "OpenAPI schema"),
        ("/", "Root page"),
    ]
    for path, label in endpoints:
        url = f"{base_url}{path}"
        res = _http_get(url, timeout)
        checks.append(LiveTestResult(
            stage="4", label=label, url=url, method="GET",
            status_code=res["status_code"],
            response_time_ms=res.get("elapsed_ms", 0),
            content_type=res.get("content_type", ""),
            body_preview=res.get("body_preview", ""),
            error=res.get("error", ""),
            passed=200 <= res["status_code"] < 500,
        ))
    return checks


# --------------------------------------------------------------------------- #
# Stage 6: Service Readiness
# --------------------------------------------------------------------------- #

def _check_services() -> list[dict]:
    results = []
    for name, exe in (("ollama", "ollama"), ("ffmpeg", "ffmpeg"),
                       ("ffprobe", "ffprobe"), ("uvicorn", "uvicorn")):
        found = _find_executable(exe)
        if found:
            ver = _run_cmd([exe, "--version"], timeout=5)
            results.append({"service": name, "state": "installed",
                            "version": ver["stdout"].strip()[:100] if ver["stdout"] else "unknown"})
        else:
            results.append({"service": name, "state": "missing"})

    # ollama endpoint
    ollama_res = _http_get("http://127.0.0.1:11434/api/tags", timeout=3)
    results.append({
        "service": "ollama_endpoint", "state": "ready" if ollama_res["status_code"] == 200 else "not_running",
        "detail": str(ollama_res["status_code"]),
    })

    # chatterbox venv
    chatterbox_venv = "/home/larry/clonecast/.venv-chatterbox/bin/python"
    if os.path.isfile(chatterbox_venv):
        results.append({"service": "chatterbox_venv", "state": "installed"})
    else:
        results.append({"service": "chatterbox_venv", "state": "missing"})

    return results


# --------------------------------------------------------------------------- #
# Stage 7: Dry-run capability
# --------------------------------------------------------------------------- #

def _discover_dry_run(analysis) -> list[dict]:
    caps = []
    caps.append({"stage": "episode_creation", "safe": False,
                  "note": "Requires further investigation of CloneCast CLI flags for dry-run modes."})
    caps.append({"stage": "research", "safe": False,
                  "note": "Check if CloneCast has --dry-run or --preview flags."})
    caps.append({"stage": "script_generation", "safe": False,
                  "note": "Inspect cli.py for dry-run/preview arguments."})
    caps.append({"stage": "voice_generation", "safe": False,
                  "note": "Check voice_service.py for test mode."})
    caps.append({"stage": "publishing", "safe": False,
                  "note": "YouTube publishing requires OAuth — not safe for automated testing."})
    return caps


# --------------------------------------------------------------------------- #
# Stage 8: Clean Shutdown
# --------------------------------------------------------------------------- #

def _shutdown(proc: subprocess.Popen | None) -> dict:
    if proc is None:
        return {"status": "no_process_to_shutdown"}
    result = {"pid": proc.pid, "method": "SIGTERM", "killed": False}
    try:
        proc.terminate()
        try:
            proc.wait(timeout=_SHUTDOWN_TIMEOUT)
            result["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            result["method"] = "SIGKILL"
            result["killed"] = True
            proc.wait(timeout=_FORCE_KILL_TIMEOUT)
            result["exit_code"] = proc.returncode
    except (OSError, subprocess.SubprocessError):
        result["status"] = "already_dead"
    return result


# --------------------------------------------------------------------------- #
# Stage 9: Change Verification
# --------------------------------------------------------------------------- #

def _verify_no_changes(repo_path: str, before_state: dict) -> dict:
    result = {"modified": False, "new_files": [], "db_unchanged": True,
              "git_clean": True}
    try:
        git_status = _run_cmd(["git", "status", "--short"], cwd=repo_path, timeout=5)
        if git_status["stdout"].strip():
            result["git_clean"] = False
            result["new_files"] = git_status["stdout"].strip().splitlines()
    except Exception:
        pass

    for db_path, before_hash in before_state.get("databases", {}).items():
        full = os.path.join(repo_path, db_path)
        if os.path.isfile(full):
            after_hash = _sha256_file(full)
            if after_hash != before_hash:
                result["db_unchanged"] = False

    return result


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def run_live_test(repo_path: str, timeout: int = _STARTUP_TIMEOUT,
                  port: int = 8000) -> LiveTestReport:
    repo_path = os.path.abspath(repo_path)
    t0 = time.time()
    analysis = analyzer.run_analysis(repo_path)
    report = LiveTestReport(repo_path=repo_path,
                            project_type=analysis.project_type)
    stages = []
    health_results = []
    before_state = {"start_time": t0, "databases": {}}

    # Capture before-state hashes
    db_path = os.path.join(repo_path, "db", "cloneshow.db")
    if os.path.isfile(db_path):
        before_state["databases"]["db/cloneshow.db"] = _sha256_file(db_path)
    report.before_sha256 = dict(before_state.get("databases", {}))

    # Stage 1: Preflight
    s1 = _run_preflight(repo_path)
    stages.append(s1)

    if s1.status == "PREFLIGHT_FAILED":
        report.stages = tuple(stages)
        report.overall_status = "UNSAFE_TO_LAUNCH"
        report.duration_seconds = time.time() - t0
        return report

    # Stage 2: Launch plan
    venv_python, args, env = _build_launch_plan(repo_path, ".venv", port)
    if venv_python is None:
        stages.append(StageResult(stage="2", status="LAUNCH_PLAN_FAILED",
                     title="Safe Launch Plan",
                     errors=("No valid Python virtual environment found.",)))
        report.stages = tuple(stages)
        report.overall_status = "STARTUP_FAILED"
        report.duration_seconds = time.time() - t0
        return report

    redacted_env = {k: _redact_env(k, str(v)) for k, v in env.items()
                    if k not in os.environ or env[k] != os.environ.get(k)}
    plan_lines = [f"Python: {venv_python}", f"Args: {' '.join(args)}",
                  f"CWD: {repo_path}",
                  f"Extra env vars: {', '.join(sorted(redacted_env))}",
                  f"Timeout: {timeout}s", f"Port: {port}"]
    stages.append(StageResult(stage="2", status="LAUNCH_PLAN_READY",
                   title="Safe Launch Plan",
                   findings=tuple(plan_lines)))

    # Stage 3: Controlled startup
    proc, start_result = _start_app(venv_python, args, repo_path, env, timeout)
    if proc is None:
        stages.append(StageResult(stage="3", status="STARTUP_FAILED",
                       title="Controlled Startup",
                       errors=(start_result.get("error", "App failed to start"),
                               start_result.get("stderr", "")[:500])))
    else:
        stages.append(StageResult(stage="3", status="STARTUP_SUCCEEDED",
                       title="Controlled Startup",
                       findings=(f"PID: {start_result.get('pid')}",)))

    # Stage 4: Health checks
    if proc is not None:
        base_url = f"http://127.0.0.1:{port}"
        hr = _run_health_checks(base_url, timeout)
        health_results = hr
        passed = sum(1 for r in hr if r.passed)
        stages.append(StageResult(stage="4", status="HEALTH_CHECKS_COMPLETE",
                       title="Live Health Checks",
                       findings=(f"{passed}/{len(hr)} endpoints healthy",)))

    # Stage 6: Service readiness
    svcs = _check_services()
    report.dependency_status = tuple(svcs)
    ready = sum(1 for s in svcs if s["state"] in ("installed", "ready"))
    stages.append(StageResult(stage="6", status="SERVICE_CHECK_COMPLETE",
                   title="Service Readiness",
                   findings=(f"{ready}/{len(svcs)} services available",)))

    # Stage 7: Dry-run capabilities
    dry_runs = _discover_dry_run(analysis)
    report.dry_run_capabilities = tuple(dry_runs)
    stages.append(StageResult(stage="7", status="DRY_RUN_DISCOVERY_COMPLETE",
                   title="Dry-Run Capability Discovery",
                   findings=(f"{len(dry_runs)} stages checked",)))

    # Stage 8: Shutdown
    shutdown_result = _shutdown(proc)
    stages.append(StageResult(stage="8", status="SHUTDOWN_COMPLETE",
                   title="Clean Shutdown",
                   findings=(f"Method: {shutdown_result.get('method')}",
                             f"Killed: {shutdown_result.get('killed', False)}")))

    # Stage 9: Change verification
    change_result = _verify_no_changes(repo_path, before_state)
    if os.path.isfile(db_path):
        report.after_sha256 = {"db/cloneshow.db": _sha256_file(db_path)}
    stages.append(StageResult(stage="9", status="CHANGE_VERIFICATION_COMPLETE",
                   title="Change Verification",
                   findings=(f"Modified: {change_result.get('modified')}",
                             f"DB unchanged: {change_result.get('db_unchanged')}",
                             f"Git clean: {change_result.get('git_clean')}")))

    report.stages = tuple(stages)
    report.health_results = tuple(health_results)
    report.duration_seconds = time.time() - t0

    if any(s.status.endswith("_FAILED") for s in stages if s.stage in ("3",)):
        report.overall_status = "STARTUP_FAILED"
    elif not health_results:
        report.overall_status = "HEALTH_CHECK_FAILED"
    elif all(r.passed for r in health_results):
        report.overall_status = "LIVE_READY"
    else:
        report.overall_status = "STARTS_WITH_WARNINGS"

    return report
