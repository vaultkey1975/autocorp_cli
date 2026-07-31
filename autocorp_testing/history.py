"""AutoCorp-managed test timing and result history.

Stored under AutoCorp's own `config.DATA_DIR`, never inside the target
repository. Cached timings may improve test ordering and give labeled
estimates in `test-plan`, but a cached pass is never treated as proof of a
real run — FAST/FOCUSED/FULL always execute the tests they select.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import config

HISTORY_DB_PATH = os.path.join(config.DATA_DIR, "fast_pytest_engine", "history.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextlib.contextmanager
def _connect(db_path: str | None = None):
    db_path = db_path or HISTORY_DB_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS test_history (
                repo_path              TEXT NOT NULL,
                node_id                TEXT NOT NULL,
                test_path              TEXT NOT NULL,
                branch                 TEXT,
                commit_sha             TEXT,
                repository_fingerprint TEXT,
                config_fingerprint     TEXT,
                duration_seconds       REAL,
                result                 TEXT,
                failure_type           TEXT,
                last_run_time          TEXT,
                related_source_files   TEXT,
                python_version         TEXT,
                pytest_version         TEXT,
                command                TEXT,
                mode                   TEXT,
                gpu                    INTEGER DEFAULT 0,
                database_involved      INTEGER DEFAULT 0,
                network                INTEGER DEFAULT 0,
                PRIMARY KEY (repo_path, node_id)
            )
            """
        )


@dataclass
class HistoryEntry:
    repo_path: str
    node_id: str
    test_path: str
    duration_seconds: float
    result: str
    mode: str
    branch: str = ""
    commit_sha: str = ""
    repository_fingerprint: str = ""
    config_fingerprint: str = ""
    failure_type: str | None = None
    related_source_files: list[str] | None = None
    python_version: str = ""
    pytest_version: str | None = None
    command: list[str] | None = None
    gpu: bool = False
    database_involved: bool = False
    network: bool = False


def record_result(entry: HistoryEntry, db_path: str | None = None) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO test_history (
                repo_path, node_id, test_path, branch, commit_sha,
                repository_fingerprint, config_fingerprint, duration_seconds,
                result, failure_type, last_run_time, related_source_files,
                python_version, pytest_version, command, mode, gpu,
                database_involved, network
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo_path, node_id) DO UPDATE SET
                test_path=excluded.test_path,
                branch=excluded.branch,
                commit_sha=excluded.commit_sha,
                repository_fingerprint=excluded.repository_fingerprint,
                config_fingerprint=excluded.config_fingerprint,
                duration_seconds=excluded.duration_seconds,
                result=excluded.result,
                failure_type=excluded.failure_type,
                last_run_time=excluded.last_run_time,
                related_source_files=excluded.related_source_files,
                python_version=excluded.python_version,
                pytest_version=excluded.pytest_version,
                command=excluded.command,
                mode=excluded.mode,
                gpu=excluded.gpu,
                database_involved=excluded.database_involved,
                network=excluded.network
            """,
            (
                entry.repo_path, entry.node_id, entry.test_path, entry.branch,
                entry.commit_sha, entry.repository_fingerprint, entry.config_fingerprint,
                entry.duration_seconds, entry.result, entry.failure_type, _now(),
                json.dumps(entry.related_source_files or []),
                entry.python_version, entry.pytest_version,
                json.dumps(entry.command or []), entry.mode,
                int(entry.gpu), int(entry.database_involved), int(entry.network),
            ),
        )


def previously_failed(repo_path: str, db_path: str | None = None) -> list[str]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT node_id FROM test_history WHERE repo_path = ? AND result = 'failed' "
            "ORDER BY last_run_time DESC",
            (repo_path,),
        ).fetchall()
    return [row["node_id"] for row in rows]


def get_entry(repo_path: str, node_id: str, db_path: str | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM test_history WHERE repo_path = ? AND node_id = ?",
            (repo_path, node_id),
        ).fetchone()
    return dict(row) if row else None


def timing_estimate(
    repo_path: str,
    node_id: str,
    *,
    current_config_fingerprint: str,
    db_path: str | None = None,
) -> tuple[float | None, bool]:
    """Returns (duration_seconds, is_stale). Stale means the config
    fingerprint has changed since the timing was recorded — the estimate
    is kept but labeled, never used as proof of a pass."""
    entry = get_entry(repo_path, node_id, db_path)
    if not entry or entry["duration_seconds"] is None:
        return None, False
    stale = entry["config_fingerprint"] != current_config_fingerprint
    return entry["duration_seconds"], stale
