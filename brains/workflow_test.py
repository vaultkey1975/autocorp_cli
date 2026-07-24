#!/usr/bin/env python3
"""
Disposable Workflow Test  (AutoCorp CLI - brains)  [Phase 1M-1N]
==================================================================

Runs a disposable CloneCast workflow stage by stage using routes resolved
from the live OpenAPI schema. Requires --disposable. Never modifies
production data. Stops on first failure.

Public API:
    run_workflow_test(repo_path, workflow) -> WorkflowTestReport
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field

from brains import scanner


@dataclass
class StageRecord:
    number: int = 0
    stage: str = ""
    status: str = "NOT_REACHED"
    duration: float = 0.0
    route: str = ""
    method: str = ""
    request_body: str = ""
    response_code: int = 0
    response_body: str = ""
    failure_reason: str = ""
    evidence: list = field(default_factory=list)
    db_before: str = ""
    db_after: str = ""


class WorkflowTestReport:
    def __init__(self):
        self.repo_path = self.disposable_root = self.production_db_path = ""
        self.production_db_before = self.production_db_after = ""
        self.production_db_size_before = self.production_db_size_after = 0
        self.stages: list[StageRecord] = []
        self.candidate_routes: list[dict] = []
        self.overall_status = "INCONCLUSIVE"
        self.first_failure = ""
        self.duration = 0.0


def _sha256_file(p: str) -> str:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(65536), b""): h.update(c)
        return h.hexdigest()
    except OSError: return ""


def _http(url: str, method: str = "GET", data: dict = None, timeout: int = 15) -> dict:
    r = {"status_code": 0, "body": "", "error": ""}
    try:
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method,
                                      headers={"Content-Type": "application/json"} if body else {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            r["status_code"] = resp.status
            r["body"] = resp.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
    except Exception as e:
        r["error"] = str(e)[:500]
    return r


def _port_listening(host: str, port: int) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=0.5); s.close(); return True
    except (OSError, ConnectionRefusedError, TimeoutError): return False


def _resolve_route(routes: list, keywords: list, method: str = "POST") -> dict | None:
    candidates = []
    for rt in routes:
        opid = rt.get("operation_id", "")
        path = rt.get("path", "")
        m = rt.get("method", "")
        if m.upper() != method.upper():
            continue
        score = sum(1 for k in keywords if k.lower() in opid.lower() or k.lower() in path.lower())
        if score > 0:
            candidates.append((score, rt))
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1] if candidates else None


def _parse_openapi_routes(schema: dict) -> list[dict]:
    routes = []
    for path, methods in schema.get("paths", {}).items():
        if not isinstance(methods, dict): continue
        for method, spec in methods.items():
            if not isinstance(spec, dict): continue
            rb = spec.get("requestBody", {})
            routes.append({
                "path": path, "method": method.upper(),
                "operation_id": spec.get("operationId", ""),
                "summary": spec.get("summary", ""),
                "tags": spec.get("tags", []),
                "has_request_body": "requestBody" in spec,
                "request_schema_ref": _resolve_ref(rb),
                "parameters": [p.get("name", "") for p in spec.get("parameters", []) if isinstance(p, dict)],
            })
    return routes


def _resolve_ref(obj, depth=0) -> str:
    if depth > 5: return ""
    if isinstance(obj, dict):
        for k in ("$ref",):
            if k in obj: return obj[k]
        for v in obj.values():
            r = _resolve_ref(v, depth + 1)
            if r: return r
    return ""


def _extract_id(body: str) -> str:
    try:
        d = json.loads(body)
        for k in ("studio_id", "session_id", "plan_id", "id", "conversation_id"):
            if k in d and d[k] is not None:
                return str(d[k])
    except: pass
    return ""


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

    git_info = scanner._git_info(repo_path)
    if git_info[1] != "clean":
        s = StageRecord(number=0, stage="ISOLATION_PROOF", status="FAIL",
                         failure_reason="Working tree is dirty.")
        report.stages.append(s); report.first_failure = s.failure_reason
        report.overall_status = "SAFETY_BLOCKED"; report.duration = time.time() - t0
        return report

    s0 = StageRecord(number=0, stage="ISOLATION_PROOF")
    disp = tempfile.mkdtemp(prefix="autocorp-clonecast-wf-")
    report.disposable_root = disp
    disp_db = os.path.join(disp, "db", "cloneshow.db")
    os.makedirs(os.path.dirname(disp_db), exist_ok=True)

    if os.path.isfile(prod_db):
        shutil.copy2(prod_db, disp_db)
    common = os.path.commonpath([os.path.abspath(disp), os.path.abspath(repo_path)])
    if common.startswith(os.path.abspath(repo_path)):
        s0.status = "FAIL"; s0.failure_reason = "Disposable root must be outside target."
        report.stages.append(s0); report.first_failure = s0.failure_reason
        report.overall_status = "SAFETY_BLOCKED"; report.duration = time.time() - t0
        return report

    s0.status = "PASS"
    s0.evidence = [f"Disposable root: {disp}", f"Prod DB: {prod_db} (read-only)",
                   f"Disposable DB: {disp_db}", "Publishing: disabled"]
    report.stages.append(s0)

    venv = os.path.join(repo_path, ".venv", "bin", "python")
    if not os.path.isfile(venv):
        s0.status = "FAIL"; s0.failure_reason = ".venv not found."
        report.overall_status = "REQUIRED_SERVICE_MISSING"; report.duration = time.time() - t0
        return report

    if _port_listening("127.0.0.1", port):
        for alt in range(port + 1, port + 100):
            if not _port_listening("127.0.0.1", alt): port = alt; break

    env = os.environ.copy()
    env["CLONECAST_DB_PATH"] = disp_db
    env["CLONECAST_RUNTIME_DIR"] = os.path.join(disp, "runtime")
    env["CLONECAST_LOG_DIR"] = os.path.join(disp, "logs")
    env["PATH"] = os.path.join(repo_path, ".venv", "bin") + ":" + env.get("PATH", "")
    env.pop("DEEPSEEK_API_KEY", None)

    args = [venv, "-m", "uvicorn", "clonecast.web_app:create_app",
            "--factory", "--host", "127.0.0.1", f"--port={port}"]
    proc = None
    try:
        proc = subprocess.Popen(args, cwd=repo_path, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(40):
            if _port_listening("127.0.0.1", port): break
            if proc.poll() is not None:
                _, err = proc.communicate()
                s0.status = "FAIL"; s0.failure_reason = f"Server exited: {err[:500]}"
                report.overall_status = "STAGE_FAILED"; report.duration = time.time() - t0
                return report
            time.sleep(0.5)
    except Exception as e:
        s0.status = "FAIL"; s0.failure_reason = str(e)
        report.overall_status = "STAGE_FAILED"; report.duration = time.time() - t0
        return report

    base = f"http://127.0.0.1:{port}"
    health = _http(f"{base}/health")
    if health["status_code"] != 200:
        s0.status = "FAIL"; s0.failure_reason = f"Health: {health['error']}"
        _shutdown(proc); report.overall_status = "STAGE_FAILED"
        report.duration = time.time() - t0; return report

    # Retrieve and parse OpenAPI
    openapi_res = _http(f"{base}/openapi.json", timeout=15)
    routes = []
    if openapi_res["status_code"] == 200:
        try:
            schema = json.loads(openapi_res["body"])
            routes = _parse_openapi_routes(schema)
        except json.JSONDecodeError: pass
    report.candidate_routes = [dict(r) for r in routes]

    def run_stage(num: int, name: str, keywords: list, body: dict = None,
                  expected: int = None) -> StageRecord:
        s = StageRecord(number=num, stage=name)
        s.db_before = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""
        rt = _resolve_route(routes, keywords)

        if rt is None:
            s.status = "FAIL"; s.failure_reason = f"ROUTE_NOT_FOUND for keywords: {keywords}"
            report.stages.append(s); return s

        s.route = rt["path"]; s.method = rt["method"]
        if body:
            s.request_body = json.dumps(body)[:500]

        t1 = time.time()
        resp = _http(f"{base}{rt['path']}", method=rt["method"], data=body)
        s.duration = time.time() - t1
        s.response_code = resp["status_code"]
        s.response_body = resp.get("body", "")[:500]
        s.db_after = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""

        if resp["status_code"] == 0:
            s.status = "FAIL"
            s.failure_reason = f"Connection failed: {resp.get('error', 'no response')[:300]}"
        elif expected and resp["status_code"] != expected:
            s.status = "FAIL"
            s.failure_reason = f"Expected {expected}, got {resp['status_code']}: {resp.get('error', resp.get('body','')[:200])}"
        elif resp["status_code"] >= 500:
            s.status = "FAIL"; s.failure_reason = f"Server error {resp['status_code']}"
        elif 400 <= resp["status_code"] < 500:
            s.status = "FAIL"; s.failure_reason = f"HTTP {resp['status_code']}: {resp.get('body','')[:200]}"
        else:
            s.status = "PASS"
            s.evidence = [f"Route: {rt['path']}", f"Response: {resp['status_code']}"]
            extracted = _extract_id(resp.get("body", ""))
            if extracted:
                s.evidence.append(f"ID: {extracted}")
        report.stages.append(s)
        return s

    # Stage 2: Create studio (show)
    s = run_stage(2, "STUDIO_CREATION", ["studio", "create"],
                   {"studio_name": "AutoCorp Disposable Test Studio",
                    "description": "Temporary test studio. DELETE AFTER TEST."})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report
    studio_id = _extract_id(s.response_body)

    # Stage 3: Create episode
    s = run_stage(3, "EPISODE_CREATION", ["episode", "start"],
                   {"studio_id": studio_id,
                    "episode_title": "AutoCorp Disposable Episode 001",
                    "topic": "Why careful software testing matters"})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report
    session_id = _extract_id(s.response_body)

    # Stage 4: Research via switch-to-no-research
    s = run_stage(4, "RESEARCH_MODE", ["research", "no-research", "switch"],
                   {"session_id": session_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report

    # Stage 5: Create episode plan
    s = run_stage(5, "EPISODE_PLAN", ["episode", "plan", "create"],
                   body={"session_id": session_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report
    plan_id = _extract_id(s.response_body)

    # Stage 6: Assemble
    s = run_stage(6, "ASSEMBLE", ["assemble", "episode"],
                   body={"plan_id": plan_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason
    else:
        report.first_failure = ""

    _shutdown(proc)
    _finalize(report, prod_db, t0)
    return report


def _shutdown(proc):
    if proc:
        try: proc.terminate(); proc.wait(timeout=10)
        except: pass


def _finalize(report, prod_db, t0):
    if os.path.isfile(prod_db):
        report.production_db_after = _sha256_file(prod_db)
        report.production_db_size_after = os.path.getsize(prod_db)
    if report.production_db_before == report.production_db_after:
        report.overall_status = ("DISPOSABLE_WORKFLOW_COMPLETE" if not report.first_failure
                                  else "DISPOSABLE_WORKFLOW_PARTIAL")
    else:
        report.overall_status = "PRODUCTION_DATABASE_ACCESS_DETECTED"
    report.duration = time.time() - t0
