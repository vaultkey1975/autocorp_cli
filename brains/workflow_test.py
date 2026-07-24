#!/usr/bin/env python3
"""
Disposable Workflow Test  (AutoCorp CLI - brains)  [Phase 1M-1P]
==================================================================

Runs a disposable CloneCast workflow with exact OpenAPI request body
construction. Resolves JSON and form-urlencoded schemas, handles 422
diagnostics, and stops on first genuine failure.

Requires --disposable. Never modifies production data.
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
import urllib.error
import urllib.parse
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
    content_type: str = ""
    request_body: str = ""
    response_code: int = 0
    response_body: str = ""
    extracted_id: str = ""
    failure_reason: str = ""
    validation_errors: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    db_before: str = ""
    db_after: str = ""


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


def _port_listening(host: str, port: int) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=0.5); s.close(); return True
    except (OSError, ConnectionRefusedError, TimeoutError): return False


def _http(url: str, method: str = "GET", data: dict | str = None,
          content_type: str = "application/json", timeout: int = 20) -> dict:
    r = {"status_code": 0, "body": "", "error": ""}
    try:
        body = None
        headers = {}
        if data is not None:
            if content_type == "application/x-www-form-urlencoded":
                body = urllib.parse.urlencode(data).encode()
                headers["Content-Type"] = content_type
            else:
                body = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            r["status_code"] = resp.status
            r["body"] = resp.read(_BODY_LIMIT).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        r["status_code"] = exc.code
        r["body"] = exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        r["error"] = str(exc)[:500]
    return r


def _resolve_ref(obj, components, depth=0):
    if depth > 10 or obj is None: return obj
    if isinstance(obj, dict):
        if "$ref" in obj:
            parts = obj["$ref"].split("/")
            target = components
            for p in parts[1:]:
                if isinstance(target, dict):
                    target = target.get(p, {})
            return _resolve_ref(target, components, depth + 1)
        return {k: _resolve_ref(v, components, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_ref(v, components, depth + 1) for v in obj]
    return obj


def _parse_openapi_routes(schema: dict) -> list[dict]:
    routes = []
    for path, methods in schema.get("paths", {}).items():
        if not isinstance(methods, dict): continue
        for method, spec in methods.items():
            if not isinstance(spec, dict): continue
            rb = spec.get("requestBody", {})
            content = rb.get("content", {})
            route = {
                "path": path, "method": method.upper(),
                "operation_id": spec.get("operationId", ""),
                "summary": spec.get("summary", ""),
                "tags": spec.get("tags", []),
                "has_request_body": bool(content),
                "content_types": list(content.keys()),
                "request_schema": {},
                "required_fields": [],
            }
            if content:
                ct = next(iter(content))
                route["content_type"] = ct
                raw_schema = content[ct].get("schema", {})
                resolved = _resolve_ref(raw_schema, schema)
                route["request_schema"] = resolved
                route["required_fields"] = resolved.get("required", [])
            routes.append(route)
    return routes


def _resolve_route(routes: list, keywords: list, method: str = "POST") -> dict | None:
    candidates = []
    for rt in routes:
        if rt["method"].upper() != method.upper(): continue
        path_score = sum(1 for k in keywords if k.lower() in rt["path"].lower())
        opid_score = sum(1 for k in keywords if k.lower() in rt["operation_id"].lower())
        score = path_score * 3 + opid_score
        if score > 0:
            path_segs = len(rt["path"].split("/"))
            candidates.append((score, -path_segs, rt))
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    if not candidates: return None
    best = candidates[0]
    tied = [c for c in candidates if c[0] == best[0] and c[1] == best[1]]
    if len(tied) > 1: return None
    return best[2]


def _build_body(rt: dict, defaults: dict = None) -> tuple[str, dict]:
    schema = rt.get("request_schema", {})
    ct = rt.get("content_type", "application/json")
    required = set(rt.get("required_fields", []))
    properties = schema.get("properties", {})

    body = {}
    for field, prop in properties.items():
        if field in required:
            if defaults and field in defaults:
                body[field] = defaults[field]
            elif prop.get("type") == "string":
                body[field] = "AutoCorp Disposable Test"
            elif prop.get("type") == "boolean":
                body[field] = False
            elif prop.get("type") == "integer":
                body[field] = 1
    return ct, body


def _extract_id(body: str) -> str:
    try:
        d = json.loads(body)
        for k in ("studio_id", "session_id", "plan_id", "id", "conversation_id", "job_id"):
            if k in d and d[k] is not None:
                return str(d[k])
    except: pass
    return ""


def _parse_422(body: str) -> list:
    try:
        d = json.loads(body)
        detail = d.get("detail", [])
        if isinstance(detail, list):
            return [{"loc": e.get("loc", []), "msg": e.get("msg", ""),
                      "type": e.get("type", "")}
                    for e in detail if isinstance(e, dict)]
    except: pass
    return []


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

    if scanner._git_info(repo_path)[1] != "clean":
        s = StageRecord(number=0, stage="ISOLATION_PROOF", status="FAIL",
                         failure_reason="Dirty working tree.")
        report.stages.append(s); report.overall_status = "SAFETY_BLOCKED"
        report.duration = time.time() - t0; return report

    s0 = StageRecord(number=0, stage="ISOLATION_PROOF")
    disp = tempfile.mkdtemp(prefix="autocorp-clonecast-wf-")
    report.disposable_root = disp
    disp_db = os.path.join(disp, "db", "cloneshow.db")
    os.makedirs(os.path.dirname(disp_db), exist_ok=True)
    if os.path.isfile(prod_db):
        shutil.copy2(prod_db, disp_db)
    if os.path.commonpath([disp, repo_path]).startswith(repo_path):
        s0.status = "FAIL"; s0.failure_reason = "Root outside repo."
        report.stages.append(s0); report.overall_status = "SAFETY_BLOCKED"
        report.duration = time.time() - t0; return report

    s0.status = "PASS"
    s0.evidence = [f"Root: {disp}", f"Prod DB: {prod_db}"]
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
        proc = subprocess.Popen(args, cwd=repo_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(40):
            if _port_listening("127.0.0.1", port): break
            if proc.poll() is not None:
                _, err = proc.communicate()
                s0.status = "FAIL"; s0.failure_reason = f"Server exited: {err[:500]}"
                report.stages.append(s0); report.overall_status = "STAGE_FAILED"
                report.duration = time.time() - t0; return report
            time.sleep(0.5)
    except Exception as e:
        s0.status = "FAIL"; s0.failure_reason = str(e)
        report.overall_status = "STAGE_FAILED"; report.duration = time.time() - t0
        return report

    base = f"http://127.0.0.1:{port}"
    if _http(f"{base}/health")["status_code"] != 200:
        _shutdown(proc); report.overall_status = "STAGE_FAILED"
        report.duration = time.time() - t0; return report

    time.sleep(1)
    openapi_res = _http(f"{base}/openapi.json")
    routes = []
    if openapi_res["status_code"] == 200 and openapi_res["body"]:
        try: routes = _parse_openapi_routes(json.loads(openapi_res["body"]))
        except: pass
    report.candidate_routes = routes

    def _stage(num: int, name: str, keywords: list, field_defaults: dict = None) -> StageRecord:
        s = StageRecord(number=num, stage=name)
        s.db_before = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""
        rt = _resolve_route(routes, keywords)
        if rt is None:
            s.status = "FAIL"
            s.failure_reason = f"ROUTE_RESOLUTION_AMBIGUOUS"
            report.stages.append(s); return s
        s.route = rt["path"]; s.method = rt["method"]
        s.operation_id = rt["operation_id"]

        ct, body = _build_body(rt, field_defaults)
        s.content_type = ct
        s.request_body = json.dumps(body)[:500]

        t1 = time.time()
        resp = _http(f"{base}{rt['path']}", method=rt["method"], data=body, content_type=ct)
        s.duration = time.time() - t1
        s.response_code = resp["status_code"]
        s.response_body = resp.get("body", "")[:2000]
        s.extracted_id = _extract_id(resp.get("body", ""))
        s.db_after = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""

        if resp["status_code"] == 422:
            s.validation_errors = _parse_422(resp.get("body", ""))
            s.status = "FAIL"
            s.failure_reason = f"HTTP 422: {json.dumps(s.validation_errors)[:500]}"
        elif resp["status_code"] == 0:
            s.status = "FAIL"
            s.failure_reason = f"Connection failed: {resp.get('error','')[:300]}"
        elif resp["status_code"] >= 400:
            s.status = "FAIL"
            s.failure_reason = f"HTTP {resp['status_code']}: {resp.get('body','')[:300]}"
        else:
            s.status = "PASS"
            s.evidence.append(f"Route: {s.method} {s.route}")
            s.evidence.append(f"OpID: {s.operation_id}")
            s.evidence.append(f"Content-Type: {ct}")
            if s.extracted_id:
                s.evidence.append(f"ID: {s.extracted_id}")
        report.stages.append(s)
        return s

    # Stage 2: Studio creation (form-urlencoded, required: display_name, show_format)
    s = _stage(2, "STUDIO_CREATION", ["studio", "create"],
                {"display_name": "AutoCorp Disposable Test Studio",
                 "show_format": "solo_host"})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc)
        _finalize(report, prod_db, t0); return report

    # Stage 3: Episode creation
    s = _stage(3, "EPISODE_START", ["episode", "start"],
                field_defaults=None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc)
        _finalize(report, prod_db, t0); return report
    session_id = s.extracted_id or ""
    report.overall_status = "DISPOSABLE_RECORD_FLOW_COMPLETE"

    # Stage 4: No-research mode
    s = _stage(4, "RESEARCH_MODE", ["research", "no-research"])
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc)
        _finalize(report, prod_db, t0); return report

    # Stage 5: Episode plan
    s = _stage(5, "EPISODE_PLAN", ["episode", "plan", "create"])
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc)
        _finalize(report, prod_db, t0); return report
    plan_id = s.extracted_id or ""

    # Stage 6: Assemble
    s = _stage(6, "ASSEMBLE", ["assemble", "episode"])
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
    if report.production_db_before != report.production_db_after:
        report.overall_status = "PRODUCTION_DATABASE_ACCESS_DETECTED"
    report.duration = time.time() - t0
