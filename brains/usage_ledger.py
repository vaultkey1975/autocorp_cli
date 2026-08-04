#!/usr/bin/env python3
"""SQLite usage and savings ledger for provider-backed operations."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
import contextlib
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1
JSON_SCHEMA_VERSION = "2a.usage-report.v1"


def ledger_path(repo_path: str) -> str:
    return os.path.join(os.path.abspath(repo_path), "data", "autocorp_usage_ledger.sqlite3")


@dataclass
class LedgerEntry:
    repository_path: str
    operation_name: str
    provider: str
    model: str
    result_status: str
    target_path: str = ""
    routing_reason: str = ""
    explicit_user_selection: bool = False
    repository_stable_identifier: str = ""
    commit_sha: str = ""
    working_tree_fingerprint: str = ""
    context_fingerprint: str = ""
    selected_context_manifest: dict[str, Any] = field(default_factory=dict)
    prompt_bytes: int = 0
    estimated_input_tokens: int = 0
    actual_input_tokens: int | None = None
    estimated_output_tokens: int = 0
    actual_output_tokens: int | None = None
    provider_reported_usage_available: bool = False
    cache_status: str = "miss"
    validation_status: str = ""
    error_type: str = ""
    error_summary: str = ""
    elapsed_time_seconds: float = 0.0
    local_model_cleanup_requested: bool = False
    local_model_cleanup_verified: bool = False
    estimated_paid_baseline_tokens: int = 0
    estimated_paid_tokens_used: int = 0
    estimated_paid_tokens_avoided: int = 0
    estimated_savings_percentage: float | None = None
    uncertainty_warnings: list[str] = field(default_factory=list)
    operation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


@contextlib.contextmanager
def _connect(repo_path: str):
    path = ledger_path(repo_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def init_ledger(repo_path: str) -> None:
    with _connect(repo_path) as con:
        cur = con.execute("PRAGMA user_version")
        version = int(cur.fetchone()[0])
        if version not in (0, SCHEMA_VERSION):
            raise RuntimeError(f"Unknown usage ledger schema version: {version}")
        if version == 0:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_operations (
                    operation_id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    repository_stable_identifier TEXT,
                    repository_path TEXT NOT NULL,
                    commit_sha TEXT,
                    working_tree_fingerprint TEXT,
                    operation_name TEXT NOT NULL,
                    target_path TEXT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    routing_reason TEXT,
                    explicit_user_selection INTEGER NOT NULL,
                    context_fingerprint TEXT,
                    selected_context_manifest_json TEXT,
                    prompt_bytes INTEGER NOT NULL,
                    estimated_input_tokens INTEGER NOT NULL,
                    actual_input_tokens INTEGER,
                    estimated_output_tokens INTEGER NOT NULL,
                    actual_output_tokens INTEGER,
                    provider_reported_usage_available INTEGER NOT NULL,
                    cache_status TEXT NOT NULL,
                    result_status TEXT NOT NULL,
                    validation_status TEXT,
                    error_type TEXT,
                    error_summary TEXT,
                    elapsed_time_seconds REAL NOT NULL,
                    local_model_cleanup_requested INTEGER NOT NULL,
                    local_model_cleanup_verified INTEGER NOT NULL,
                    estimated_paid_baseline_tokens INTEGER NOT NULL,
                    estimated_paid_tokens_used INTEGER NOT NULL,
                    estimated_paid_tokens_avoided INTEGER NOT NULL,
                    estimated_savings_percentage REAL,
                    uncertainty_warnings_json TEXT
                )
                """
            )
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def record(entry: LedgerEntry) -> str:
    init_ledger(entry.repository_path)
    safe_manifest = _safe_manifest(entry.selected_context_manifest)
    warnings = list(entry.uncertainty_warnings)
    with _connect(entry.repository_path) as con:
        con.execute(
            """
            INSERT INTO usage_operations VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                entry.operation_id,
                entry.timestamp,
                entry.repository_stable_identifier,
                entry.repository_path,
                entry.commit_sha,
                entry.working_tree_fingerprint,
                entry.operation_name,
                entry.target_path,
                entry.provider,
                entry.model,
                entry.routing_reason,
                1 if entry.explicit_user_selection else 0,
                entry.context_fingerprint,
                json.dumps(safe_manifest, sort_keys=True),
                entry.prompt_bytes,
                entry.estimated_input_tokens,
                entry.actual_input_tokens,
                entry.estimated_output_tokens,
                entry.actual_output_tokens,
                1 if entry.provider_reported_usage_available else 0,
                entry.cache_status,
                entry.result_status,
                entry.validation_status,
                entry.error_type,
                entry.error_summary[:1000],
                entry.elapsed_time_seconds,
                1 if entry.local_model_cleanup_requested else 0,
                1 if entry.local_model_cleanup_verified else 0,
                entry.estimated_paid_baseline_tokens,
                entry.estimated_paid_tokens_used,
                entry.estimated_paid_tokens_avoided,
                entry.estimated_savings_percentage,
                json.dumps(warnings, sort_keys=True),
            ),
        )
    return entry.operation_id


def _safe_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if not manifest:
        return {}
    out = dict(manifest)
    # Raw contents/prompts are intentionally not ledger material.
    out.pop("content", None)
    out.pop("prompt", None)
    return out


def calculate_savings(baseline_tokens: int, paid_tokens_used: int) -> tuple[int, float | None]:
    avoided = max(0, int(baseline_tokens) - int(paid_tokens_used))
    if baseline_tokens <= 0:
        return avoided, None
    return avoided, round((avoided / baseline_tokens) * 100, 2)


def _rows(repo_path: str) -> list[sqlite3.Row]:
    path = ledger_path(repo_path)
    if not os.path.exists(path):
        return []
    init_ledger(repo_path)
    with _connect(repo_path) as con:
        return list(con.execute("SELECT * FROM usage_operations ORDER BY timestamp ASC"))


def report(repo_path: str) -> dict[str, Any]:
    rows = _rows(repo_path)
    by_provider: dict[str, int] = {}
    statuses = {"success": 0, "failed": 0, "blocked": 0}
    total_estimated_input = 0
    total_actual_input = 0
    actual_available = 0
    paid_avoided = 0
    baseline = 0
    paid_used = 0
    cache_hits = 0
    uncertainty = 0
    cleanup_failures = 0
    local_ops = paid_ops = 0
    for row in rows:
        provider = row["provider"]
        by_provider[provider] = by_provider.get(provider, 0) + 1
        if provider == "local":
            local_ops += 1
        else:
            paid_ops += 1
        status = row["result_status"]
        if status in statuses:
            statuses[status] += 1
        total_estimated_input += row["estimated_input_tokens"] or 0
        if row["actual_input_tokens"] is not None:
            total_actual_input += row["actual_input_tokens"]
            actual_available += 1
        paid_avoided += row["estimated_paid_tokens_avoided"] or 0
        baseline += row["estimated_paid_baseline_tokens"] or 0
        paid_used += row["estimated_paid_tokens_used"] or 0
        if row["cache_status"] == "hit":
            cache_hits += 1
        warnings = json.loads(row["uncertainty_warnings_json"] or "[]")
        if warnings:
            uncertainty += 1
        if row["local_model_cleanup_requested"] and not row["local_model_cleanup_verified"]:
            cleanup_failures += 1
    avoided, savings_pct = calculate_savings(baseline, paid_used)
    latest = dict(rows[-1]) if rows else None
    if latest:
        latest.pop("selected_context_manifest_json", None)
        latest.pop("uncertainty_warnings_json", None)
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "repo_path": os.path.abspath(repo_path),
        "total_operations": len(rows),
        "operations_by_provider": by_provider,
        "local_operations": local_ops,
        "paid_operations": paid_ops,
        "statuses": statuses,
        "total_estimated_input_tokens": total_estimated_input,
        "actual_input_tokens_available_count": actual_available,
        "total_actual_input_tokens": total_actual_input if actual_available else None,
        "estimated_paid_tokens_avoided": paid_avoided or avoided,
        "measured_savings_percentage": savings_pct,
        "cache_hits": cache_hits,
        "operations_with_uncertainty": uncertainty,
        "local_model_cleanup_failures": cleanup_failures,
        "latest_operation": latest,
    }


def render_human(summary: dict[str, Any]) -> str:
    if summary["total_operations"] == 0:
        return (
            "Usage Ledger\n"
            f"Repository: {summary['repo_path']}\n"
            "No provider usage evidence has been recorded yet.\n"
            "Estimated savings: unavailable (no evidence)."
        )
    savings = summary["measured_savings_percentage"]
    savings_text = "unavailable" if savings is None else f"{savings:.2f}%"
    latest = summary.get("latest_operation") or {}
    return "\n".join([
        "Usage Ledger",
        f"Repository: {summary['repo_path']}",
        f"Total recorded operations: {summary['total_operations']}",
        f"Operations by provider: {summary['operations_by_provider']}",
        f"Local operations: {summary['local_operations']}",
        f"Paid operations: {summary['paid_operations']}",
        f"Successful/failed/blocked: {summary['statuses']['success']} / {summary['statuses']['failed']} / {summary['statuses']['blocked']}",
        f"Total estimated input tokens: {summary['total_estimated_input_tokens']} (estimated)",
        f"Actual input tokens: {summary['total_actual_input_tokens'] if summary['total_actual_input_tokens'] is not None else 'unavailable'}",
        f"Estimated paid tokens avoided: {summary['estimated_paid_tokens_avoided']} (token estimate, not dollars)",
        f"Measured savings percentage: {savings_text}",
        f"Cache hits: {summary['cache_hits']}",
        f"Operations with uncertainty: {summary['operations_with_uncertainty']}",
        f"Local model cleanup failures: {summary['local_model_cleanup_failures']}",
        f"Latest operation: {latest.get('operation_name', '')} {latest.get('provider', '')}/{latest.get('model', '')} {latest.get('result_status', '')}".strip(),
    ])
