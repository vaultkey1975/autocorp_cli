#!/usr/bin/env python3
"""Live Application Inspector.

The inspector answers a different question from repository discovery:
"does the application actually start and respond?" It composes the
existing Discovery Engine and static Live Readiness Scanner, then safely
launches detected entry points from a disposable copy of the target
repository. It never writes to the target repository.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from brains import discovery, live_readiness, scanner


_MAX_CAPTURE = 120_000
_MAX_RESPONSE = 2 * 1024 * 1024
_REQUEST_TIMEOUT = 5
_POLL_INTERVAL = 0.1
_SAFE_PATHS = ("/", "/health", "/docs", "/openapi.json")
_FRAMEWORK_IMPORTS = {"fastapi", "flask", "django"}
_CLONECAST_FEATURES = (
    ("Create Episode", ("episode", "create")),
    ("Research", ("research",)),
    ("Script Studio", ("script",)),
    ("Storytelling", ("story", "conversation")),
    ("Voice Lab", ("voice", "tts")),
    ("Publishing", ("publish", "release")),
    ("Video Studio", ("video", "render")),
    ("YouTube Publishing", ("youtube",)),
)


@dataclass(frozen=True)
class EntryPoint:
    kind: str
    target: str
    evidence: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    confidence: int = 0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "target": self.target,
            "evidence": list(self.evidence),
            "command": list(self.command),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class EndpointResult:
    method: str
    path: str
    status: str
    status_code: int = 0
    latency_ms: float = 0.0
    error: str = ""
    content_type: str = ""
    body_preview: str = ""

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "status_code": self.status_code,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
            "content_type": self.content_type,
            "body_preview": self.body_preview,
        }


@dataclass(frozen=True)
class DatabaseInspection:
    path: str
    status: str
    integrity: str = "UNKNOWN"
    foreign_keys: str = "UNKNOWN"
    schema_version: str = "UNKNOWN"
    migrations: str = "UNKNOWN"
    evidence: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "status": self.status,
            "integrity": self.integrity,
            "foreign_keys": self.foreign_keys,
            "schema_version": self.schema_version,
            "migrations": self.migrations,
            "evidence": list(self.evidence),
            "error": self.error,
        }


@dataclass(frozen=True)
class FeatureInspection:
    name: str
    status: str
    evidence: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "evidence": list(self.evidence),
            "reason": self.reason,
        }


@dataclass
class LiveInspectionReport:
    repo_path: str
    project_type: str = "Unknown"
    entry_points: tuple[EntryPoint, ...] = ()
    selected_entry_point: EntryPoint | None = None
    application_launches: bool = False
    launch_status: str = "UNKNOWN"
    launch_time_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    startup_exception: str = ""
    disposable_root: str = ""
    cleanup_verified: bool = False
    routes_discovered: tuple[str, ...] = ()
    routes_failing: tuple[EndpointResult, ...] = ()
    endpoint_results: tuple[EndpointResult, ...] = ()
    database_status: tuple[DatabaseInspection, ...] = ()
    configuration_problems: tuple[str, ...] = ()
    feature_status: tuple[FeatureInspection, ...] = ()
    broken_features: tuple[FeatureInspection, ...] = ()
    healthy_features: tuple[FeatureInspection, ...] = ()
    highest_risk_failures: tuple[str, ...] = ()
    highest_value_next_task: str = "Unable to determine from repository evidence."
    repository_quality: str = "UNKNOWN"
    running_application: str = "UNKNOWN"
    production_readiness: str = "UNKNOWN"
    developer_workspace: str = "UNKNOWN"
    discovery_profile: dict = field(default_factory=dict)
    live_readiness: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "repo_path": self.repo_path,
            "project_type": self.project_type,
            "entry_points": [item.to_dict() for item in self.entry_points],
            "selected_entry_point": self.selected_entry_point.to_dict() if self.selected_entry_point else None,
            "application_launches": self.application_launches,
            "launch_status": self.launch_status,
            "launch_time_seconds": round(self.launch_time_seconds, 3),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "startup_exception": self.startup_exception,
            "disposable_root": self.disposable_root,
            "cleanup_verified": self.cleanup_verified,
            "routes_discovered": list(self.routes_discovered),
            "routes_failing": [item.to_dict() for item in self.routes_failing],
            "endpoint_results": [item.to_dict() for item in self.endpoint_results],
            "database_status": [item.to_dict() for item in self.database_status],
            "configuration_problems": list(self.configuration_problems),
            "feature_status": [item.to_dict() for item in self.feature_status],
            "broken_features": [item.to_dict() for item in self.broken_features],
            "healthy_features": [item.to_dict() for item in self.healthy_features],
            "highest_risk_failures": list(self.highest_risk_failures),
            "highest_value_next_task": self.highest_value_next_task,
            "repository_quality": self.repository_quality,
            "running_application": self.running_application,
            "production_readiness": self.production_readiness,
            "developer_workspace": self.developer_workspace,
            "discovery_profile": dict(self.discovery_profile),
            "live_readiness": dict(self.live_readiness),
        }


def inspect_application(repo_path: str, timeout: int = 10, preferred_port: int = 0) -> LiveInspectionReport:
    """Inspect a running application from a disposable copy of `repo_path`."""
    repo_path = os.path.abspath(repo_path)
    profile = discovery.discover_repository(repo_path, store_profile=True)
    readiness = live_readiness.run_live_readiness(repo_path)
    scan = scanner.run_scan(repo_path)
    files = discovery._inventory(repo_path)
    texts = discovery._manifest_text(repo_path)
    entries = _detect_entry_points(repo_path, files, texts)
    dbs = _inspect_databases(repo_path, files)
    report = LiveInspectionReport(
        repo_path=repo_path,
        project_type=profile.application_type.value,
        entry_points=tuple(entries),
        database_status=tuple(dbs),
        repository_quality=_repository_quality(readiness),
        developer_workspace="DIRTY" if scan.working_tree == "dirty" else "CLEAN",
        discovery_profile=profile.to_dict(),
        live_readiness=_readiness_to_dict(readiness),
    )

    if not entries:
        report.launch_status = "NO_ENTRY_POINT"
        report.configuration_problems = ("No runnable entry point found.",)
        return _finalize(report, readiness)

    selected = _select_entry_point(entries)
    report.selected_entry_point = selected
    temp_root = tempfile.mkdtemp(prefix="autocorp-inspect-")
    report.disposable_root = temp_root
    proc = None
    try:
        app_root = os.path.join(temp_root, "repo")
        try:
            _copy_disposable(repo_path, app_root)
        except (OSError, shutil.Error) as exc:
            report.launch_status = "DISPOSABLE_COPY_FAILED"
            report.startup_exception = str(exc)[:2000]
            return _finalize(report, readiness)
        command = _command_for_entry(selected, app_root, preferred_port, _python_for_repo(repo_path))
        selected = EntryPoint(selected.kind, selected.target, selected.evidence, tuple(command["args"]), selected.confidence)
        report.selected_entry_point = selected
        if selected.kind in {"cli", "console_script"}:
            _run_cli(command, app_root, timeout, report)
        else:
            proc = _launch_server(command, app_root)
            _poll_server(command, proc, timeout, report)
            if report.application_launches:
                _inspect_http(command, report)
    finally:
        if proc is not None and proc.poll() is None:
            out, err = _terminate(proc)
            if not report.stdout:
                report.stdout = out[:_MAX_CAPTURE]
            if not report.stderr:
                report.stderr = err[:_MAX_CAPTURE]
        report.cleanup_verified = _cleanup(temp_root)

    return _finalize(report, readiness)


def render_inspection(report: LiveInspectionReport, full: bool = False) -> str:
    lines = [
        "Live Application Inspector",
        "==========================",
        "",
        f"Repository: {report.repo_path}",
        f"Project Type: {report.project_type}",
        f"Application Launches: {'Yes' if report.application_launches else 'No'}",
        f"Launch Status: {report.launch_status}",
        f"Launch Time: {report.launch_time_seconds:.3f}s",
        f"Disposable Cleanup Verified: {'Yes' if report.cleanup_verified else 'No'}",
        "",
        "Score Separation",
        "----------------",
        f"- Repository Quality: {report.repository_quality}",
        f"- Running Application: {report.running_application}",
        f"- Production Readiness: {report.production_readiness}",
        f"- Developer Workspace: {report.developer_workspace}",
        "",
        "Entry Points",
        "------------",
    ]
    lines.extend(_entry_lines(report.entry_points))
    lines.extend(["", "HTTP Endpoint Results", "---------------------"])
    lines.extend(_endpoint_lines(report.endpoint_results))
    lines.extend(["", "Routes Discovered", "-----------------"])
    lines.extend(_bullet(report.routes_discovered))
    lines.extend(["", "Routes Failing", "--------------"])
    lines.extend(_endpoint_lines(report.routes_failing))
    lines.extend(["", "Database Status", "---------------"])
    lines.extend(_database_lines(report.database_status))
    lines.extend(["", "Feature Status", "--------------"])
    lines.extend(_feature_lines(report.feature_status))
    lines.extend(["", "Healthy Features", "----------------"])
    lines.extend(_feature_lines(report.healthy_features))
    lines.extend(["", "Broken Features", "---------------"])
    lines.extend(_feature_lines(report.broken_features))
    lines.extend(["", "Configuration Problems", "----------------------"])
    lines.extend(_bullet(report.configuration_problems))
    lines.extend(["", "Highest-Risk Failures", "---------------------"])
    lines.extend(_bullet(report.highest_risk_failures))
    lines.extend(["", "Highest-Value Next Task", "-----------------------", report.highest_value_next_task])
    if full:
        lines.extend(["", "stdout", "------", report.stdout or "(empty)", "", "stderr", "------", report.stderr or "(empty)"])
    return "\n".join(lines)


def inspection_to_json(report: LiveInspectionReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def _detect_entry_points(repo_path: str, files: list[str], texts: dict[str, str]) -> list[EntryPoint]:
    entries = []
    for rel in files:
        if not rel.endswith(".py"):
            continue
        full = os.path.join(repo_path, rel)
        content = _read(full)
        findings = _python_findings(content)
        module = _module_name(rel)
        for name in findings["fastapi_apps"]:
            entries.append(EntryPoint("fastapi", f"{module}:{name}", (f"{rel}: {name} = FastAPI(...)",), confidence=92))
        for name in findings["fastapi_factories"]:
            entries.append(EntryPoint("fastapi", f"{module}:{name}", (f"{rel}: factory returns FastAPI",), confidence=88))
        for name in findings["flask_apps"]:
            entries.append(EntryPoint("flask", f"{module}:{name}", (f"{rel}: {name} = Flask(...)",), confidence=88))
        if findings["django_manage"]:
            entries.append(EntryPoint("django", "manage.py", (rel,), confidence=85))
        if findings["main_block"]:
            entries.append(EntryPoint("cli", rel, (f"{rel}: __main__ block",), confidence=78))

    entries.extend(_console_script_entries(texts))
    entries.extend(_server_target_entries(texts))
    return _dedupe_entries(entries)


def _python_findings(content: str) -> dict:
    out = {
        "fastapi_apps": [],
        "fastapi_factories": [],
        "flask_apps": [],
        "main_block": False,
        "django_manage": False,
    }
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call_name = _call_name(node.value.func)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if call_name == "FastAPI":
                        out["fastapi_apps"].append(target.id)
                    elif call_name == "Flask":
                        out["flask_apps"].append(target.id)
        elif isinstance(node, ast.FunctionDef):
            if _function_returns_call(node, "FastAPI"):
                out["fastapi_factories"].append(node.name)
        elif isinstance(node, ast.If):
            test = ast.get_source_segment(content, node.test) or ""
            if "__name__" in test and "__main__" in test:
                out["main_block"] = True
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and _call_name(child.func).endswith("execute_from_command_line"):
                out["django_manage"] = True
    return out


def _function_returns_call(node: ast.FunctionDef, call_name: str) -> bool:
    assigned = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
            if _call_name(child.value.func) == call_name:
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        assigned.add(target.id)
        if isinstance(child, ast.Return) and isinstance(child.value, ast.Call):
            if _call_name(child.value.func) == call_name:
                return True
        if isinstance(child, ast.Return) and isinstance(child.value, ast.Name):
            if child.value.id in assigned:
                return True
    return False


def _call_name(func) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parent = _call_name(func.value)
        return f"{parent}.{func.attr}" if parent else func.attr
    return ""


def _console_script_entries(texts: dict[str, str]) -> list[EntryPoint]:
    entries = []
    pyproject = texts.get("pyproject.toml", "")
    if pyproject and "[project.scripts]" in pyproject:
        in_scripts = False
        for raw in pyproject.splitlines():
            line = raw.strip()
            if line.startswith("["):
                in_scripts = line == "[project.scripts]"
                continue
            if in_scripts and "=" in line and ":" in line:
                name, target = line.split("=", 1)
                entries.append(EntryPoint("console_script", target.strip().strip("\"'"), (f"pyproject.toml [project.scripts] {name.strip()}",), confidence=85))
    for rel, text in texts.items():
        if os.path.basename(rel) in {"setup.cfg", "setup.py"} and "console_scripts" in text:
            for target in re.findall(r"=\s*([a-zA-Z_][\w.]+:[a-zA-Z_][\w.]*)", text):
                entries.append(EntryPoint("console_script", target, (f"{rel}: console_scripts",), confidence=75))
    return entries


def _server_target_entries(texts: dict[str, str]) -> list[EntryPoint]:
    entries = []
    blob = "\n".join(texts.values())
    for target in re.findall(r"(?:uvicorn|gunicorn)\s+([a-zA-Z_][\w.]+:[a-zA-Z_][\w.]+)", blob):
        kind = "fastapi" if "uvicorn" in blob else "web"
        entries.append(EntryPoint(kind, target, (f"server command target: {target}",), confidence=70))
    return entries


def _select_entry_point(entries: list[EntryPoint]) -> EntryPoint:
    order = {"fastapi": 0, "flask": 1, "django": 2, "console_script": 3, "cli": 4}
    return sorted(entries, key=lambda e: (order.get(e.kind, 99), -e.confidence, e.target))[0]


def _command_for_entry(entry: EntryPoint, app_root: str, preferred_port: int, python: str | None = None) -> dict:
    python = python or _python_for_repo(app_root)
    port = preferred_port if preferred_port else _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([app_root, os.path.join(app_root, "src"), env.get("PYTHONPATH", "")])
    env["AUTOCORP_INSPECT"] = "1"
    env["DATABASE_URL"] = f"sqlite:///{os.path.join(tempfile.gettempdir(), 'autocorp_inspect.sqlite')}"
    env["DB_PATH"] = os.path.join(tempfile.gettempdir(), "autocorp_inspect.sqlite")
    for key in ("YOUTUBE_CLIENT_SECRET_FILE", "YOUTUBE_TOKEN_FILE", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        env.pop(key, None)
    if entry.kind == "fastapi":
        args = [python, "-m", "uvicorn", entry.target, "--host", "127.0.0.1", "--port", str(port)]
        if entry.evidence and "factory returns FastAPI" in entry.evidence[0]:
            args.append("--factory")
        return {"args": args, "port": port, "env": env}
    if entry.kind == "flask":
        return {"args": [python, "-m", "flask", "--app", entry.target, "run", "--host", "127.0.0.1", "--port", str(port)], "port": port, "env": env}
    if entry.kind == "django":
        return {"args": [python, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], "port": port, "env": env}
    if entry.kind == "console_script":
        module, _, func = entry.target.partition(":")
        code = (
            "import importlib; "
            f"module = importlib.import_module({module!r}); "
            f"raise SystemExit(getattr(module, {func!r})())"
        )
        return {"args": [python, "-c", code, "--help"], "port": 0, "env": env}
    return {"args": [python, entry.target, "--help"], "port": 0, "env": env}


def _run_cli(command: dict, app_root: str, timeout: int, report: LiveInspectionReport) -> None:
    start = time.time()
    try:
        proc = subprocess.run(
            command["args"], cwd=app_root, env=command["env"], text=True,
            capture_output=True, timeout=timeout,
        )
        report.stdout = (proc.stdout or "")[:_MAX_CAPTURE]
        report.stderr = (proc.stderr or "")[:_MAX_CAPTURE]
        report.launch_time_seconds = time.time() - start
        report.application_launches = proc.returncode in (0, 2)
        report.launch_status = "CLI_STARTED" if report.application_launches else "CLI_FAILED"
        if not report.application_launches:
            report.startup_exception = report.stderr or f"exit code {proc.returncode}"
    except subprocess.TimeoutExpired as exc:
        report.launch_time_seconds = time.time() - start
        report.launch_status = "CLI_TIMEOUT"
        report.stdout = (exc.stdout or "")[:_MAX_CAPTURE] if isinstance(exc.stdout, str) else ""
        report.stderr = (exc.stderr or "")[:_MAX_CAPTURE] if isinstance(exc.stderr, str) else ""
        report.startup_exception = "CLI startup timed out."


def _launch_server(command: dict, app_root: str):
    return subprocess.Popen(
        command["args"], cwd=app_root, env=command["env"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _poll_server(command: dict, proc, timeout: int, report: LiveInspectionReport) -> None:
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate()
            report.stdout = (out or "")[:_MAX_CAPTURE]
            report.stderr = (err or "")[:_MAX_CAPTURE]
            report.launch_time_seconds = time.time() - start
            report.launch_status = "PROCESS_EXITED_EARLY"
            report.startup_exception = report.stderr or report.stdout or f"exit code {proc.returncode}"
            return
        if _is_port_open(command["port"]):
            report.application_launches = True
            report.launch_status = "APPLICATION_RESPONDING"
            report.launch_time_seconds = time.time() - start
            return
        time.sleep(_POLL_INTERVAL)
    report.launch_time_seconds = time.time() - start
    report.launch_status = "STARTUP_TIMEOUT"
    report.startup_exception = f"Port {command['port']} did not listen within {timeout}s."


def _inspect_http(command: dict, report: LiveInspectionReport) -> None:
    base = f"http://127.0.0.1:{command['port']}"
    results = [_http_get(base, path) for path in _SAFE_PATHS]
    openapi = next((r for r in results if r.path == "/openapi.json" and r.status_code == 200), None)
    route_paths = []
    if openapi and openapi.body_preview:
        try:
            body = _http_body(f"{base}/openapi.json")
            data = json.loads(body)
            route_paths = sorted(data.get("paths", {}).keys())
        except (json.JSONDecodeError, TypeError, urllib.error.URLError, OSError):
            route_paths = []
    for path in route_paths[:12]:
        if path not in _SAFE_PATHS and _safe_get_path(path):
            results.append(_http_get(base, path))
    report.endpoint_results = tuple(results)
    report.routes_discovered = tuple(route_paths)
    report.routes_failing = tuple(r for r in results if r.status in {"FAIL", "ERROR"})


def _http_get(base: str, path: str) -> EndpointResult:
    start = time.time()
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=_REQUEST_TIMEOUT) as resp:
            raw = resp.read(min(_MAX_RESPONSE, 4096))
            text = raw.decode("utf-8", errors="replace")
            status = "PASS" if 200 <= resp.status < 400 else "FAIL"
            return EndpointResult("GET", path, status, resp.status, (time.time() - start) * 1000, "", resp.headers.get("Content-Type", ""), text[:500])
    except urllib.error.HTTPError as exc:
        return EndpointResult("GET", path, "FAIL" if exc.code >= 500 else "WARNING", exc.code, (time.time() - start) * 1000, f"HTTP {exc.code}")
    except Exception as exc:
        return EndpointResult("GET", path, "ERROR", 0, (time.time() - start) * 1000, str(exc)[:200])


def _http_body(url: str) -> str:
    with urllib.request.urlopen(url, timeout=_REQUEST_TIMEOUT) as resp:
        return resp.read(_MAX_RESPONSE).decode("utf-8", errors="replace")


def _inspect_databases(repo_path: str, files: list[str]) -> list[DatabaseInspection]:
    db_files = [rel for rel in files if os.path.splitext(rel)[1].lower() in {".db", ".sqlite", ".sqlite3"}]
    migrations = [rel for rel in files if rel == "migrations" or rel.startswith("migrations/") or rel.startswith("alembic/")]
    out = []
    for rel in db_files[:10]:
        full = os.path.join(repo_path, rel)
        try:
            conn = sqlite3.connect(f"file:{full}?mode=ro", uri=True, timeout=5)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
                user_version = conn.execute("PRAGMA user_version").fetchone()[0]
                tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            finally:
                conn.close()
            status = "PASS" if integrity == "ok" and not fk_rows else "FAIL"
            migration_status = "PRESENT" if migrations or "alembic_version" in tables else "MISSING"
            out.append(DatabaseInspection(
                path=rel, status=status, integrity=integrity,
                foreign_keys="PASS" if not fk_rows else f"FAIL ({len(fk_rows)} violation(s))",
                schema_version=str(user_version),
                migrations=migration_status,
                evidence=tuple((migrations[:3] or ["No migrations directory found"])),
            ))
        except sqlite3.Error as exc:
            out.append(DatabaseInspection(rel, "FAIL", error=str(exc)))
    if not out:
        return [DatabaseInspection("Unknown", "UNKNOWN", evidence=("No SQLite database files found.",))]
    return out


def _finalize(report: LiveInspectionReport, readiness) -> LiveInspectionReport:
    features = _features(report)
    healthy = [f for f in features if f.status == "PASS"]
    broken = [f for f in features if f.status == "FAIL"]
    report.feature_status = tuple(features)
    report.healthy_features = tuple(healthy)
    report.broken_features = tuple(broken)
    problems = list(report.configuration_problems)
    if report.startup_exception:
        problems.append(report.startup_exception.splitlines()[0][:180])
    for db in report.database_status:
        if db.status == "FAIL":
            detail = db.error or f"integrity={db.integrity}; foreign_keys={db.foreign_keys}"
            problems.append(f"Database {db.path}: {detail}")
        elif db.path != "Unknown" and db.migrations == "MISSING":
            problems.append(f"Database {db.path}: migrations not found")
    report.configuration_problems = tuple(dict.fromkeys(problems))
    risks = []
    if not report.application_launches:
        risks.append(f"Application startup: {report.launch_status}")
    risks.extend(
        f"Database {db.path}: {db.error or db.foreign_keys}"
        for db in report.database_status
        if db.status == "FAIL"
    )
    risks.extend(f"GET {r.path}: {r.status_code or r.error}" for r in report.routes_failing[:5])
    risks.extend(f"{f.name}: {f.reason}" for f in broken[:5])
    report.highest_risk_failures = tuple(risks or ("No high-risk runtime failure found by live inspector.",))
    report.running_application = _running_application_status(report)
    report.production_readiness = _production_readiness_status(report, readiness)
    report.highest_value_next_task = _next_task(report)
    return report


def _features(report: LiveInspectionReport) -> list[FeatureInspection]:
    features = []
    route_blob = " ".join(report.routes_discovered).lower()
    endpoint_by_path = {r.path: r for r in report.endpoint_results}
    repo_name = os.path.basename(report.repo_path).lower()
    if "clonecast" in repo_name or "clonecast" in route_blob:
        for name, terms in _CLONECAST_FEATURES:
            matching = [path for path in report.routes_discovered if any(term in path.lower() for term in terms)]
            if not matching:
                status = "NOT CONFIGURED" if name == "YouTube Publishing" else "UNKNOWN"
                features.append(FeatureInspection(name, status, reason="No matching live route found."))
                continue
            failing = [endpoint_by_path[p] for p in matching if p in endpoint_by_path and endpoint_by_path[p].status in {"FAIL", "ERROR"}]
            if failing:
                features.append(FeatureInspection(name, "FAIL", tuple(matching[:5]), f"{len(failing)} safe diagnostic route(s) failed."))
            elif any(p in endpoint_by_path and endpoint_by_path[p].status == "PASS" for p in matching):
                features.append(FeatureInspection(name, "PASS", tuple(matching[:5]), "Safe live route responded."))
            else:
                features.append(FeatureInspection(name, "UNKNOWN", tuple(matching[:5]), "Route exists but was not safe to exercise."))
    return features


def _repository_quality(readiness) -> str:
    fails = sum(1 for c in readiness.checks if c.status == "fail")
    blocked = sum(1 for c in readiness.checks if c.status == "blocked")
    if fails or blocked:
        return "NEEDS_ATTENTION"
    return "PASS"


def _running_application_status(report: LiveInspectionReport) -> str:
    if not report.application_launches:
        return "FAIL"
    if report.routes_failing:
        return "PARTIAL"
    if report.endpoint_results:
        return "PASS"
    return "STARTED"


def _production_readiness_status(report: LiveInspectionReport, readiness) -> str:
    if not report.application_launches:
        return "BLOCKED"
    if report.routes_failing or report.broken_features:
        return "NEEDS_ATTENTION"
    if any(db.status == "FAIL" for db in report.database_status):
        return "NEEDS_ATTENTION"
    if readiness.blockers:
        return "NEEDS_ATTENTION"
    return "PASS"


def _next_task(report: LiveInspectionReport) -> str:
    if not report.application_launches:
        return "Fix application startup so the detected entry point launches in a disposable environment."
    if report.routes_failing:
        first = report.routes_failing[0]
        return f"Fix failing live endpoint: {first.method} {first.path} returned {first.status_code or first.error}."
    for db in report.database_status:
        if db.status == "FAIL":
            return f"Fix database integrity/open failure for {db.path}."
        if db.path != "Unknown" and db.migrations == "MISSING":
            return f"Add or document migrations for database {db.path}."
    if report.broken_features:
        return f"Fix broken feature: {report.broken_features[0].name}."
    return "No immediate runtime blocker found by live inspector."


def _readiness_to_dict(readiness) -> dict:
    return {
        "overall_status": readiness.overall_status,
        "confidence": readiness.confidence,
        "blockers": list(readiness.blockers),
        "checks": [
            {
                "title": c.title,
                "status": c.status,
                "category": c.category,
                "reason": c.reason,
                "evidence": list(c.evidence),
            }
            for c in readiness.checks
        ],
    }


def _copy_disposable(src: str, dst: str) -> None:
    ignore = shutil.ignore_patterns(
        ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
        "workspace", "data", "runtime", "output", "outputs", "artifacts",
        "node_modules", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    )
    shutil.copytree(src, dst, ignore=ignore)


def _python_for_repo(repo_path: str) -> str:
    for rel in (".venv/bin/python", "venv/bin/python"):
        candidate = os.path.join(repo_path, rel)
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def _is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _terminate(proc) -> tuple[str, str]:
    try:
        proc.terminate()
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate(timeout=5)
        return out or "", err or ""
    except (OSError, subprocess.SubprocessError):
        return "", ""


def _cleanup(path: str) -> bool:
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return not os.path.exists(path)


def _safe_get_path(path: str) -> bool:
    if "{" in path or "}" in path:
        return False
    lowered = path.lower()
    if any(word in lowered for word in ("delete", "publish", "upload", "create", "generate", "render", "start", "stop")):
        return False
    return True


def _module_name(rel: str) -> str:
    no_ext = os.path.splitext(rel)[0].replace("/", ".")
    if no_ext.startswith("src."):
        return no_ext[4:]
    return no_ext


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _dedupe_entries(entries: list[EntryPoint]) -> list[EntryPoint]:
    seen = set()
    out = []
    for entry in entries:
        key = (entry.kind, entry.target)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def _entry_lines(entries: tuple[EntryPoint, ...]) -> list[str]:
    if not entries:
        return ["- Unknown - not enough evidence"]
    return [f"- {entry.kind}: {entry.target} ({entry.confidence}%)" for entry in entries]


def _endpoint_lines(results: tuple[EndpointResult, ...]) -> list[str]:
    if not results:
        return ["- (none)"]
    return [
        f"- [{result.status}] {result.method} {result.path}: {result.status_code or result.error} ({result.latency_ms:.0f}ms)"
        for result in results
    ]


def _database_lines(databases: tuple[DatabaseInspection, ...]) -> list[str]:
    if not databases:
        return ["- Unknown - not enough evidence"]
    lines = []
    for db in databases:
        lines.append(
            f"- [{db.status}] {db.path}: integrity={db.integrity}, foreign_keys={db.foreign_keys}, migrations={db.migrations}"
        )
        if db.error:
            lines.append(f"  Error: {db.error}")
    return lines


def _feature_lines(features: tuple[FeatureInspection, ...]) -> list[str]:
    if not features:
        return ["- (none)"]
    return [f"- [{feature.status}] {feature.name}: {feature.reason}" for feature in features]


def _bullet(items) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- (none)"]
