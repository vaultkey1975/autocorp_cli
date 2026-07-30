#!/usr/bin/env python3
"""SQLite state helpers for the Reliability Engine.

The schema itself is installed by ``memory.store.init_db()`` so this module uses
the same database path, connection context manager, and fail-soft conventions as
the rest of AutoCorp memory.
"""

import datetime
import json
import os
from typing import Iterable

from memory import store


PROJECT_STATE = "project-state.json"
VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}


def _now() -> str:
    return datetime.datetime.now().isoformat()


def init_state() -> None:
    store.init_db()


def create_subtask(description: str, files_touched: Iterable[str] = (),
                   blast_radius: int = 0, status: str = "pending",
                   inferred_target: bool = False) -> int:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid subtask status: {status}")
    init_state()
    with store._connect() as conn:  # noqa: SLF001 - reuse repo SQLite convention
        cur = conn.execute(
            "INSERT INTO subtasks (description, status, files_touched, blast_radius, attempts, "
            "inferred_target) VALUES (?, ?, ?, ?, 0, ?)",
            (
                description,
                status,
                json.dumps(list(files_touched or [])),
                int(blast_radius or 0),
                1 if inferred_target else 0,
            ),
        )
        return int(cur.lastrowid)


def update_subtask(subtask_id: int, status: str | None = None,
                   files_touched: Iterable[str] | None = None,
                   blast_radius: int | None = None,
                   attempts: int | None = None) -> bool:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid subtask status: {status}")
    fields: list[str] = []
    params: list[object] = []
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if files_touched is not None:
        fields.append("files_touched = ?")
        params.append(json.dumps(list(files_touched)))
    if blast_radius is not None:
        fields.append("blast_radius = ?")
        params.append(int(blast_radius))
    if attempts is not None:
        fields.append("attempts = ?")
        params.append(int(attempts))
    if not fields:
        return False
    init_state()
    with store._connect() as conn:  # noqa: SLF001
        cur = conn.execute(
            f"UPDATE subtasks SET {', '.join(fields)} WHERE id = ?",
            (*params, int(subtask_id)),
        )
        return cur.rowcount > 0


def increment_attempts(subtask_id: int) -> int:
    init_state()
    with store._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE subtasks SET attempts = attempts + 1 WHERE id = ?", (int(subtask_id),))
        row = conn.execute("SELECT attempts FROM subtasks WHERE id = ?", (int(subtask_id),)).fetchone()
    return int(row["attempts"]) if row else 0


def record_attempt(subtask_id: int, attempt_num: int, model_used: str, diff: str,
                   static_gate_result: str, test_result: str, error_trace: str = "",
                   raw_output: str = "", prompt: str = "") -> int:
    init_state()
    with store._connect() as conn:  # noqa: SLF001
        cur = conn.execute(
            "INSERT INTO attempts (subtask_id, attempt_num, model_used, prompt, diff, raw_output, "
            "static_gate_result, test_result, error_trace, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(subtask_id), int(attempt_num), model_used, prompt, diff,
             raw_output, static_gate_result, test_result, error_trace, _now()),
        )
        return int(cur.lastrowid)


def record_known_issue(subtask_id: int, reason: str, last_error: str = "") -> int:
    init_state()
    with store._connect() as conn:  # noqa: SLF001
        cur = conn.execute(
            "INSERT INTO known_issues (subtask_id, reason, last_error, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (int(subtask_id), reason, last_error, _now()),
        )
        return int(cur.lastrowid)


def list_subtasks(status: str | None = None) -> list[dict]:
    init_state()
    query = "SELECT * FROM subtasks"
    params: tuple[object, ...] = ()
    if status is not None:
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY id"
    with store._connect() as conn:  # noqa: SLF001
        rows = conn.execute(query, params).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        try:
            data["files_touched"] = json.loads(data.get("files_touched") or "[]")
        except (TypeError, ValueError):
            data["files_touched"] = []
        out.append(data)
    return out


def reset_subtasks() -> None:
    init_state()
    with store._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM attempts")
        conn.execute("DELETE FROM known_issues")
        conn.execute("DELETE FROM subtasks")


def write_project_state(workspace: str, subtasks: list[dict] | None = None) -> str:
    os.makedirs(workspace, exist_ok=True)
    data = {"subtasks": subtasks if subtasks is not None else list_subtasks()}
    path = os.path.join(workspace, PROJECT_STATE)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def load_project_state(workspace: str) -> dict:
    path = os.path.join(workspace, PROJECT_STATE)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
