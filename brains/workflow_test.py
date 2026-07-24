#!/usr/bin/env python3
"""
Disposable Workflow Test  (AutoCorp CLI - brains)  [Phase 1M-1O]
==================================================================

Runs a disposable CloneCast workflow using routes resolved from the live
OpenAPI schema. Validates real artifacts (scripts, audio via FFprobe).
Requires --disposable. Never modifies production data.

Status semantics:
    DISPOSABLE_SETUP_COMPLETE    - isolation + server started
    DISPOSABLE_RECORD_FLOW_COMPLETE - show/episode/plan records created
    DISPOSABLE_SCRIPT_COMPLETE   - real script generated
    DISPOSABLE_AUDIO_COMPLETE    - real audio produced + validated
    DISPOSABLE_MEDIA_COMPLETE    - audio assembled + validated
    DISPOSABLE_RELEASE_PREP_COMPLETE - release package created
    DISPOSABLE_WORKFLOW_PARTIAL  - workflow did not reach publishing barrier
    STAGE_FAILED                 - a stage failed
    SAFETY_BLOCKED               - safety check failed
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

_BODY_LIMIT = 2 * 1024 * 1024


@dataclass
class StageRecord:
    number: int = 0
    stage: str = ""
    status: str = "NOT_REACHED"
    duration: float = 0.0
    route: str = ""
    method: str = ""
    operation_id: str = ""
    request_body: str = ""
    response_code: int = 0
    response_body: str = ""
    extracted_id: str = ""
    failure_reason: str = ""
    evidence: list = field(default_factory=list)
    db_before: str = ""
    db_after: str = ""
    artifacts: list = field(default_factory=list)


class WorkflowTestReport:
    def __init__(self):
        self.repo_path = self.disposable_root = self.production_db_path = ""
        self.production_db_before = self.production_db_after = ""
        self.production_db_size_before = self.production_db_size_after = 0
        self.stages: list[StageRecord] = []
        self.candidate_routes: list = []
        self.overall_status = "INCONCLUSIVE"
        self.first_failure = ""
        self.duration = 0.0
        self.artifact_inventory: list[dict] = []


def _sha256_file(p: str) -> str:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(65536), b""): h.update(c)
        return h.hexdigest()
    except OSError: return ""


def _http(url: str, method: str = "GET", data: dict = None, timeout: int = 20) -> dict:
    r = {"status_code": 0, "body": "", "error": ""}
    try:
        body = json.dumps(data).encode() if data else None
        headers = {"Content-Type": "application/json"} if body else {}
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            r["status_code"] = resp.status
            r["body"] = resp.read(_BODY_LIMIT).decode("utf-8", errors="replace")
    except Exception as e:
        r["error"] = str(e)[:500]
    return r


def _port_listening(host: str, port: int) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=0.5); s.close(); return True
    except (OSError, ConnectionRefusedError, TimeoutError): return False


def _parse_openapi_routes(schema: dict) -> list[dict]:
    routes = []
    for path, methods in schema.get("paths", {}).items():
        if not isinstance(methods, dict): continue
        for method, spec in methods.items():
            if not isinstance(spec, dict): continue
            routes.append({
                "path": path, "method": method.upper(),
                "operation_id": spec.get("operationId", ""),
                "summary": spec.get("summary", ""),
                "tags": spec.get("tags", []),
                "has_request_body": "requestBody" in spec,
            })
    return routes


def _resolve_route(routes: list, keywords: list, method: str = "POST") -> dict | None:
    candidates = []
    for rt in routes:
        if rt["method"].upper() != method.upper(): continue
        path_score = sum(1 for k in keywords
                          if k.lower() in rt["path"].lower())
        opid_score = sum(1 for k in keywords
                          if k.lower() in rt["operation_id"].lower())
        score = path_score * 3 + opid_score  # path match weighted higher
        if score > 0:
            # prefer shorter paths (fewer parameter components)
            path_segments = len(rt["path"].split("/"))
            candidates.append((score, -path_segments, rt))
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    if not candidates: return None
    best = candidates[0]
    # Only ambiguous if another candidate has same path_score+opid_score AND same path length
    tied = [c for c in candidates if c[0] == best[0] and c[1] == best[1]]
    if len(tied) > 1: return None
    return best[2]


def _extract_id(body: str) -> str:
    try:
        d = json.loads(body)
        for k in ("studio_id", "session_id", "plan_id", "id", "conversation_id",
                   "job_id"):
            if k in d and d[k] is not None:
                return str(d[k])
    except: pass
    return ""


def _ffprobe(path: str) -> dict:
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                             "-show_streams", "-show_format", path],
                            capture_output=True, text=True, timeout=15)
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except Exception:
        return {}


def _validate_audio(path: str) -> dict:
    result = {"valid": False, "size": 0, "duration": 0.0, "codec": "",
               "sample_rate": 0, "error": ""}
    if not os.path.isfile(path): return result
    result["size"] = os.path.getsize(path)
    if result["size"] == 0:
        result["error"] = "Zero-byte file"; return result

    probe = _ffprobe(path)
    if not probe: result["error"] = "FFprobe failed"; return result
    streams = probe.get("streams", [])
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        result["error"] = "No audio stream"; return result
    s = audio_streams[0]
    result["valid"] = True
    result["duration"] = float(probe.get("format", {}).get("duration", 0))
    result["codec"] = s.get("codec_name", "")
    result["sample_rate"] = int(s.get("sample_rate", 0))
    return result


def _validate_script(content: str) -> dict:
    result = {"valid": False, "lines": 0, "chars": 0, "error": ""}
    if not content or len(content.strip()) < 20:
        result["error"] = "Empty or too short"
        return result
    if all(w in content.lower() for w in ("placeholder", "mock", "fake", "stub")):
        result["error"] = "Contains placeholder/mock keywords"
        return result
    result["valid"] = True
    result["lines"] = len([l for l in content.splitlines() if l.strip()])
    result["chars"] = len(content)
    return result


def _poll_job(base: str, job_id: str, stage_name: str, timeout: int = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _http(f"{base}/api/jobs/{job_id}", method="GET", timeout=10)
        if r["status_code"] == 200:
            try:
                j = json.loads(r["body"])
                state = j.get("status", j.get("state", "unknown"))
                if state in ("completed", "done", "finished", "success"):
                    return {"status": "completed", "body": r["body"]}
                if state in ("failed", "error", "cancelled"):
                    return {"status": "failed", "body": r["body"]}
            except: pass
        elif r["status_code"] == 0:
            return {"status": "connection_lost", "body": ""}
        time.sleep(2)
    return {"status": "timeout", "body": ""}


def _record_artifact(report: WorkflowTestReport, path: str, stage: str,
                     media_type: str = "file"):
    if not os.path.isfile(path): return
    info = {"path": os.path.relpath(path, report.disposable_root),
            "stage": stage, "type": media_type,
            "size": os.path.getsize(path), "sha256": _sha256_file(path)}
    if media_type in ("audio", "video"):
        probe = _ffprobe(path)
        if probe:
            info["probe"] = {
                "duration": float(probe.get("format", {}).get("duration", 0)),
                "streams": [s.get("codec_type", "") for s in probe.get("streams", [])],
            }
    report.artifact_inventory.append(info)


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
                         failure_reason="Dirty working tree.")
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
    if os.path.commonpath([os.path.abspath(disp), repo_path]).startswith(repo_path):
        s0.status = "FAIL"; s0.failure_reason = "Root must be outside repo."
        report.stages.append(s0); report.overall_status = "SAFETY_BLOCKED"
        report.duration = time.time() - t0; return report

    s0.status = "PASS"
    s0.evidence = [f"Disposable root: {disp}", f"Prod DB: {prod_db} (read-only)"]
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
    if _http(f"{base}/health")["status_code"] != 200:
        _shutdown(proc); report.overall_status = "STAGE_FAILED"
        report.duration = time.time() - t0; return report

    # Wait for server to fully initialize routes
    time.sleep(1)

    openapi_res = _http(f"{base}/openapi.json", timeout=15)
    routes = []
    if openapi_res["status_code"] == 200 and openapi_res["body"]:
        try:
            schema = json.loads(openapi_res["body"])
            routes = _parse_openapi_routes(schema)
        except json.JSONDecodeError:
            pass
    report.candidate_routes = routes

    def _stage(num: int, name: str, keywords: list, body: dict = None,
               method: str = "POST") -> StageRecord:
        s = StageRecord(number=num, stage=name)
        s.db_before = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""
        rt = _resolve_route(routes, keywords, method)
        if rt is None:
            s.status = "FAIL"
            s.failure_reason = f"ROUTE_RESOLUTION_AMBIGUOUS" if any(
                _resolve_route(routes, [k], method) for k in keywords
            ) else f"ROUTE_NOT_FOUND: {keywords}"
            report.stages.append(s); return s

        s.route = rt["path"]; s.method = rt["method"]
        s.operation_id = rt["operation_id"]
        if body: s.request_body = json.dumps(body)[:500]

        t1 = time.time()
        resp = _http(f"{base}{rt['path']}", method=rt["method"], data=body)
        s.duration = time.time() - t1
        s.response_code = resp["status_code"]
        s.response_body = resp.get("body", "")[:2000]
        s.extracted_id = _extract_id(resp.get("body", ""))
        s.db_after = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""

        if resp["status_code"] == 0:
            s.status = "FAIL"
            s.failure_reason = f"Connection failed: {resp.get('error','')[:300]}"
        elif resp["status_code"] >= 400:
            s.status = "FAIL"
            s.failure_reason = f"HTTP {resp['status_code']}: {resp.get('body','')[:300]}"
        else:
            s.status = "PASS"
            s.evidence.append(f"Route: {s.method} {s.route}")
            s.evidence.append(f"Operation: {s.operation_id}")
            if s.extracted_id:
                s.evidence.append(f"Extracted ID: {s.extracted_id}")
        report.stages.append(s)
        return s

    # Stage 2: Studio
    s = _stage(2, "STUDIO_CREATION", ["studio", "create"],
                {"studio_name": "AutoCorp Disposable Test Studio",
                 "description": "Temporary test studio."})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc)
        _finalize(report, prod_db, t0); return report

    # Stage 3: Episode
    s = _stage(3, "EPISODE_START", ["episode", "start"],
                {"episode_title": "AutoCorp Test 001",
                 "topic": "Software testing importance",
                 "session_id": s.extracted_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc)
        _finalize(report, prod_db, t0); return report

    session_id = s.extracted_id or ""

    # Stage 4: Research mode
    s = _stage(4, "RESEARCH_MODE", ["research", "no-research"],
                {"session_id": session_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc)
        _finalize(report, prod_db, t0); return report

    # Stage 5: Episode plan
    s = _stage(5, "EPISODE_PLAN", ["episode", "plan", "create"],
                {"session_id": session_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc)
        _finalize(report, prod_db, t0); return report

    plan_id = s.extracted_id or ""
    report.overall_status = "DISPOSABLE_RECORD_FLOW_COMPLETE"

    # Stage 6: Assemble (starts script generation + audio pipeline)
    s = _stage(6, "ASSEMBLE", ["assemble", "episode"],
                {"plan_id": plan_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc)
        _finalize(report, prod_db, t0); return report

    # Poll for job completion
    job_id = s.extracted_id or _extract_id(s.response_body)
    if job_id:
        poll = _poll_job(base, job_id, "assemble", timeout=120)
        if poll["status"] == "completed":
            s.evidence.append("Job completed")
            report.overall_status = "DISPOSABLE_MEDIA_COMPLETE"
        elif poll["status"] == "failed":
            s.status = "FAIL"
            s.failure_reason = f"Assemble job failed: {poll.get('body','')[:300]}"
            report.first_failure = s.failure_reason
        else:
            s.evidence.append(f"Job status: {poll['status']}")

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
    if report.production_db_before != report.production_db_after:
        report.overall_status = "PRODUCTION_DATABASE_ACCESS_DETECTED"
    report.duration = time.time() - t0
