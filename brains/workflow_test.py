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


@dataclass
class AudioArtifactRecord:
    kind: str = ""
    path: str = ""
    size_bytes: int = 0
    duration_seconds: float = 0.0
    codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    format_name: str = ""
    sha256: str = ""
    db_sha256: str = ""
    sha256_matches_db: bool = True
    verified: bool = False
    verify_error: str = ""


@dataclass
class DatabaseVerification:
    checked: bool = False
    integrity_check: str = ""
    integrity_ok: bool = False
    foreign_key_violations: list = field(default_factory=list)
    foreign_keys_ok: bool = False
    table_row_counts: dict = field(default_factory=dict)
    missing_expected_rows: list = field(default_factory=list)
    error: str = ""


@dataclass
class PublishingFinding:
    severity: str = "INFO"   # PASS | WARNING | FAIL | INFO
    category: str = ""
    evidence: str = ""
    recommendation: str = ""


@dataclass
class ExternalDependencyStatus:
    platform: str = ""
    credentials_configured: bool = False
    credentials_env_vars_checked: list = field(default_factory=list)
    endpoint_reachable: str = "N/A"          # N/A | reachable | unreachable
    real_upload_code_exists: bool = False
    notes: str = ""


class WorkflowTestReport:
    def __init__(self):
        self.repo_path = self.disposable_root = self.production_db_path = ""
        self.production_db_before = self.production_db_after = ""
        self.production_db_size_before = self.production_db_size_after = 0
        self.clonecast_git_status_before = self.clonecast_git_status_after = ""
        self.audio_artifact = AudioArtifactRecord()
        self.artifacts: list[AudioArtifactRecord] = []
        self.database_verification = DatabaseVerification()
        self.cleanup_attempted = False
        self.cleanup_removed = False
        self.cleanup_error = ""
        self.stages: list[StageRecord] = []
        self.candidate_routes: list = []
        self.overall_status = "INCONCLUSIVE"
        self.first_failure = ""
        self.duration = 0.0
        self.success = False
        self.failure_reason = ""
        self.workflow_stage = "NOT_STARTED"
        self.repository_unchanged = False
        self.verification_summary = "Verification has not run."
        self.recommended_next_action = "Run the disposable workflow validation."
        self.exit_code = 1
        # Phase 1Y: publishing validation
        self.include_publishing = False
        self.publishing_readiness = "NOT_RUN"   # PASS | WARNING | FAIL | NOT_RUN
        self.publishing_findings: list[PublishingFinding] = []
        self.external_dependency_status: list[ExternalDependencyStatus] = []


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


_ID_FIELDS = {"id", "studio_id", "session_id", "plan_id", "job_id", "episode_id",
              "conversation_id", "caller_id", "guest_id", "character_id",
              "reviewer_id", "approver_id", "asset_id", "package_id"}
_ENUM_FIELDS = {"decision", "status", "mode", "level", "format_key", "format",
                "length_preset", "research_level", "privacy_status",
                "recovery_policy", "show_format", "language", "role"}
_SEMANTIC_FIELDS = _ID_FIELDS | _ENUM_FIELDS | {"reviewer", "approver",
                    "stable_name", "speaking_style", "personality_summary"}

_PLACEHOLDER_TERMS = {"test", "testing", "placeholder", "dummy", "example",
                       "sample", "fake", "mock", "unknown", "temp",
                       "temporary", "default", "autocorp test"}


_SEMANTIC_DEFAULTS = {
    "reviewer": "Larry",
    "approver": "Larry",
    "decision": "accepted",
    "notes": "AutoCorp disposable review.",
    "show_format": "solo_host",
    "language": "en-US",
}


def _is_free_form(field: str) -> bool:
    return field not in _SEMANTIC_FIELDS or field in _SEMANTIC_DEFAULTS


def _is_placeholder(value: str) -> bool:
    return str(value).lower().strip() in _PLACEHOLDER_TERMS


def _build_body(rt: dict, known: dict = None) -> tuple[str, dict]:
    schema = rt.get("request_schema", {})
    ct = rt.get("content_type", "application/json")
    required = set(rt.get("required_fields", []))
    properties = schema.get("properties", {})
    body = {}
    known = known or {}
    for field, prop in properties.items():
        if field in known and known[field] is not None and not _is_placeholder(known[field]):
            body[field] = known[field]
            continue
        if field not in required:
            continue
        elif _is_free_form(field) and prop.get("type") == "string":
            if field in _SEMANTIC_DEFAULTS:
                body[field] = _SEMANTIC_DEFAULTS[field]
            elif field in ("display_name", "stable_name"):
                body[field] = "AutoCorp Disposable Test Studio"
            elif field == "description":
                body[field] = "Temporary disposable test studio."
            elif field in ("reviewer", "approver"):
                body[field] = "AutoCorp Disposable Reviewer"
            elif field == "topic":
                body[field] = "Why careful software testing matters"
            else:
                body[field] = "disposable-test-value"
        elif "enum" in prop:
            body[field] = prop["enum"][0]  # safest enum value
        elif prop.get("type") == "integer":
            body[field] = 1
        elif prop.get("type") == "number":
            body[field] = 1.0
        elif prop.get("type") == "boolean":
            body[field] = False
        # else: leave unresolved - request will fail prevalidation
    return ct, body


def _check_redirect_failure(params: dict) -> str:
    for key in _FLASH_ERROR_KEYS:
        if key in params:
            return f"Redirect contains {key}={params[key]}"
    return ""


def _classify_redirect_failure(reason: str) -> str:
    lowered = reason.lower()
    if "record not found" in lowered or "not found:" in lowered:
        return "AUTOCORP_IDENTIFIER_PROPAGATION_DEFECT"
    return "CLONECAST_WORKFLOW_PRECONDITION"


def _is_idempotent_redirect_failure(reason: str) -> bool:
    lowered = reason.lower()
    return "already exists" in lowered and "existing " in lowered


def _extract_id_from_url(url: str) -> dict:
    ids = {}
    params = _parse_redirect_params(url)
    for k, v in params.items():
        if v and ("_id" in k or k == "id"):
            ids[k] = v
    return ids


def _extract_char_id_from_html(html: str, display_name: str) -> str:
    """Extract the canonical character identifier from rendered studio HTML."""
    ids = re.findall(r'\b(character_[a-f0-9]{32}|[a-f0-9]{32})\b', html)
    if not ids:
        return ""
    # One match = that's our character
    if len(ids) == 1:
        return ids[0]
    # Multiple matches: find the one near our display name
    for cid in ids:
        # Check if display name appears within 200 chars of this ID
        idx = html.find(cid)
        if idx >= 0:
            ctx = html[max(0, idx-200):idx+200]
            if display_name in ctx:
                return cid
    # Fallback: return last one (most recently created)
    return ids[-1]


def _db_record_exists(db_path: str, table: str, column: str, value: str) -> bool:
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


def _db_one(db_path: str, query: str, params: tuple = ()) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _db_all(db_path: str, query: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(query, params)]
    finally:
        conn.close()


def _substitute_path_params(path: str, path_params: dict | None) -> str:
    resolved_path = path
    if path_params:
        for k, v in path_params.items():
            resolved_path = resolved_path.replace("{" + k + "}", str(v))
    return resolved_path


def _route_path_matches(route_path: str, requested_path: str) -> bool:
    route_parts = route_path.strip("/").split("/")
    requested_parts = requested_path.strip("/").split("/")
    if len(route_parts) != len(requested_parts):
        return False
    for left, right in zip(route_parts, requested_parts):
        if left.startswith("{") and left.endswith("}"):
            continue
        if left != right:
            return False
    return True


def _find_approved_voice_profile(db_path: str) -> str:
    if not os.path.isfile(db_path):
        return ""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        row = conn.execute(
            """
            SELECT voice_profile_id
              FROM voice_profiles
             WHERE lifecycle_status='approved'
             ORDER BY CASE WHEN lower(display_name)='larry' THEN 0 ELSE 1 END,
                      display_name,
                      voice_profile_id
             LIMIT 1
            """
        ).fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


def _find_active_radio_audio_preset(db_path: str) -> str:
    if not os.path.isfile(db_path):
        return ""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        row = conn.execute(
            """
            SELECT preset_id
              FROM radio_audio_presets
             WHERE lifecycle_status='active'
             ORDER BY CASE WHEN preset_id='rapreset_clean_studio_v1' THEN 0 ELSE 1 END,
                      preset_id
             LIMIT 1
            """
        ).fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


def _prepare_disposable_voice_assets(disp_db: str, disp: str) -> list[str]:
    """Copy approved voice references into the disposable root and repoint only
    the copied database. Production voice assets remain read-only inputs."""
    copied = []
    target = os.path.join(disp, "runtime", "voice_assets")
    os.makedirs(target, exist_ok=True)
    conn = sqlite3.connect(disp_db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT reference_asset_id, managed_path FROM voice_reference_assets").fetchall()
        for row in rows:
            src = row["managed_path"]
            if not src or not os.path.isfile(src):
                continue
            dst = os.path.join(target, os.path.basename(src))
            shutil.copy2(src, dst)
            conn.execute(
                "UPDATE voice_reference_assets SET managed_path=? WHERE reference_asset_id=?",
                (dst, row["reference_asset_id"]),
            )
            copied.append(dst)
        conn.commit()
    finally:
        conn.close()
    return copied


def _clonecast_env(repo_path: str, disp: str, disp_db: str) -> dict:
    env = os.environ.copy()
    env["CLONECAST_ROOT"] = disp
    env["CLONECAST_DB_PATH"] = disp_db
    env["CLONECAST_MIGRATIONS_PATH"] = os.path.join(repo_path, "migrations")
    env["CLONECAST_LOG_DIR"] = os.path.join(disp, "logs")
    env["CLONECAST_RESEARCH_ROOT"] = os.path.join(disp, "research")
    env["CLONECAST_RUNTIME_DIR"] = os.path.join(disp, "runtime")
    env["CLONECAST_VOICE_ASSET_DIR"] = os.path.join(disp, "runtime", "voice_assets")
    env["CLONECAST_SPEECH_OUTPUT_DIR"] = os.path.join(disp, "runtime", "speech")
    env["CLONECAST_CONVERSATION_ASSEMBLY_DIR"] = os.path.join(disp, "runtime", "conversation_assembly")
    env["CLONECAST_CONVERSATION_ASSEMBLY_SILENCE_MS"] = "0"
    env["CLONECAST_RADIO_EPISODE_INTEGRATION_DIR"] = os.path.join(disp, "runtime", "radio_episode_integration")
    env["CLONECAST_RADIO_EPISODE_INTEGRATION_PAUSE_MS"] = "0"
    env["CLONECAST_EPISODE_AUDIO_OUTPUT_DIR"] = os.path.join(disp, "runtime", "episode_audio")
    env["CLONECAST_EPISODE_RELEASE_DIR"] = os.path.join(disp, "runtime", "episode_releases")
    env["CLONECAST_PUBLICATION_DROP_DIR"] = os.path.join(disp, "runtime", "publication_drop")
    env["CLONECAST_RADIO_RELEASE_PACKAGE_DIR"] = os.path.join(disp, "runtime", "radio_release_packages")
    env["CLONECAST_RADIO_PUBLICATION_DIR"] = os.path.join(disp, "runtime", "radio_publications")
    env["CLONECAST_BRANDING_DIR"] = os.path.join(disp, "runtime", "branding")
    env["CLONECAST_SPEECH_RUNTIME_PYTHON"] = os.path.join(repo_path, ".venv-chatterbox", "bin", "python")
    env["CLONECAST_SPEECH_MODEL_CACHE_DIR"] = os.path.join(repo_path, "runtime", "models", "huggingface")
    env["PATH"] = os.path.join(repo_path, ".venv", "bin") + ":" + env.get("PATH", "")
    env.pop("DEEPSEEK_API_KEY", None)
    return env


def _inside(path: str, root: str) -> bool:
    try:
        os.path.abspath(path)
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:
        return False


def _ffprobe(path: str, timeout: int = 30) -> tuple[AudioArtifactRecord, str]:
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", path,
            ],
            text=True, capture_output=True, timeout=timeout,
        )
    except Exception as exc:
        return AudioArtifactRecord(path=path), str(exc)
    if proc.returncode != 0:
        return AudioArtifactRecord(path=path), (proc.stderr or proc.stdout)[:1000]
    try:
        payload = json.loads(proc.stdout)
        stream = next((s for s in payload.get("streams", []) if s.get("codec_type") == "audio"), {})
        duration = stream.get("duration") or payload.get("format", {}).get("duration") or 0
        return AudioArtifactRecord(
            path=path,
            size_bytes=os.path.getsize(path),
            duration_seconds=float(duration),
            codec=str(stream.get("codec_name") or ""),
            sample_rate=int(stream.get("sample_rate") or 0),
            channels=int(stream.get("channels") or 0),
            format_name=str(payload.get("format", {}).get("format_name") or ""),
        ), ""
    except Exception as exc:
        return AudioArtifactRecord(path=path), str(exc)


def _verify_artifact(kind: str, path: str, db_sha256: str = "") -> AudioArtifactRecord:
    """Independently verify one on-disk audio artifact: real ffprobe metadata
    plus a freshly computed SHA-256, cross-checked against whatever hash the
    database row claims (if any). Never trusts the DB's own recorded values -
    proves them against the actual file."""
    meta, error = _ffprobe(path)
    meta.kind = kind
    if error:
        meta.verify_error = error
        return meta
    meta.sha256 = _sha256_file(path)
    meta.db_sha256 = db_sha256
    meta.sha256_matches_db = (not db_sha256) or (meta.sha256 == db_sha256)
    meta.verified = True
    if not meta.sha256_matches_db:
        meta.verify_error = "SHA-256 mismatch between database record and on-disk file"
    return meta


_EXPECTED_TABLES = (
    "episodes",
    "radio_sessions",
    "ai_conversations",
    "conversation_voice_render_jobs",
    "conversation_assembly_jobs",
)


def _verify_database(db_path: str) -> "DatabaseVerification":
    """PRAGMA integrity_check + PRAGMA foreign_key_check against the
    disposable database, plus a presence/row-count check for every table the
    workflow is expected to have written to. Read-only (no PRAGMA writes any
    data); opened directly (not via the mode=ro URI) because PRAGMA
    integrity_check requires a writable-mode connection on some SQLite
    builds, but no statement here ever mutates a row."""
    result = DatabaseVerification(checked=True)
    if not os.path.isfile(db_path):
        result.error = f"Database file does not exist: {db_path}"
        return result
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
            result.integrity_check = "; ".join(str(r[0]) for r in integrity_rows) or "unknown"
            result.integrity_ok = result.integrity_check.strip().lower() == "ok"

            fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
            result.foreign_key_violations = [
                {"table": r[0], "rowid": r[1], "parent": r[2], "fkid": r[3]} for r in fk_rows
            ]
            result.foreign_keys_ok = not result.foreign_key_violations

            for table in _EXPECTED_TABLES:
                try:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.Error as exc:
                    result.table_row_counts[table] = -1
                    result.missing_expected_rows.append(f"{table}: query failed ({exc})")
                    continue
                result.table_row_counts[table] = count
                if count < 1:
                    result.missing_expected_rows.append(f"{table}: expected at least 1 row, found 0")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        result.error = str(exc)
    return result


def _classify_job_error(error: str) -> str:
    lowered = (error or "").lower()
    if any(term in lowered for term in ("ollama", "conversation provider", "chatterbox", "cuda", "gpu", "model", "speech provider")):
        return "MISSING_EXTERNAL_MODEL_OR_DEPENDENCY"
    if any(term in lowered for term in ("ffmpeg", "ffprobe")):
        return "MISSING_EXTERNAL_MODEL_OR_DEPENDENCY"
    if "record not found" in lowered or "not found:" in lowered:
        return "AUTOCORP_IDENTIFIER_PROPAGATION_DEFECT"
    if "requires" in lowered or "must be" in lowered or "not eligible" in lowered:
        return "CLONECAST_WORKFLOW_PRECONDITION"
    return "CLONECAST_APPLICATION_DEFECT"


# --------------------------------------------------------------------------- #
# Phase 1Y: external publishing-provider dependency check (read-only, no
# network I/O, never publishes anything)
# --------------------------------------------------------------------------- #
_PLATFORM_CREDENTIAL_ENV_VARS = {
    "youtube": ["YOUTUBE_API_KEY", "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"],
    "spotify_rss": ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_RSS_FEED_TOKEN"],
    "rumble": ["RUMBLE_API_KEY", "RUMBLE_ACCESS_TOKEN"],
    "tiktok_audio_visualizer": ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_ACCESS_TOKEN"],
    "facebook_instagram_audio_visualizer": ["FACEBOOK_ACCESS_TOKEN", "FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET",
                                             "INSTAGRAM_ACCESS_TOKEN"],
}


def _check_external_publishing_dependencies() -> list:
    """Read-only: is any credential configured (via common env var names) for
    each named external publishing platform? Confirmed by source inspection
    (see Phase 1Y research) that CloneCast's own code has NO real HTTP-client
    integration for any of these platforms - destination_type is DB-CHECK-
    constrained to the literal string 'local' only, and the platform "export"
    routes explicitly write upload_status=not_uploaded_no_platform_api_configured.
    This function never makes a network call and never publishes anything."""
    statuses = []
    for platform, env_vars in _PLATFORM_CREDENTIAL_ENV_VARS.items():
        configured_vars = [v for v in env_vars if os.environ.get(v)]
        statuses.append(ExternalDependencyStatus(
            platform=platform,
            credentials_configured=bool(configured_vars),
            credentials_env_vars_checked=env_vars,
            endpoint_reachable="N/A - CloneCast's source has no HTTP client code for this platform",
            real_upload_code_exists=False,
            notes=(
                f"{len(configured_vars)}/{len(env_vars)} common credential env var(s) set. "
                "CloneCast has no real upload/API-calling code for this platform (confirmed by source "
                "inspection) - configuring credentials would have no effect without further development."
            ),
        ))
    return statuses


def run_workflow_test(repo_path: str, port: int = 8000, include_publishing: bool = False) -> WorkflowTestReport:
    repo_path = os.path.abspath(repo_path)
    t0 = time.time()
    disp = None
    disp_db = None
    proc = None
    report = WorkflowTestReport()
    report.repo_path = repo_path
    report.include_publishing = include_publishing
    prod_db = os.path.join(repo_path, "db", "cloneshow.db")
    report.production_db_path = prod_db
    try:
        report.clonecast_git_status_before = subprocess.run(
            ["git", "status", "--short"], cwd=repo_path, text=True, capture_output=True
        ).stdout.strip()
    except Exception as exc:
        report.clonecast_git_status_before = f"GIT_STATUS_FAILED: {exc}"
    if os.path.isfile(prod_db):
        report.production_db_before = _sha256_file(prod_db)
        report.production_db_size_before = os.path.getsize(prod_db)

    try:
        git_state = scanner._git_info(repo_path)[1]
    except Exception as exc:
        git_state = "unknown"
        report.stages.append(StageRecord(
            number=0,
            stage="ISOLATION_PROOF",
            status="FAIL",
            failure_reason=f"Unable to inspect target git status: {exc}",
            failure_ownership="AUTOCORP_WORKFLOW_ENGINE_DEFECT",
        ))
        report.overall_status = "SAFETY_BLOCKED"
        report.first_failure = report.stages[-1].failure_reason
        _finalize(report, prod_db, t0, disp, disp_db); return report

    if git_state != "clean":
        s = StageRecord(number=0, stage="ISOLATION_PROOF", status="FAIL",
                         failure_reason="Dirty working tree.")
        report.stages.append(s); report.overall_status = "SAFETY_BLOCKED"
        report.first_failure = s.failure_reason
        _finalize(report, prod_db, t0, disp, disp_db); return report

    try:
        disp = tempfile.mkdtemp(prefix="acwf-")
    except Exception as exc:
        s = StageRecord(
            number=1,
            stage="DISPOSABLE_WORKSPACE_CREATE",
            status="FAIL",
            failure_reason=f"FAILED TO CREATE DISPOSABLE WORKSPACE: {exc}",
            failure_ownership="AUTOCORP_WORKFLOW_ENGINE_DEFECT",
        )
        report.stages.append(s)
        report.overall_status = "FAILED TO CREATE DISPOSABLE WORKSPACE"
        report.first_failure = s.failure_reason
        _finalize(report, prod_db, t0, disp, disp_db); return report
    report.disposable_root = disp
    disp_db = os.path.join(disp, "db", "cloneshow.db")
    try:
        os.makedirs(os.path.dirname(disp_db), exist_ok=True)
        if os.path.isfile(prod_db):
            shutil.copy2(prod_db, disp_db)
    except Exception as exc:
        s = StageRecord(
            number=1,
            stage="DISPOSABLE_DATABASE_COPY",
            status="FAIL",
            failure_reason=f"DATABASE COPY FAILED: {exc}",
            failure_ownership="AUTOCORP_WORKFLOW_ENGINE_DEFECT",
        )
        report.stages.append(s)
        report.overall_status = "DATABASE COPY FAILED"
        report.first_failure = s.failure_reason
        _finalize(report, prod_db, t0, disp, disp_db); return report
    if os.path.commonpath([disp, repo_path]).startswith(repo_path):
        s = StageRecord(
            number=1,
            stage="DISPOSABLE_WORKSPACE_CREATE",
            status="FAIL",
            failure_reason="Disposable workspace was created inside the target repository.",
            failure_ownership="AUTOCORP_WORKFLOW_ENGINE_DEFECT",
        )
        report.stages.append(s); report.overall_status = "SAFETY_BLOCKED"; report.first_failure = s.failure_reason
        _finalize(report, prod_db, t0, disp, disp_db); return report

    s0 = StageRecord(number=0, stage="ISOLATION_PROOF", status="PASS",
                      evidence=[f"Root: {disp}"])
    try:
        copied_refs = _prepare_disposable_voice_assets(disp_db, disp)
    except Exception as exc:
        s = StageRecord(
            number=1,
            stage="DISPOSABLE_DATABASE_COPY",
            status="FAIL",
            failure_reason=f"DATABASE COPY FAILED: {exc}",
            failure_ownership="AUTOCORP_WORKFLOW_ENGINE_DEFECT",
        )
        report.stages.append(s)
        report.overall_status = "DATABASE COPY FAILED"
        report.first_failure = s.failure_reason
        _finalize(report, prod_db, t0, disp, disp_db); return report
    s0.evidence.append(f"Copied voice references: {len(copied_refs)}")
    report.stages.append(s0)

    venv = os.path.join(repo_path, ".venv", "bin", "python")
    if not os.path.isfile(venv):
        report.overall_status = "REQUIRED_SERVICE_MISSING"
        s = StageRecord(
            number=1,
            stage="CLONECAST_ENVIRONMENT_CHECK",
            status="FAIL",
            failure_reason=f"Required Python executable is missing: {venv}",
            failure_ownership="CLONECAST_WORKFLOW_PRECONDITION",
        )
        report.stages.append(s)
        report.first_failure = s.failure_reason
        _finalize(report, prod_db, t0, disp, disp_db); return report
    if _port_listening("127.0.0.1", port):
        for alt in range(port + 1, port + 100):
            if not _port_listening("127.0.0.1", alt): port = alt; break

    env = _clonecast_env(repo_path, disp, disp_db)

    args = [venv, "-m", "uvicorn", "clonecast.web_app:create_app", "--factory", "--host", "127.0.0.1", f"--port={port}"]
    try:
        proc = subprocess.Popen(args, cwd=repo_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except Exception as exc:
        s = StageRecord(
            number=1,
            stage="APPLICATION_STARTUP",
            status="FAIL",
            failure_reason=f"APPLICATION FAILED TO START: {exc}",
            failure_ownership="CLONECAST_WORKFLOW_PRECONDITION",
        )
        report.stages.append(s)
        report.overall_status = "APPLICATION FAILED TO START"
        report.first_failure = s.failure_reason
        _finalize(report, prod_db, t0, disp, disp_db); return report
    for _ in range(40):
        if _port_listening("127.0.0.1", port): break
        if proc.poll() is not None:
            _, err = proc.communicate()
            s = StageRecord(
                number=1,
                stage="APPLICATION_STARTUP",
                status="FAIL",
                failure_reason=f"APPLICATION FAILED TO START: {err.strip()[:1000] or 'server exited before listening'}",
                failure_ownership="CLONECAST_WORKFLOW_PRECONDITION",
            )
            report.stages.append(s)
            report.overall_status = "APPLICATION FAILED TO START"
            report.first_failure = s.failure_reason
            _finalize(report, prod_db, t0, disp, disp_db); return report
        time.sleep(0.5)

    base = f"http://127.0.0.1:{port}"
    h = SessionHTTP()
    if h.request(f"{base}/health")["status_code"] != 200:
        s = StageRecord(
            number=1,
            stage="APPLICATION_HEALTHCHECK",
            status="FAIL",
            failure_reason="APPLICATION FAILED TO START: /health did not return HTTP 200",
            failure_ownership="CLONECAST_WORKFLOW_PRECONDITION",
        )
        report.stages.append(s)
        _shutdown(proc); report.overall_status = "APPLICATION FAILED TO START"; report.first_failure = s.failure_reason
        _finalize(report, prod_db, t0, disp, disp_db); return report

    time.sleep(1)
    routes = []
    or_ = h.request(f"{base}/openapi.json")
    if or_["status_code"] == 200 and or_["body"]:
        try: routes = _parse_openapi_routes(json.loads(or_["body"]))
        except: pass
    report.candidate_routes = routes

    def _stage(num: int, name: str, keywords: list, known: dict = None,
               path_params: dict = None, exact_path: str = "") -> StageRecord:
        s = StageRecord(number=num, stage=name)
        s.db_before = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""
        rt = (
            next(
                (r for r in routes if r["method"] == "POST" and _route_path_matches(r["path"], exact_path)),
                None,
            )
            if exact_path else _resolve_route(routes, keywords)
        )
        if rt is None:
            s.status = "FAIL"; s.failure_reason = "ROUTE_RESOLUTION_AMBIGUOUS"
            report.stages.append(s); report.overall_status = "STAGE_FAILED"; return s
        s.route = rt["path"]; s.method = rt["method"]; s.operation_id = rt["operation_id"]

        # Substitute path parameters
        if exact_path and "{" not in exact_path:
            resolved_path = exact_path
        else:
            resolved_path = _substitute_path_params(rt["path"], path_params)

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
        elif redirect_failure and _is_idempotent_redirect_failure(redirect_failure):
            s.status = "PASS"
            s.evidence.append(f"Idempotent existing resource: {redirect_failure}")
        elif redirect_failure:
            s.status = "FAIL"; s.failure_ownership = _classify_redirect_failure(redirect_failure)
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
        if s.status == "FAIL":
            report.overall_status = "STAGE_FAILED"
        return s

    def _fail(num: int, name: str, reason: str, ownership: str = "") -> StageRecord:
        s = StageRecord(number=num, stage=name, status="FAIL",
                        failure_reason=reason, failure_ownership=ownership)
        s.db_before = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""
        s.db_after = s.db_before
        report.stages.append(s)
        report.first_failure = reason
        report.overall_status = "STAGE_FAILED"
        return s

    def _pass(num: int, name: str, evidence: list[str] | None = None,
              ids: dict | None = None) -> StageRecord:
        s = StageRecord(number=num, stage=name, status="PASS",
                        evidence=evidence or [], extracted_ids=ids or {})
        s.db_before = _sha256_file(disp_db) if os.path.isfile(disp_db) else ""
        s.db_after = s.db_before
        report.stages.append(s)
        return s

    def _poll_job(num: int, name: str, job_id: str, timeout_seconds: int = 900) -> StageRecord:
        s = StageRecord(number=num, stage=name, method="GET", route="/api/jobs/{job_id}")
        deadline = time.time() + timeout_seconds
        last = {}
        while time.time() < deadline:
            resp = h.request(f"{base}/api/jobs/{urllib.parse.quote(job_id)}", timeout=10)
            s.response_code = resp["status_code"]
            s.response_body = resp.get("body", "")[:4000]
            try:
                last = json.loads(resp.get("body", "") or "{}")
            except json.JSONDecodeError:
                last = {}
            if last.get("status") == "succeeded":
                s.status = "PASS"
                s.evidence = [f"job_id={job_id}", f"kind={last.get('kind', '')}"]
                s.extracted_ids = {"job_id": job_id}
                report.stages.append(s)
                return s
            if last.get("status") == "failed":
                s.status = "FAIL"
                s.failure_reason = str(last.get("error", ""))[:1000]
                s.failure_ownership = _classify_job_error(s.failure_reason)
                report.stages.append(s)
                report.overall_status = "STAGE_FAILED"
                return s
            time.sleep(2)
        s.status = "FAIL"
        s.failure_reason = f"Timed out waiting for job {job_id}; last={last}"
        s.failure_ownership = "EXPECTED_ENVIRONMENT_LIMITATION"
        report.stages.append(s)
        report.overall_status = "STAGE_FAILED"
        return s

    def _start_json_job(num: int, name: str, path: str, body: dict | None = None) -> tuple[StageRecord, str]:
        s = StageRecord(number=num, stage=name, method="POST", route=path)
        s.request_body = json.dumps(body or {})[:500]
        resp = h.request(
            f"{base}{path}", method="POST", data=body,
            content_type="application/x-www-form-urlencoded", follow_redirects=False,
        )
        s.response_code = resp["status_code"]
        s.response_body = resp.get("body", "")[:4000]
        try:
            payload = json.loads(resp.get("body", "") or "{}")
        except json.JSONDecodeError:
            payload = {}
        job_id = str(payload.get("job_id") or "")
        if resp["status_code"] >= 400 or not job_id:
            s.status = "FAIL"
            s.failure_reason = resp.get("body", "")[:1000] or f"HTTP {resp['status_code']}"
            s.failure_ownership = "AUTOCORP_REQUEST_CONSTRUCTION_DEFECT" if resp["status_code"] == 422 else "CLONECAST_WORKFLOW_PRECONDITION"
        else:
            s.status = "PASS"
            s.extracted_ids = {"job_id": job_id}
            s.evidence = [f"Started job {job_id}"]
        report.stages.append(s)
        if s.status == "FAIL":
            report.overall_status = "STAGE_FAILED"
        return s, job_id

    def _create_episode_record(num: int) -> tuple[StageRecord, str]:
        s = StageRecord(number=num, stage="DISPOSABLE_EPISODE_RECORD_CREATE",
                        method="service", route="ResearchService.ingest_one + EpisodeService.create")
        research_dir = os.path.join(disp, "inputs")
        os.makedirs(research_dir, exist_ok=True)
        research_path = os.path.join(research_dir, "autocorp-disposable-research.txt")
        body = (
            "Title: AutoCorp Disposable Audio Workflow\n"
            "Source URL: https://example.com/autocorp-disposable-audio-workflow\n"
            "Source Name: AutoCorp Disposable Verification\n"
            "Published At: 2026-07-28T00:00:00Z\n"
            "Collected At: 2026-07-28T00:00:00Z\n"
            "Tags: autocorp, disposable\n"
            "External ID: autocorp-disposable-audio-workflow\n\n"
            "This disposable research record exists only to create a CloneCast episode row for audio workflow verification.\n"
        )
        with open(research_path, "w", encoding="utf-8") as f:
            f.write(body)
        code = (
            "import json; "
            "from clonecast.config import load_settings; from clonecast.db import connect_database; "
            "from clonecast.research_service import ResearchService; from clonecast.episode_service import EpisodeService; "
            "settings=load_settings(); conn=connect_database(settings.db_path); "
            "out=ResearchService(conn, settings).ingest_one(%r); "
            "assert out.status == 'accepted', out; "
            "ep=EpisodeService(conn).create([out.research_id], 'AutoCorp Disposable Audio Workflow', 'autocorp-disposable-episode'); "
            "print(json.dumps({'research_id': out.research_id, 'episode_id': ep.episode_id})); conn.close()"
        ) % research_path
        proc2 = subprocess.run([venv, "-c", code], cwd=repo_path, env=env, text=True, capture_output=True, timeout=60)
        s.response_code = proc2.returncode
        s.response_body = (proc2.stdout + proc2.stderr)[:4000]
        if proc2.returncode != 0:
            s.status = "FAIL"; s.failure_reason = s.response_body; s.failure_ownership = "CLONECAST_WORKFLOW_PRECONDITION"
            report.stages.append(s); return s, ""
        try:
            payload = json.loads(proc2.stdout.strip().splitlines()[-1])
        except Exception:
            s.status = "FAIL"; s.failure_reason = "Episode service returned non-JSON output"; s.failure_ownership = "CLONECAST_APPLICATION_DEFECT"
            report.stages.append(s); return s, ""
        episode_id = payload.get("episode_id", "")
        if not _db_record_exists(disp_db, "episodes", "episode_id", episode_id):
            s.status = "FAIL"; s.failure_reason = "Created episode row not found in disposable DB"; s.failure_ownership = "CLONECAST_APPLICATION_DEFECT"
        else:
            s.status = "PASS"; s.extracted_ids = payload; s.evidence = [f"episode_id={episode_id}", f"research_id={payload.get('research_id', '')}"]
        report.stages.append(s)
        return s, episode_id

    def _create_and_run_qc(num: int, integration_job_id: str) -> tuple[StageRecord, str]:
        """No HTTP route creates/runs a QC request (only human-review and
        readiness-create are exposed over HTTP) - invoke the real service
        directly via subprocess, exactly like _create_episode_record does for
        research/episode creation."""
        s = StageRecord(number=num, stage="QC_REQUEST_CREATE_AND_RUN",
                        method="service", route="RadioEpisodeQCService.create_qc_request + run_qc")
        code = (
            "import json; "
            "from clonecast.config import load_settings; from clonecast.db import connect_database; "
            "from clonecast.radio_episode_qc_service import RadioEpisodeQCService; "
            "settings=load_settings(); conn=connect_database(settings.db_path); "
            "svc=RadioEpisodeQCService(conn, settings); "
            "req=svc.create_qc_request(%r, 'autocorp-disposable-qc'); "
            "result=svc.run_qc(req['qc_request_id']); "
            "print(json.dumps({'qc_request_id': req['qc_request_id'], 'status': result.get('status')})); conn.close()"
        ) % integration_job_id
        proc2 = subprocess.run([venv, "-c", code], cwd=repo_path, env=env, text=True, capture_output=True, timeout=120)
        s.response_code = proc2.returncode
        s.response_body = (proc2.stdout + proc2.stderr)[:4000]
        if proc2.returncode != 0:
            s.status = "FAIL"; s.failure_reason = s.response_body; s.failure_ownership = "CLONECAST_WORKFLOW_PRECONDITION"
            report.stages.append(s); return s, ""
        try:
            payload = json.loads(proc2.stdout.strip().splitlines()[-1])
        except Exception:
            s.status = "FAIL"; s.failure_reason = "QC service returned non-JSON output"; s.failure_ownership = "CLONECAST_APPLICATION_DEFECT"
            report.stages.append(s); return s, ""
        qc_request_id = payload.get("qc_request_id", "")
        qc_row = _db_one(disp_db, "SELECT status FROM radio_episode_qc_requests WHERE qc_request_id=?", (qc_request_id,))
        if qc_row.get("status") != "completed":
            s.status = "FAIL"; s.failure_reason = f"QC request did not reach completed status: {qc_row}"; s.failure_ownership = "CLONECAST_WORKFLOW_PRECONDITION"
            report.stages.append(s); return s, qc_request_id
        s.status = "PASS"; s.extracted_ids = {"qc_request_id": qc_request_id}
        s.evidence = [f"qc_request_id={qc_request_id}", f"status={qc_row.get('status')}"]
        report.stages.append(s)
        return s, qc_request_id

    def _evaluate_qc_checks(num: int, qc_request_id: str) -> StageRecord:
        """Read-only: query every individual QC check row and classify it.
        Blocking-severity failures genuinely block release readiness (the
        disposable DB has trigger-level enforcement of this, not just
        application code), so this stage must fail cleanly if any exist -
        proceeding would just hit a SQLite RAISE(ABORT) at the next stage."""
        s = StageRecord(number=num, stage="QC_CHECKS_EVALUATE", method="query", route="radio_episode_qc_checks")
        checks = _db_all(
            disp_db,
            "SELECT check_code, category, severity, status, measured_value, measured_text, expected_value, units "
            "FROM radio_episode_qc_checks WHERE qc_request_id=?",
            (qc_request_id,),
        )
        blocking_failed = [c for c in checks if c["severity"] == "blocking" and c["status"] == "failed"]
        advisory_issues = [c for c in checks if c["severity"] == "advisory" and c["status"] in ("failed", "warning")]
        passed = [c for c in checks if c["status"] == "passed"]
        for c in checks:
            if c["severity"] == "blocking" and c["status"] == "failed":
                sev = "FAIL"
            elif c["status"] in ("failed", "warning"):
                sev = "WARNING"
            else:
                continue
            report.publishing_findings.append(PublishingFinding(
                severity=sev, category=f"qc:{c['category']}",
                evidence=(f"{c['check_code']}: status={c['status']} "
                          f"measured={c.get('measured_value') if c.get('measured_value') is not None else c.get('measured_text')} "
                          f"expected={c.get('expected_value')} {c.get('units') or ''}").strip(),
                recommendation=("Investigate and fix this QC check before approving release."
                               if sev == "FAIL" else
                               "Advisory only - review but does not block release readiness."),
            ))
        if blocking_failed:
            s.status = "FAIL"
            s.failure_reason = f"{len(blocking_failed)} blocking QC check(s) failed: {[c['check_code'] for c in blocking_failed]}"
            s.failure_ownership = "CLONECAST_WORKFLOW_PRECONDITION"
        else:
            s.status = "PASS"
            s.evidence = [f"{len(passed)} passed, {len(advisory_issues)} advisory issue(s), 0 blocking failures",
                          f"qc_request_id={qc_request_id}"]
        report.stages.append(s)
        return s

    # Stage 2: Studio creation
    s = _stage(2, "STUDIO_CREATION", ["studio", "create"],
                {"display_name": "AutoCorp Disposable Test Studio", "show_format": "solo_host"})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    studio_id = s.extracted_ids.get("studio_id", "") or ""

    # Stage 2b: Studio validation (required before review)
    s = _stage(3, "STUDIO_VALIDATE", ["validate_studio", "studios__studio_id__validate"],
                path_params={"studio_id": studio_id} if studio_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    report.overall_status = "DISPOSABLE_STUDIO_VALIDATED"

    # Stage 2c: Studio review (required before approval)
    s = _stage(4, "STUDIO_REVIEW", ["review_studio", "studios__studio_id__review"],
                path_params={"studio_id": studio_id} if studio_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    report.overall_status = "DISPOSABLE_STUDIO_VALIDATED"

    # Stage 2c: Studio approval (required before activation)
    s = _stage(5, "STUDIO_APPROVAL", ["approve_studio", "studios__studio_id__approve"],
                path_params={"studio_id": studio_id} if studio_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    report.overall_status = "DISPOSABLE_STUDIO_APPROVED"

    # Stage 2c: Studio activation (required before episode creation)
    s = _stage(6, "STUDIO_ACTIVATION", ["activate_studio", "studios__studio_id__activate"],
                path_params={"studio_id": studio_id} if studio_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    report.overall_status = "DISPOSABLE_STUDIO_READY"

    # Stage 3: Host character creation (required before episode creation)
    host_name = f"AutoCorp Disp Host {report.disposable_root[-6:]}"
    s = _stage(7, "HOST_CHARACTER", ["create_character", "studios__studio_id__characters__create"],
                {"display_name": host_name, "role": "host"},
                path_params={"studio_id": studio_id} if studio_id else None)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    # Stage 3b: Recover character ID from studio page HTML
    character_id = ""
    try:
        resp = h.request(f"{base}/studios?studio_id={studio_id}", follow_redirects=True)
        character_id = _extract_char_id_from_html(resp.get("body", ""), host_name)
    except Exception:
        pass

    s_cid = StageRecord(number=8, stage="CHARACTER_ID_RECOVERY")
    if character_id:
        s_cid.status = "PASS"
        s_cid.evidence = [f"character_id={character_id}"]
        s_cid.extracted_ids = {"character_id": character_id}
    else:
        s_cid.status = "FAIL"
        s_cid.failure_reason = "Cannot recover character ID from studio page."
        report.stages.append(s_cid)
        report.first_failure = s_cid.failure_reason
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    report.stages.append(s_cid)

    # Stage 3c: Character validation/review/approval are CloneCast lifecycle preconditions.
    s = _stage(9, "HOST_VALIDATE", ["validate_character", "characters__character_id__validate"],
                path_params={"studio_id": studio_id, "character_id": character_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s = _stage(10, "HOST_REVIEW", ["review_character", "characters__character_id__review"],
                {"reviewer": "Larry", "decision": "accepted"},
                path_params={"studio_id": studio_id, "character_id": character_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s = _stage(11, "HOST_APPROVAL", ["approve_character", "characters__character_id__approve"],
                {"approver": "Larry"},
                path_params={"studio_id": studio_id, "character_id": character_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    voice_profile_id = _find_approved_voice_profile(disp_db)
    preset_id = _find_active_radio_audio_preset(disp_db)
    s = _stage(12, "HOST_VOICE_ASSIGN", [],
                {"voice_profile_id": voice_profile_id, "preset_id": preset_id},
                exact_path=f"/studios/{studio_id}/characters/{character_id}/assign-voice")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    # Stage 3d: Character activation
    s = _stage(13, "HOST_ACTIVATION", ["activate_character", "characters__character_id__activate"],
                path_params={"studio_id": studio_id, "character_id": character_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    # Stage 4: Episode start (with real studio_id)
    s = _stage(14, "EPISODE_START", ["episode", "start"],
                {"studio_id": studio_id, "topic": "Why careful software testing matters",
                 "research_level": "none", "length_preset": "ten", "format_key": "solo_host"})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s, episode_id = _create_episode_record(15)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s = _stage(16, "BOUND_SESSION_CREATE", [],
               {
                   "studio_id": studio_id,
                   "title": "AutoCorp Disposable Bound Audio Session",
                   "editorial_purpose": "Create a disposable audio artifact without publishing.",
                   "expected_duration_seconds": 50.0,
                   "episode_id": episode_id,
               },
               exact_path="/conversations/sessions/create")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    session_id = s.extracted_ids.get("session_id", "") or ""

    s = _stage(17, "BOUND_SESSION_CONFIGURE", [],
               exact_path=f"/conversations/sessions/{session_id}/configure")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s = _stage(18, "BOUND_SESSION_SEGMENT_CREATE", [],
               {
                   "segment_type": "topic_setup",
                   "title_or_purpose": "Disposable audio verification",
                   "position": 0,
                   "planned_duration_seconds": 50.0,
               },
               exact_path=f"/conversations/sessions/{session_id}/segments/create")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    for num, name, path in (
        (19, "BOUND_SESSION_VALIDATE", f"/conversations/sessions/{session_id}/validate"),
        (20, "BOUND_SESSION_REVIEW", f"/conversations/sessions/{session_id}/review"),
        (21, "BOUND_SESSION_APPROVAL", f"/conversations/sessions/{session_id}/approve"),
        (22, "BOUND_SESSION_START", f"/conversations/sessions/{session_id}/start"),
    ):
        known = {}
        if name == "BOUND_SESSION_REVIEW":
            known = {"reviewer": "Larry", "decision": "accepted", "notes": "AutoCorp disposable review."}
        elif name == "BOUND_SESSION_APPROVAL":
            known = {"approver": "Larry"}
        s = _stage(num, name, [], known, exact_path=path)
        if s.status != "PASS":
            report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    segment = _db_one(
        disp_db,
        "SELECT segment_id FROM radio_rundown_segments WHERE session_id=? ORDER BY position LIMIT 1",
        (session_id,),
    )
    segment_id = segment.get("segment_id", "")
    if not segment_id:
        _fail(23, "SEGMENT_ID_RECOVERY", "Created segment row not found in disposable DB.", "CLONECAST_APPLICATION_DEFECT")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    _pass(23, "SEGMENT_ID_RECOVERY", [f"segment_id={segment_id}"], {"segment_id": segment_id})

    s = _stage(24, "CONVERSATION_CREATE", [],
               {
                   "studio_id": studio_id,
                   "session_id": session_id,
                   "segment_id": segment_id,
                   "host_character_id": character_id,
                   "editorial_purpose": "Create a disposable script section for audio verification.",
                   "intended_topic": "Why careful software testing matters",
                   "target_duration_seconds": 50.0,
               },
               exact_path="/conversations/create")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    conversation_id = s.extracted_ids.get("conversation_id", "") or ""

    s = _stage(25, "CONVERSATION_CONFIGURE", [], {"session_id": session_id},
               exact_path=f"/conversations/{conversation_id}/configure")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s = _stage(26, "CONVERSATION_PARTICIPANT_SNAPSHOT", [], {"session_id": session_id},
               exact_path=f"/conversations/{conversation_id}/snapshot-participants")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    participant = _db_one(
        disp_db,
        "SELECT participant_id FROM ai_conversation_participants WHERE conversation_id=? AND participant_type='host' AND snapshot_status='active' LIMIT 1",
        (conversation_id,),
    )
    participant_id = participant.get("participant_id", "")
    if not participant_id:
        _fail(27, "PARTICIPANT_ID_RECOVERY", "Host participant snapshot not found.", "CLONECAST_WORKFLOW_PRECONDITION")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    _pass(27, "PARTICIPANT_ID_RECOVERY", [f"participant_id={participant_id}"], {"participant_id": participant_id})

    s = _stage(28, "CONVERSATION_BLUEPRINT_ADD", [],
               {
                   "session_id": session_id,
                   "position": 0,
                   "intended_speaker_participant_id": participant_id,
                   "turn_purpose": "deliver the disposable verification script",
                   "topic": "Why careful software testing matters",
                   "emotional_direction": "clear",
                   "evidence_requirement": "",
                   "expected_duration_seconds": 50.0,
                   "closing_behavior": "hard_close",
               },
               exact_path=f"/conversations/{conversation_id}/blueprint/add")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s = _stage(29, "CONVERSATION_VALIDATE", [], {"session_id": session_id},
               exact_path=f"/conversations/{conversation_id}/validate")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s, job_id = _start_json_job(30, "DIALOGUE_GENERATION_START", f"/conversations/{conversation_id}/generate-dialogue")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    s = _poll_job(31, "DIALOGUE_GENERATION_COMPLETE", job_id, timeout_seconds=420)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; report.overall_status = "EXTERNAL_DEPENDENCY_BLOCKED"; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    generation = _db_one(
        disp_db,
        "SELECT generation_id FROM ai_conversation_generations WHERE conversation_id=? AND status='succeeded' ORDER BY completed_at DESC LIMIT 1",
        (conversation_id,),
    )
    generation_id = generation.get("generation_id", "")
    if not generation_id:
        _fail(32, "GENERATION_ID_RECOVERY", "Succeeded generation row not found.", "CLONECAST_APPLICATION_DEFECT")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    _pass(32, "GENERATION_ID_RECOVERY", [f"generation_id={generation_id}"], {"generation_id": generation_id})

    s = _stage(33, "CONVERSATION_REVIEW", [],
               {"session_id": session_id, "generation_id": generation_id, "reviewer": "Larry", "decision": "accepted"},
               exact_path=f"/conversations/{conversation_id}/review")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s = _stage(34, "CONVERSATION_APPROVAL", [], {"session_id": session_id, "approver": "Larry"},
               exact_path=f"/conversations/{conversation_id}/approve")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s, job_id = _start_json_job(35, "VOICE_GENERATION_START", f"/conversations/{conversation_id}/render-voices")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    s = _poll_job(36, "VOICE_GENERATION_COMPLETE", job_id, timeout_seconds=1200)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; report.overall_status = "EXTERNAL_DEPENDENCY_BLOCKED"; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    render = _db_one(
        disp_db,
        "SELECT render_job_id FROM conversation_voice_render_jobs WHERE conversation_id=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",
        (conversation_id,),
    )
    render_job_id = render.get("render_job_id", "")
    if not render_job_id:
        _fail(37, "VOICE_RENDER_JOB_RECOVERY", "Completed voice render row not found.", "CLONECAST_APPLICATION_DEFECT")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    turn_assets = _db_all(
        disp_db,
        "SELECT output_path, file_size_bytes, duration_seconds, audio_format, sample_rate, channels, output_sha256, turn_position "
        "FROM conversation_voice_turn_assets WHERE render_job_id=? AND status='completed' ORDER BY turn_position",
        (render_job_id,),
    )
    if not turn_assets:
        _fail(38, "VOICE_AUDIO_VALIDATE", "No completed voice turn assets found.", "CLONECAST_APPLICATION_DEFECT")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    for asset in turn_assets:
        path = asset.get("output_path", "")
        if not path or not _inside(path, disp) or not os.path.isfile(path) or os.path.getsize(path) <= 0:
            _fail(38, "VOICE_AUDIO_VALIDATE", f"Voice asset is missing or outside disposable root: {path}", "CLONECAST_APPLICATION_DEFECT")
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    # Independently verify EVERY generated turn WAV (real ffprobe + real
    # SHA-256, cross-checked against the DB's own recorded hash) - not just
    # existence/size, per the artifact-verification requirement.
    for asset in turn_assets:
        artifact = _verify_artifact(
            f"voice_turn_wav[{asset.get('turn_position')}]",
            asset["output_path"],
            db_sha256=asset.get("output_sha256") or "",
        )
        report.artifacts.append(artifact)
        if artifact.verify_error:
            _fail(38, "VOICE_AUDIO_VALIDATE",
                  f"Turn WAV verification failed for {asset['output_path']}: {artifact.verify_error}",
                  "CLONECAST_APPLICATION_DEFECT")
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    _pass(38, "VOICE_AUDIO_VALIDATE",
          [f"render_job_id={render_job_id}", f"turn_assets={len(turn_assets)}",
           "Every turn WAV independently verified via ffprobe + SHA-256."],
          {"render_job_id": render_job_id})

    s, job_id = _start_json_job(39, "CONVERSATION_ASSEMBLY_START", f"/conversations/{conversation_id}/assemble",
                                {"render_job_id": render_job_id})
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    s = _poll_job(40, "CONVERSATION_ASSEMBLY_COMPLETE", job_id, timeout_seconds=420)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    assembly = _db_one(
        disp_db,
        "SELECT assembly_id FROM conversation_assembly_jobs WHERE render_job_id=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",
        (render_job_id,),
    )
    assembly_id = assembly.get("assembly_id", "")
    if not assembly_id:
        _fail(41, "CONVERSATION_ASSEMBLY_RECOVERY", "Completed conversation assembly row not found.", "CLONECAST_APPLICATION_DEFECT")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    _pass(41, "CONVERSATION_ASSEMBLY_RECOVERY", [f"assembly_id={assembly_id}"], {"assembly_id": assembly_id})

    conv_asset = _db_one(
        disp_db,
        "SELECT managed_path, sha256, file_size_bytes, duration_seconds, sample_rate, channels, codec, bit_rate "
        "FROM conversation_assembly_assets WHERE assembly_id=? AND asset_type='listening_mp3' LIMIT 1",
        (assembly_id,),
    )
    conv_managed_path = conv_asset.get("managed_path", "")
    conv_mp3_path = conv_managed_path if os.path.isabs(conv_managed_path) else os.path.join(disp, conv_managed_path)
    if not conv_managed_path or not _inside(conv_mp3_path, disp):
        _fail(42, "CONVERSATION_AUDIO_VERIFY",
              f"Conversation assembly listening_mp3 asset missing or outside disposable root: {conv_managed_path!r}",
              "CLONECAST_APPLICATION_DEFECT")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    conv_artifact = _verify_artifact("conversation_listening_mp3", conv_mp3_path, db_sha256=conv_asset.get("sha256") or "")
    report.artifacts.append(conv_artifact)
    if conv_artifact.verify_error or conv_artifact.duration_seconds <= 0:
        _fail(42, "CONVERSATION_AUDIO_VERIFY",
              f"Conversation assembly MP3 failed verification: {conv_artifact.verify_error or 'non-positive duration'}",
              "CLONECAST_APPLICATION_DEFECT")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    _pass(42, "CONVERSATION_AUDIO_VERIFY",
          [f"path={conv_artifact.path}", f"size={conv_artifact.size_bytes}",
           f"duration={conv_artifact.duration_seconds}", f"bitrate_db={conv_asset.get('bit_rate')}",
           f"sample_rate={conv_artifact.sample_rate}", f"channels={conv_artifact.channels}",
           f"sha256_matches_db={conv_artifact.sha256_matches_db}"])

    s = _stage(43, "EPISODE_PLAN_CREATE", [],
               {"episode_id": episode_id, "studio_id": studio_id, "title": "AutoCorp Disposable Episode Assembly Plan"},
               exact_path="/conversations/episode-plans/create")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    plan_id = s.extracted_ids.get("plan_id", "") or ""

    s = _stage(44, "EPISODE_PLAN_ADD_CONVERSATION", [],
               {"position": 0, "source_conversation_assembly_id": assembly_id},
               exact_path=f"/conversations/episode-plans/{plan_id}/components/add-conversation")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

    s, job_id = _start_json_job(45, "EPISODE_ASSEMBLY_START", f"/conversations/episode-plans/{plan_id}/assemble")
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    s = _poll_job(46, "EPISODE_ASSEMBLY_COMPLETE", job_id, timeout_seconds=420)
    if s.status != "PASS":
        report.first_failure = s.failure_reason; _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    integration = _db_one(
        disp_db,
        "SELECT job_id FROM radio_episode_integration_jobs WHERE plan_id=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",
        (plan_id,),
    )
    integration_job_id = integration.get("job_id", "")
    if not integration_job_id:
        _fail(47, "EPISODE_ASSEMBLY_RECOVERY", "Completed radio episode integration row not found.", "CLONECAST_APPLICATION_DEFECT")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    asset = _db_one(
        disp_db,
        "SELECT * FROM radio_episode_integration_assets WHERE job_id=? AND asset_type='listening_mp3' LIMIT 1",
        (integration_job_id,),
    )
    managed_path = asset.get("managed_path", "")
    artifact_path = managed_path if os.path.isabs(managed_path) else os.path.join(disp, managed_path) if managed_path else ""
    meta = _verify_artifact("episode_listening_mp3", artifact_path, db_sha256=asset.get("sha256") or "") if artifact_path else AudioArtifactRecord(kind="episode_listening_mp3", path=artifact_path)
    error = meta.verify_error
    if (
        not artifact_path
        or not _inside(artifact_path, disp)
        or not os.path.isfile(artifact_path)
        or os.path.getsize(artifact_path) <= 0
        or error
        or meta.duration_seconds <= 0
        or meta.codec not in {"mp3", "pcm_s16le", "aac", "flac", "opus"}
    ):
        _fail(48, "FINAL_AUDIO_ARTIFACT_VERIFY",
              f"Final artifact verification failed path={artifact_path!r} ffprobe_error={error!r}",
              "CLONECAST_APPLICATION_DEFECT")
        _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
    report.audio_artifact = meta
    report.artifacts.append(meta)
    _pass(48, "FINAL_AUDIO_ARTIFACT_VERIFY",
          [
              f"path={meta.path}",
              f"size={meta.size_bytes}",
              f"duration={meta.duration_seconds}",
              f"codec={meta.codec}",
              f"sample_rate={meta.sample_rate}",
              f"channels={meta.channels}",
              f"sha256={meta.sha256}",
              f"sha256_matches_db={meta.sha256_matches_db}",
          ],
          {"integration_job_id": integration_job_id})

    if include_publishing:
        def _resolve_pkg_path(rel_or_abs: str) -> str:
            if not rel_or_abs:
                return ""
            return rel_or_abs if os.path.isabs(rel_or_abs) else os.path.join(disp, rel_or_abs)

        def _verify_generic_file(kind: str, path: str, db_sha256: str) -> AudioArtifactRecord:
            """Non-audio artifact check (metadata.json/manifest.json/
            checksums.sha256): existence + size + SHA-256 cross-check against
            the DB's own recorded hash. No ffprobe (these aren't media)."""
            rec = AudioArtifactRecord(kind=kind, path=path)
            if not path or not os.path.isfile(path):
                rec.verify_error = "File does not exist"
                return rec
            rec.size_bytes = os.path.getsize(path)
            rec.sha256 = _sha256_file(path)
            rec.db_sha256 = db_sha256 or ""
            rec.sha256_matches_db = (not db_sha256) or (rec.sha256 == db_sha256)
            rec.verified = True
            if not rec.sha256_matches_db:
                rec.verify_error = "SHA-256 mismatch between database record and on-disk file"
            return rec

        # External dependency check and thumbnail/artwork finding run FIRST
        # and unconditionally: neither depends on QC/packaging/publication
        # succeeding, so they must be recorded even if the pipeline stops
        # early below (safety rule: "continue remaining safe validation").
        report.external_dependency_status = _check_external_publishing_dependencies()
        report.publishing_findings.append(PublishingFinding(
            severity="WARNING", category="thumbnail_artwork",
            evidence="CloneCast has no thumbnail/artwork asset pipeline. `artwork_reference` in the "
                     "platform-export form is an unvalidated free-text string - no image file is "
                     "generated, stored, hashed, or dimension-checked anywhere in the codebase.",
            recommendation="Implement a real cover-art/thumbnail asset pipeline (storage, dimension "
                          "validation, hashing) before external platform publishing is attempted.",
        ))

        # Stage 49-50: QC (no HTTP route creates/runs it - real service call)
        s, qc_request_id = _create_and_run_qc(49, integration_job_id)
        if s.status != "PASS":
            report.first_failure = s.failure_reason; report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

        s = _evaluate_qc_checks(50, qc_request_id)
        if s.status != "PASS":
            report.first_failure = s.failure_reason; report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

        # Stage 51: human QC review (required before release readiness)
        s = _stage(51, "QC_HUMAN_REVIEW", [],
                   {"reviewer_label": "Larry", "decision": "approved",
                    "notes": "AutoCorp disposable QC review."},
                   exact_path=f"/publishing/qc/{qc_request_id}/review")
        if s.status != "PASS":
            report.first_failure = s.failure_reason; report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

        # Stage 52: release readiness (DB-trigger-enforced: completed QC +
        # approved review + zero blocking failures, all re-checked above)
        s = _stage(52, "RELEASE_READINESS_CREATE", [], {},
                   exact_path=f"/publishing/qc/{qc_request_id}/readiness/create")
        if s.status != "PASS":
            report.first_failure = s.failure_reason; report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
        readiness = _db_one(
            disp_db,
            "SELECT readiness_id FROM radio_episode_release_readiness WHERE qc_request_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (qc_request_id,),
        )
        readiness_id = readiness.get("readiness_id", "")
        if not readiness_id:
            _fail(52, "RELEASE_READINESS_ID_RECOVERY", "Release readiness row not found.", "CLONECAST_APPLICATION_DEFECT")
            report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

        # Stage 53: release package creation (episode.mp3 + metadata.json +
        # manifest.json + checksums.sha256)
        s = _stage(53, "RELEASE_PACKAGE_CREATE", [], {"readiness_id": readiness_id},
                   exact_path="/publishing/packages/create")
        if s.status != "PASS":
            report.first_failure = s.failure_reason; report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
        pkg_row = _db_one(
            disp_db,
            "SELECT package_id, audio_path, metadata_path, manifest_path, checksums_path, "
            "audio_sha256, metadata_sha256, manifest_sha256, checksums_sha256 "
            "FROM radio_release_packages WHERE readiness_id=? ORDER BY created_at DESC LIMIT 1",
            (readiness_id,),
        )
        package_id = pkg_row.get("package_id", "")
        if not package_id:
            _fail(53, "RELEASE_PACKAGE_ID_RECOVERY", "Release package row not found.", "CLONECAST_APPLICATION_DEFECT")
            report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

        # Stage 54: release package validation
        s = _stage(54, "RELEASE_PACKAGE_VALIDATE", [], {},
                   exact_path=f"/publishing/packages/{package_id}/validate")
        if s.status != "PASS":
            report.first_failure = s.failure_reason; report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

        # Stage 55: independently verify every package artifact (metadata,
        # manifest, checksums, and the packaged episode MP3 itself)
        audio_artifact = _verify_artifact("package_episode_mp3", _resolve_pkg_path(pkg_row.get("audio_path", "")),
                                          db_sha256=pkg_row.get("audio_sha256") or "")
        report.artifacts.append(audio_artifact)
        metadata_artifact = _verify_generic_file("package_metadata_json", _resolve_pkg_path(pkg_row.get("metadata_path", "")),
                                                 pkg_row.get("metadata_sha256") or "")
        report.artifacts.append(metadata_artifact)
        manifest_artifact = _verify_generic_file("package_manifest_json", _resolve_pkg_path(pkg_row.get("manifest_path", "")),
                                                 pkg_row.get("manifest_sha256") or "")
        report.artifacts.append(manifest_artifact)
        checksums_artifact = _verify_generic_file("package_checksums_sha256", _resolve_pkg_path(pkg_row.get("checksums_path", "")),
                                                   pkg_row.get("checksums_sha256") or "")
        report.artifacts.append(checksums_artifact)
        pkg_errors = [a.verify_error for a in (audio_artifact, metadata_artifact, manifest_artifact, checksums_artifact)
                     if a.verify_error]
        if pkg_errors:
            _fail(55, "RELEASE_PACKAGE_ARTIFACT_VERIFY", "; ".join(pkg_errors), "CLONECAST_APPLICATION_DEFECT")
            report.first_failure = "; ".join(pkg_errors); report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
        _pass(55, "RELEASE_PACKAGE_ARTIFACT_VERIFY",
              [f"package_id={package_id}",
               "episode.mp3/metadata.json/manifest.json/checksums.sha256 all independently verified"],
              {"package_id": package_id})

        # Stage 56: local publication (destination is server-side hardcoded to
        # "local" via /publish-local - there is no route or code path in
        # CloneCast that can reach an external destination)
        s = _stage(56, "PUBLICATION_CREATE_LOCAL", [], {},
                   exact_path=f"/publishing/packages/{package_id}/publish-local")
        if s.status != "PASS":
            report.first_failure = s.failure_reason; report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
        pub_row = _db_one(
            disp_db,
            "SELECT publication_id, destination_type, destination_path, audio_sha256 "
            "FROM radio_release_publications WHERE package_id=? ORDER BY created_at DESC LIMIT 1",
            (package_id,),
        )
        publication_id = pub_row.get("publication_id", "")
        if not publication_id:
            _fail(56, "PUBLICATION_ID_RECOVERY", "Release publication row not found.", "CLONECAST_APPLICATION_DEFECT")
            report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
        # SAFETY PROOF: destination_type is DB-CHECK-constrained to 'local'
        # only (migration 0030) - this assertion can never fail unless the
        # schema itself has been changed to allow a real external destination.
        if pub_row.get("destination_type") != "local":
            _fail(56, "PUBLICATION_DESTINATION_SAFETY_CHECK",
                  f"UNEXPECTED destination_type: {pub_row.get('destination_type')!r} (expected 'local' only)",
                  "CLONECAST_APPLICATION_DEFECT")
            report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

        # Stage 57: publication validation
        s = _stage(57, "PUBLICATION_VALIDATE", [], {},
                   exact_path=f"/publishing/publications/{publication_id}/validate")
        if s.status != "PASS":
            report.first_failure = s.failure_reason; report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report

        # Stage 58: verify the published local copy is byte-identical to source
        published_dir = _resolve_pkg_path(pub_row.get("destination_path", ""))
        published_mp3 = os.path.join(published_dir, "episode.mp3") if published_dir else ""
        if not published_mp3 or not os.path.isfile(published_mp3):
            _fail(58, "PUBLICATION_ARTIFACT_VERIFY", f"Published local copy not found: {published_mp3!r}",
                  "CLONECAST_APPLICATION_DEFECT")
            report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
        published_artifact = _verify_artifact("published_local_episode_mp3", published_mp3,
                                              db_sha256=pub_row.get("audio_sha256") or "")
        report.artifacts.append(published_artifact)
        if published_artifact.verify_error:
            _fail(58, "PUBLICATION_ARTIFACT_VERIFY", published_artifact.verify_error, "CLONECAST_APPLICATION_DEFECT")
            report.first_failure = published_artifact.verify_error; report.publishing_readiness = "FAIL"
            _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db); return report
        _pass(58, "PUBLICATION_ARTIFACT_VERIFY",
              [f"publication_id={publication_id}", f"destination_type={pub_row.get('destination_type')}",
               "Published local copy verified byte-identical to source (SHA-256 match)."],
              {"publication_id": publication_id})

        # Stages 59-63: platform "export" for every named destination. Each of
        # these is proven local-only (see Phase 1Y research): the route writes
        # upload_status=not_uploaded_no_platform_api_configured and never
        # makes an outbound network call for any platform.
        platforms = ("spotify_rss", "rumble", "youtube", "tiktok_audio_visualizer",
                     "facebook_instagram_audio_visualizer")
        export_num = 59
        for platform in platforms:
            s = _stage(export_num, f"PLATFORM_EXPORT_{platform.upper()}", [],
                       {
                           "title": "AutoCorp Disposable Episode Export",
                           "description": "Disposable verification-only export. No upload is performed.",
                           "artwork_reference": "placeholder-artwork-reference.png",
                           "ai_disclosure": "This is AI-generated disposable test content.",
                       },
                       exact_path=f"/publishing/packages/{package_id}/export/{platform}")
            if s.status == "PASS":
                found_metadata = None
                for root, _dirs, files in os.walk(disp):
                    if "platform-metadata.json" in files and platform in root:
                        try:
                            with open(os.path.join(root, "platform-metadata.json"), encoding="utf-8") as fh:
                                found_metadata = json.load(fh)
                        except Exception:
                            found_metadata = None
                        break
                upload_status = (found_metadata or {}).get("upload_status", "")
                if found_metadata is None:
                    report.publishing_findings.append(PublishingFinding(
                        severity="WARNING", category=f"platform_export:{platform}",
                        evidence="Export route returned success but platform-metadata.json was not located "
                                 "on disk to independently confirm no-upload status.",
                        recommendation="Manually confirm the export directory layout if this recurs.",
                    ))
                elif upload_status != "not_uploaded_no_platform_api_configured":
                    report.publishing_findings.append(PublishingFinding(
                        severity="FAIL", category=f"platform_export:{platform}",
                        evidence=f"platform-metadata.json upload_status was {upload_status!r}, expected "
                                 "'not_uploaded_no_platform_api_configured'.",
                        recommendation="STOP - investigate immediately whether a real upload occurred.",
                    ))
                else:
                    report.publishing_findings.append(PublishingFinding(
                        severity="PASS", category=f"platform_export:{platform}",
                        evidence=f"Export completed locally only; upload_status={upload_status!r} "
                                 "independently confirms no external upload occurred.",
                        recommendation="",
                    ))
            else:
                report.publishing_findings.append(PublishingFinding(
                    severity="WARNING", category=f"platform_export:{platform}",
                    evidence=f"Export stage did not pass: {s.failure_reason}",
                    recommendation="Investigate this specific platform export path; does not block local publishing.",
                ))
            export_num += 1

        blocking = [f for f in report.publishing_findings if f.severity == "FAIL"]
        warnings = [f for f in report.publishing_findings if f.severity == "WARNING"]
        report.publishing_readiness = "FAIL" if blocking else ("WARNING" if warnings else "PASS")

    report.overall_status = "DISPOSABLE_WORKFLOW_COMPLETE"
    report.first_failure = ""

    _shutdown(proc); _finalize(report, prod_db, t0, disp, disp_db)
    return report


def _shutdown(proc):
    if proc:
        try: proc.terminate(); proc.wait(timeout=10)
        except: pass


def _finalize(report, prod_db, t0, disp=None, disp_db=None):
    if os.path.isfile(prod_db):
        report.production_db_after = _sha256_file(prod_db)
        report.production_db_size_after = os.path.getsize(prod_db)
    if report.repo_path:
        try:
            report.clonecast_git_status_after = subprocess.run(
                ["git", "status", "--short"], cwd=report.repo_path, text=True, capture_output=True
            ).stdout.strip()
        except Exception as exc:
            report.clonecast_git_status_after = f"GIT_STATUS_FAILED: {exc}"

    # Database verification MUST run before cleanup removes the disposable DB.
    if disp_db and os.path.isfile(disp_db):
        try:
            report.database_verification = _verify_database(disp_db)
        except Exception as exc:
            report.database_verification = DatabaseVerification(error=str(exc))

    # Cleanup verification: the disposable root must be fully removable, and
    # actually removed, regardless of whether the workflow passed or failed.
    if disp:
        report.cleanup_attempted = True
        try:
            shutil.rmtree(disp, ignore_errors=False)
            report.cleanup_removed = not os.path.exists(disp)
            if not report.cleanup_removed:
                report.cleanup_error = "Disposable root still exists after rmtree."
        except Exception as exc:
            report.cleanup_removed = False
            report.cleanup_error = str(exc)

    if report.production_db_before != report.production_db_after:
        report.overall_status = "PRODUCTION_DATABASE_ACCESS_DETECTED"
    if report.clonecast_git_status_before != report.clonecast_git_status_after:
        report.overall_status = "CLONECAST_WORKTREE_CHANGED"
    if disp and report.cleanup_attempted and not report.cleanup_removed:
        report.overall_status = "CLEANUP_FAILED"
    report.duration = time.time() - t0
    report.repository_unchanged = (
        report.production_db_before == report.production_db_after
        and report.clonecast_git_status_before == report.clonecast_git_status_after
    )

    last_failed = next((s for s in report.stages if s.status == "FAIL"), None)
    last_stage = report.stages[-1] if report.stages else None
    report.workflow_stage = (last_failed or last_stage).stage if (last_failed or last_stage) else "NOT_STARTED"
    report.failure_reason = report.first_failure or (last_failed.failure_reason if last_failed else "")

    if report.include_publishing and report.publishing_readiness == "NOT_RUN":
        report.publishing_readiness = "FAIL"
        report.publishing_findings.append(PublishingFinding(
            severity="FAIL",
            category="publishing_validation",
            evidence=(
                "Publishing validation could not run because the disposable "
                f"workflow stopped at {report.workflow_stage}."
            ),
            recommendation="Resolve the earlier workflow failure before rerunning publish-test.",
        ))

    report.success = (
        report.overall_status == "DISPOSABLE_WORKFLOW_COMPLETE"
        and report.repository_unchanged
        and (not disp or (report.cleanup_attempted and report.cleanup_removed))
    )
    report.exit_code = 0 if report.success else 1

    cleanup_status = "NOT_CREATED"
    if report.cleanup_attempted:
        cleanup_status = "REMOVED" if report.cleanup_removed else "FAILED"
    database_status = "NOT_RUN"
    if report.database_verification.checked:
        database_status = "PASS" if report.database_verification.integrity_ok else "FAIL"
    elif report.database_verification.error:
        database_status = "FAIL"
    report.verification_summary = (
        f"database={database_status}; cleanup={cleanup_status}; "
        f"repository_unchanged={'yes' if report.repository_unchanged else 'no'}"
    )
    if report.success:
        report.recommended_next_action = "Review the generated report and keep production credentials disabled."
    elif report.overall_status == "SAFETY_BLOCKED":
        report.recommended_next_action = "Clean or review the target repository before rerunning disposable validation."
    elif report.overall_status == "FAILED TO CREATE DISPOSABLE WORKSPACE":
        report.recommended_next_action = "Check temporary-directory permissions and available disk space, then rerun."
    elif report.overall_status == "DATABASE COPY FAILED":
        report.recommended_next_action = "Verify the target database exists and is readable, then rerun."
    elif report.overall_status == "APPLICATION FAILED TO START":
        report.recommended_next_action = "Inspect the captured startup failure and fix the target application startup."
    elif report.overall_status == "CLEANUP_FAILED":
        report.recommended_next_action = "Remove the disposable directory manually after inspecting cleanup_error."
    else:
        report.recommended_next_action = "Resolve the reported workflow stage failure and rerun disposable validation."
    return report.exit_code
