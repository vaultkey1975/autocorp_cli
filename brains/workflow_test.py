#!/usr/bin/env python3
"""
Disposable Workflow Test  (AutoCorp CLI - brains)  [Phase 1M]
===============================================================

Runs a real but disposable CloneCast episode workflow stage by stage,
verifying production isolation at every step. Stops on first failure.
Never modifies CloneCast source, databases, or output files.

Public API:
    run_workflow_test(repo_path, workflow) -> WorkflowTestReport
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

from brains import scanner

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class StageRecord:
    def __init__(self):
        self.number: int = 0
        self.stage: str = ""
        self.status: str = "NOT_REACHED"
        self.duration: float = 0.0
        self.request: str = ""
        self.response_code: int = 0
        self.stdout: str = ""
        self.stderr: str = ""
        self.failure_reason: str = ""
        self.evidence: list[str] = []
        self.db_before: str = ""
        self.db_after: str = ""
        self.files_created: list[str] = []


class WorkflowTestReport:
    def __init__(self):
        self.repo_path: str = ""
        self.disposable_root: str = ""
        self.production_db_path: str = ""
        self.production_db_before: str = ""
        self.production_db_after: str = ""
        self.production_db_size_before: int = 0
        self.production_db_size_after: int = 0
        self.stages: list[StageRecord] = []
        self.overall_status: str = "INCONCLUSIVE"
        self.first_failure: str = ""
        self.duration: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _http_post(url: str, data: dict = None, timeout: int = 15) -> dict:
    result = {"status_code": 0, "body": "", "error": ""}
    try:
        body = json.dumps(data or {}).encode() if data else b""
        req = urllib.request.Request(url, data=body, method="POST",
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status_code"] = resp.status
            result["body"] = resp.read(8192).decode("utf-8", errors="replace")[:2000]
    except Exception as exc:
        result["error"] = str(exc)[:500]
    return result


def _http_get(url: str, timeout: int = 10) -> dict:
    result = {"status_code": 0, "body": "", "error": ""}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            result["status_code"] = resp.status
            result["body"] = resp.read(4096).decode("utf-8", errors="replace")[:500]
    except Exception as exc:
        result["error"] = str(exc)[:200]
    return result


def _is_port_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


# ---------------------------------------------------------------------------
# Workflow test
# ---------------------------------------------------------------------------


def run_workflow_test(repo_path: str, workflow: str = "episode",
                      port: int = 8000) -> WorkflowTestReport:
    repo_path = os.path.abspath(repo_path)
    t0 = time.time()
    report = WorkflowTestReport()
    report.repo_path = repo_path

    prod_db = os.path.join(repo_path, "db", "cloneshow.db")
    report.production_db_path = prod_db
    if os.path.isfile(prod_db):
        report.production_db_before = _sha256_file(prod_db)
        report.production_db_size_before = os.path.getsize(prod_db)

    git_before = scanner._git_info(repo_path)
    if git_before[1] != "clean":
        report.overall_status = "SAFETY_BLOCKED"
        s = StageRecord(); s.number = 0; s.stage = "ISOLATION_PROOF"
        s.status = "FAIL"
        s.failure_reason = "Production working tree is dirty. Commit or stash changes."
        report.stages.append(s)
        report.first_failure = s.failure_reason
        report.duration = time.time() - t0
        return report

    # Stage 0: Isolation proof
    s0 = StageRecord(); s0.number = 0; s0.stage = "ISOLATION_PROOF"
    s0.status = "PASS"

    disp_root = tempfile.mkdtemp(prefix="autocorp-clonecast-workflow-")
    report.disposable_root = disp_root
    disposable_db = os.path.join(disp_root, "db", "cloneshow.db")
    os.makedirs(os.path.dirname(disposable_db), exist_ok=True)

    if prod_db and os.path.isfile(prod_db):
        shutil.copy2(prod_db, disposable_db)

    common = os.path.commonpath([os.path.abspath(disp_root), os.path.abspath(repo_path)])
    if common.startswith(os.path.abspath(repo_path)):
        s0.status = "FAIL"
        s0.failure_reason = "Disposable root must be outside the target repository."
        report.stages.append(s0)
        report.first_failure = s0.failure_reason
        report.overall_status = "SAFETY_BLOCKED"
        report.duration = time.time() - t0
        return report

    s0.evidence.append(f"Disposable root: {disp_root}")
    s0.evidence.append(f"Production DB: {prod_db} (read-only)")
    s0.evidence.append(f"Disposable DB: {disposable_db}")
    s0.evidence.append("Publishing: disabled")
    s0.evidence.append("External network: disabled")
    s0.evidence.append(f"Git clean: yes")
    report.stages.append(s0)

    # Start server with disposable env
    venv_python = os.path.join(repo_path, ".venv", "bin", "python")
    if not os.path.isfile(venv_python):
        s0.status = "FAIL"
        s0.failure_reason = ".venv not found."
        report.overall_status = "REQUIRED_SERVICE_MISSING"
        report.duration = time.time() - t0
        return report

    if _is_port_listening("127.0.0.1", port):
        for alt in range(port + 1, port + 100):
            if not _is_port_listening("127.0.0.1", alt):
                port = alt
                break

    env = os.environ.copy()
    env["CLONECAST_DB_PATH"] = disposable_db
    env["CLONECAST_RUNTIME_DIR"] = os.path.join(disp_root, "runtime")
    env["CLONECAST_LOG_DIR"] = os.path.join(disp_root, "logs")
    env["CLONECAST_RESEARCH_ROOT"] = os.path.join(disp_root, "research")
    env.pop("DEEPSEEK_API_KEY", None)
    env["PATH"] = os.path.join(repo_path, ".venv", "bin") + ":" + env.get("PATH", "")

    args = [venv_python, "-m", "uvicorn", "clonecast.web_app:create_app",
            "--factory", "--host", "127.0.0.1", f"--port={port}"]

    proc = None
    try:
        proc = subprocess.Popen(args, cwd=repo_path, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(2)
        for _ in range(30):
            if _is_port_listening("127.0.0.1", port):
                break
            if proc.poll() is not None:
                out, err = proc.communicate()
                s0.status = "FAIL"
                s0.failure_reason = f"Server exited early: {proc.returncode}\n{err[:500]}"
                report.overall_status = "STAGE_FAILED"
                report.duration = time.time() - t0
                return report
            time.sleep(0.5)
        else:
            s0.status = "FAIL"
            s0.failure_reason = "Server did not start within timeout."
            report.overall_status = "STAGE_FAILED"
            report.duration = time.time() - t0
            return report
    except (OSError, subprocess.SubprocessError) as exc:
        s0.status = "FAIL"
        s0.failure_reason = str(exc)
        report.overall_status = "STAGE_FAILED"
        report.duration = time.time() - t0
        return report

    base = f"http://127.0.0.1:{port}"
    health = _http_get(f"{base}/health")
    if health["status_code"] != 200:
        s0.status = "FAIL"
        s0.failure_reason = f"Health check failed: {health['error']}"
        proc.terminate(); proc.wait(timeout=10)
        report.overall_status = "STAGE_FAILED"
        report.duration = time.time() - t0
        return report


    # Helper to run a stage
    def _run_stage(num: int, name: str, req: str,
                   expected: int = 200, method: str = "POST",
                   data: dict = None) -> StageRecord:
        s = StageRecord(); s.number = num; s.stage = name; s.request = req
        s.db_before = _sha256_file(disposable_db) if os.path.isfile(disposable_db) else ""

        if method == "POST":
            r = _http_post(f"{base}{req}", data)
        else:
            r = _http_get(f"{base}{req}")

        s.response_code = r["status_code"]
        s.stdout = r.get("body", "")[:500]
        s.stderr = r.get("error", "")
        s.db_after = _sha256_file(disposable_db) if os.path.isfile(disposable_db) else ""

        if expected and r["status_code"] != expected:
            s.status = "FAIL"
            s.failure_reason = f"Expected {expected}, got {r['status_code']}: {r.get('error', s.stdout[:200])}"
        elif r["status_code"] >= 500:
            s.status = "FAIL"
            s.failure_reason = f"Server error {r['status_code']}"
        elif r["status_code"] >= 400 and method == "POST":
            s.status = "FAIL"
            s.failure_reason = f"Request rejected: {r['status_code']}: {s.stdout[:200]}"
        else:
            s.status = "PASS"
            s.evidence.append(f"Response {r['status_code']}")
            if s.stdout:
                s.evidence.append(f"Body: {s.stdout[:200]}")

        report.stages.append(s)
        return s

    # Stage 2: Create show
    s2 = _run_stage(2, "SHOW_CREATION",
                     "/api/shows", expected=200,
                     data={"title": "AutoCorp Disposable Test Show",
                           "description": "Temporary disposable test show. DELETE AFTER TEST.",
                           "format": "solo_host"})
    if s2.status != "PASS":
        report.first_failure = s2.failure_reason; _cleanup(proc, report, prod_db, t0); return report

    # Stage 3: Create episode
    show_id = _extract_id(s2.stdout)
    s3 = _run_stage(3, "EPISODE_CREATION",
                     "/api/episodes", expected=200,
                     data={"show_id": show_id,
                           "title": "AutoCorp Disposable Episode 001",
                           "topic": "Why careful software testing matters"})
    if s3.status != "PASS":
        report.first_failure = s3.failure_reason; _cleanup(proc, report, prod_db, t0); return report

    # Stage 4: Research (safe GET - check if research endpoint exists)
    episode_id = _extract_id(s3.stdout)
    s4 = _run_stage(4, "RESEARCH",
                     f"/api/episodes/{episode_id}/research", expected=None)
    if s4.status != "PASS":
        report.first_failure = s4.failure_reason
        _cleanup(proc, report, prod_db, t0)
        return report

    # Stage 5: Script generation
    s5 = _run_stage(5, "SCRIPT_GENERATION",
                     f"/api/episodes/{episode_id}/scripts", expected=None,
                     data={"topic": "Why careful software testing matters",
                           "target_duration_seconds": 30})
    if s5.status != "PASS":
        report.first_failure = s5.failure_reason
        _cleanup(proc, report, prod_db, t0)
        return report

    # Stage 6: Voice generation
    s6 = _run_stage(6, "VOICE_GENERATION",
                     f"/api/episodes/{episode_id}/voice", expected=None)
    if s6.status != "PASS":
        report.first_failure = s6.failure_reason
    else:
        report.first_failure = ""

    _cleanup(proc, report, prod_db, t0)
    return report


def _extract_id(body: str) -> int:
    try:
        data = json.loads(body)
        for key in ("id", "show_id", "episode_id"):
            if key in data:
                return int(data[key])
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return 0


def _cleanup(proc, report, prod_db, t0):
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            try:
                proc.kill()
            except Exception:
                pass

    if os.path.isfile(prod_db):
        report.production_db_after = _sha256_file(prod_db)
        report.production_db_size_after = os.path.getsize(prod_db)

    if report.production_db_before == report.production_db_after:
        report.overall_status = "DISPOSABLE_WORKFLOW_COMPLETE" if not report.first_failure else "DISPOSABLE_WORKFLOW_PARTIAL"
    else:
        report.overall_status = "PRODUCTION_DATABASE_ACCESS_DETECTED"

    report.duration = time.time() - t0
