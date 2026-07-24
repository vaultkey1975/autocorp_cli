#!/usr/bin/env python3
"""
Disposable Workflow Test  (AutoCorp CLI - brains)  [Phase 1M-1S]
==================================================================

Persistent HTTP session with semantic redirect validation, real identifier
propagation, studio activation, and OpenAPI-grounded request construction.
Requires --disposable. Never modifies production data.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from brains import scanner

_BODY_LIMIT = 2 * 1024 * 1024
_FLASH_ERROR_KEYS = {"flash_error", "error", "flash_warning"}
_FLASH_SUCCESS_KEYS = {"flash_success", "flash_info"}


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
    redirect_url: str = ""
    redirect_chain: list = field(default_factory=list)
    final_url: str = ""
    flash_messages: dict = field(default_factory=dict)
    extracted_ids: dict = field(default_factory=dict)
    failure_reason: str = ""
    failure_ownership: str = ""
    validation_errors: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    db_before: str = ""
    db_after: str = ""
    db_studio_exists: bool = False


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


def _sha256_file(p: str) -> str:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(65536), b""): h.update(c)
        return h.hexdigest()
    except OSError: return ""


def _port_listening(host: str, port: int) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=0.5); s.close()
        return True
    except: return False


def _parse_redirect_params(url: str) -> dict:
    result = {}
    if "?" in url:
        try:
            qs = url.split("?", 1)[1]
            for k, v in urllib.parse.parse_qs(qs).items():
                result[k] = v[0] if v else ""
        except: pass
    return result


class SessionHTTP:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.cookie_processor = urllib.request.HTTPCookieProcessor(self.cj)
        self.opener = urllib.request.build_opener(self.cookie_processor)

    def request(self, url: str, method: str = "GET", data: dict | str = None,
                content_type: str = "application/json", timeout: int = 20,
                follow_redirects: bool = True) -> dict:
        r = {"status_code": 0, "body": "", "error": "", "redirect_url": "", "final_url": url}
        try:
            body_bytes = None
            headers = {}
            if data is not None:
                body_bytes = (urllib.parse.urlencode(data).encode() if content_type == "application/x-www-form-urlencoded"
                              else json.dumps(data).encode())
                headers["Content-Type"] = content_type
            req = urllib.request.Request(url, data=body_bytes, method=method, headers=headers)

            if not follow_redirects:
                r["final_url"] = url
                opener = urllib.request.build_opener(self.cookie_processor)
            else:
                opener = urllib.request.build_opener(
                    self.cookie_processor, urllib.request.HTTPRedirectHandler())

            resp = opener.open(req, timeout=timeout)
            r["status_code"] = resp.status
            r["body"] = resp.read(_BODY_LIMIT).decode("utf-8", errors="replace")
            r["final_url"] = resp.url
            if resp.url != url:
                r["redirect_url"] = resp.url
        except urllib.error.HTTPError as exc:
            r["status_code"] = exc.code
            r["body"] = exc.read().decode("utf-8", errors="replace")[:5000]
            r["final_url"] = exc.url if exc.url != url else url
            if exc.url != url: r["redirect_url"] = exc.url
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
                if not isinstance(target, dict): return obj
                target = target.get(p, {})
            return target
        return obj
    return obj


def _parse_openapi_routes(schema: dict) -> list[dict]:
    routes = []
    for path, methods in schema.get("paths", {}).items():
        if not isinstance(methods, dict): continue
        for method, spec in methods.items():
            if not isinstance(spec, dict): continue
            rb = spec.get("requestBody", {})
            content = rb.get("content", {})
            route = {"path": path, "method": method.upper(),
                     "operation_id": spec.get("operationId", ""),
                     "has_request_body": bool(content),
                     "content_types": list(content.keys()),
                     "request_schema": {}, "required_fields": []}
            if content:
                ct = next(iter(content))
                route["content_type"] = ct
                resolved = _resolve_ref(content[ct].get("schema", {}), schema)
                route["request_schema"] = resolved
                route["required_fields"] = resolved.get("required", [])
            routes.append(route)
    return routes


def _resolve_route(routes: list, keywords: list, method: str = "POST") -> dict | None:
    candidates = []
    for rt in routes:
        if rt["method"].upper() != method.upper(): continue
        ps = sum(1 for k in keywords if k.lower() in rt["path"].lower())
        os_ = sum(1 for k in keywords if k.lower() in rt["operation_id"].lower())
        score = ps * 3 + os_
        if score > 0: candidates.append((score, -len(rt["path"].split("/")), rt))
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    if not candidates: return None
    best = candidates[0]
    tied = [c for c in candidates if c[0] == best[0] and c[1] == best[1]]
    return None if len(tied) > 1 else best[2]


def _build_body(rt: dict, known: dict = None) -> tuple[str, dict]:
    schema = rt.get("request_schema", {})
    ct = rt.get("content_type", "application/json")
    required = set(rt.get("required_fields", []))
    properties = schema.get("properties", {})
    body = {}
    known = known or {}
    for field, prop in properties.items():
        if field not in required: continue
        if field in known and known[field] is not None:
            body[field] = known[field]
        elif "enum" in prop:
            body[field] = prop["enum"][0]
        elif prop.get("type") == "integer":
            body[field] = 1
        elif prop.get("type") == "boolean":
            body[field] = False
        elif prop.get("type") == "string":
            body[field] = "test"
    return ct, body


def _check_redirect_failure(params: dict) -> str:
    for key in _FLASH_ERROR_KEYS:
        if key in params:
            return f"Redirect contains {key}={params[key]}"
    return ""


def _extract_id_from_url(url: str) -> dict:
    ids = {}
    params = _parse_redirect_params(url)
    for k, v in params.items():
        if v and ("_id" in k or k == "id"):
            ids[k] = v
    return ids


def _check_db_record(db_path: str, table: str, column: str, value: str) -> bool:
    if not all([os.path.isfile(db_path), table, column, value]):
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        cur = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (value,))
        count = cur.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


def run_workflow_test(repo_path: str, port: int = 8000) -> WorkflowTestReport:
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

    disp = tempfile.mkdtemp(prefix="acwf-")
    report.disposable_root = disp
    disp_db = os.path.join(disp, "db", "cloneshow.db")
    os.makedirs(os.path.dirname(disp_db), exist_ok=True)
    if os.path.isfile(prod_db):
        shutil.copy2(prod_db, disp_db)
    if os.path.commonpath([disp, repo_path]).startswith(repo_path):
        report.stages.append(StageRecord(number=0, status="FAIL")); report.overall_status = "SAFETY_BLOCKED"
        report.duration = time.time() - t0; return report

    s0 = StageRecord(number=0, stage="ISOLATION_PROOF", status="PASS",
                      evidence=[f"Root: {disp}"])
    report.stages.append(s0)

    venv = os.path.join(repo_path, ".venv", "bin", "python")
    if not os.path.isfile(venv):
        report.overall_status = "REQUIRED_SERVICE_MISSING"; report.duration = time.time() - t0; return report
    if _port_listening("127.0.0.1", port):
        for alt in range(port + 1, port + 100):
            if not _port_listening("127.0.0.1", alt): port = alt; break

    env = os.environ.copy()
    env["CLONECAST_DB_PATH"] = disp_db
    env["PATH"] = os.path.join(repo_path, ".venv", "bin") + ":" + env.get("PATH", "")
    env.pop("DEEPSEEK_API_KEY", None)

    args = [venv, "-m", "uvicorn", "clonecast.web_app:create_app", "--factory", "--host", "127.0.0.1", f"--port={port}"]
    proc = subprocess.Popen(args, cwd=repo_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(40):
        if _port_listening("127.0.0.1", port): break
        if proc.poll() is not None:
            _, err = proc.communicate()
            report.overall_status = "STAGE_FAILED"; report.duration = time.time() - t0; return report
        time.sleep(0.5)

    base = f"http://127.0.0.1:{port}"
    h = SessionHTTP()
    if h.request(f"{base}/health")["status_code"] != 200:
        _shutdown(proc); report.overall_status = "STAGE_FAILED"
        report.duration = time.time() - t0; return report

    time.sleep(1)
    routes = []
    or_ = h.request(f"{base}/openapi.json")
    if or_["status_code"] == 200 and or_["body"]:
        try: routes = _parse_openapi_routes(json.loads(or_["body"]))
        except: pass
    report.candidate_routes = routes

    def _stage(num: int, name: str, keywords: list, known: dict = None,
               path_params: dict = None) -> StageRecord:
        s = StageRecord(number=num, stage=name)
        s.db_before = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""
        rt = _resolve_route(routes, keywords)
        if rt is None:
            s.status = "FAIL"; s.failure_reason = "ROUTE_RESOLUTION_AMBIGUOUS"
            report.stages.append(s); return s
        s.route = rt["path"]; s.method = rt["method"]; s.operation_id = rt["operation_id"]

        # Substitute path parameters
        resolved_path = rt["path"]
        if path_params:
            for k, v in path_params.items():
                resolved_path = resolved_path.replace("{" + k + "}", str(v))

        ct, body = _build_body(rt, known)
        s.content_type = ct; s.request_body = json.dumps(body)[:500]

        t1 = time.time()
        resp = h.request(f"{base}{resolved_path}", method=rt["method"], data=body, content_type=ct, follow_redirects=False)
        s.duration = time.time() - t1
        s.response_code = resp["status_code"]
        s.response_body = resp.get("body", "")[:4000]
        s.redirect_url = resp.get("redirect_url", "")
        s.final_url = resp.get("final_url", "")

        # Parse redirect for identifiers and flash messages
        if s.redirect_url:
            params = _parse_redirect_params(s.redirect_url)
            s.flash_messages = {k: v for k, v in params.items()
                                 if k in _FLASH_ERROR_KEYS or k in _FLASH_SUCCESS_KEYS or "flash_" in k}
            ids = _extract_id_from_url(s.redirect_url)
            s.extracted_ids = ids

        # Semantic failure check
        redirect_failure = _check_redirect_failure(_parse_redirect_params(s.redirect_url))
        if s.response_code == 422:
            s.status = "FAIL"; s.failure_ownership = "AUTOCORP_REQUEST_CONSTRUCTION_DEFECT"
            s.failure_reason = f"422"
        elif s.response_code >= 500:
            s.status = "FAIL"; s.failure_ownership = "CLONECAST_SERVER_EXCEPTION"
            s.failure_reason = f"HTTP {s.response_code}"
        elif s.response_code == 0:
            s.status = "FAIL"; s.failure_ownership = "AUTOCORP_REQUEST_CONSTRUCTION_DEFECT"
            s.failure_reason = f"Connection failed"
        elif redirect_failure:
            s.status = "FAIL"; s.failure_ownership = "AUTOCORP_IDENTIFIER_PROPAGATION_DEFECT"
            s.failure_reason = redirect_failure
        elif s.response_code >= 400:
            s.status = "FAIL"
            s.failure_reason = f"HTTP {s.response_code}"
        else:
            s.status = "PASS"
            s.evidence.append(f"{s.method} {s.route} -> {s.response_code}")
            if s.extracted_ids:
                s.evidence.append(f"IDs: {s.extracted_ids}")
            if s.redirect_url:
                s.evidence.append(f"Redirect: {s.redirect_url[:200]}")

        s.db_after = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""
        report.stages.append(s)
        return s

    # Stage 2: Studio creation
    s = _stage(2, "STUDIO_CREATION", ["studio", "create"],
                {"display_name": "AutoCorp Disposable Test Studio", "show_format": "solo_host"})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report
    studio_id = s.extracted_ids.get("studio_id", "") or ""

    # Stage 2b: Studio review (required before approval)
    s = _stage(3, "STUDIO_REVIEW", ["review_studio", "studios__studio_id__review"],
                path_params={"studio_id": studio_id} if studio_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report
    report.overall_status = "DISPOSABLE_STUDIO_VALIDATED"

    # Stage 2c: Studio approval (required before activation)
    s = _stage(4, "STUDIO_APPROVAL", ["approve_studio", "studios__studio_id__approve"],
                path_params={"studio_id": studio_id} if studio_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report
    report.overall_status = "DISPOSABLE_STUDIO_APPROVED"

    # Stage 2c: Studio activation (required before episode creation)
    s = _stage(5, "STUDIO_ACTIVATION", ["activate_studio", "studios__studio_id__activate"],
                path_params={"studio_id": studio_id} if studio_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report
    report.overall_status = "DISPOSABLE_STUDIO_READY"

    # Stage 3: Episode start (with real studio_id)
    s = _stage(6, "EPISODE_START", ["episode", "start"],
                {"studio_id": studio_id, "topic": "Why careful software testing matters",
                 "research_level": "none", "length_preset": "very_short", "format_key": "solo_host"})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report

    session_id = s.extracted_ids.get("session_id", s.extracted_ids.get("id", ""))
    report.overall_status = "DISPOSABLE_RECORD_FLOW_COMPLETE"

    # Stage 4: Research mode
    s = _stage(7, "RESEARCH_MODE", ["research", "no-research"],
                {"session_id": session_id} if session_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report

    # Stage 5: Plan
    s = _stage(8, "EPISODE_PLAN", ["episode", "plan", "create"],
                {"session_id": session_id} if session_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0); return report
    plan_id = s.extracted_ids.get("plan_id", "") or ""

    # Stage 6: Assemble
    s = _stage(9, "ASSEMBLE", ["assemble", "episode"],
                {"plan_id": plan_id} if plan_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason
    else:
        report.first_failure = ""

    _shutdown(proc); _finalize(report, prod_db, t0)
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
