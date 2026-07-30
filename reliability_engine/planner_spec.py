#!/usr/bin/env python3
"""Structured subtask enforcement for planner output."""

import json
import os
from dataclasses import dataclass
from typing import Any

from core import llm
from reliability_engine import state_store


REPAIR_SYSTEM_PROMPT = """You repair malformed AutoCorp reliability plans.
Return ONLY a valid JSON object in this shape:
{"subtasks":[{"description":"...","files_touched":["relative/path.py"],"status":"pending"}]}
Use only pending status. Paths must be relative and must not contain '..'."""


@dataclass
class PlannerSpecResult:
    subtasks: list[dict]
    repairs: int = 0


def _safe_path(path: str) -> bool:
    return bool(path) and not os.path.isabs(path) and ".." not in path.split("/")


def normalize_planner_output(raw: Any) -> list[dict]:
    if not isinstance(raw, dict):
        raise ValueError("planner output must be a dict")
    if isinstance(raw.get("subtasks"), list):
        source = raw["subtasks"]
    elif isinstance(raw.get("files"), list):
        source = [
            {
                "description": f.get("purpose") or f"Implement {f.get('path', '')}",
                "files_touched": [f.get("path", "")],
                "status": "pending",
            }
            for f in raw["files"] if isinstance(f, dict)
        ]
    else:
        raise ValueError("planner output must contain subtasks or files")

    subtasks = []
    for item in source:
        if not isinstance(item, dict):
            raise ValueError("each subtask must be an object")
        description = str(item.get("description") or "").strip()
        files = item.get("files_touched", [])
        if isinstance(files, str):
            files = [files]
        cleaned = []
        for path in files:
            relpath = str(path).strip()
            if not _safe_path(relpath):
                raise ValueError(f"unsafe subtask path: {relpath}")
            cleaned.append(relpath)
        files = cleaned
        status = str(item.get("status") or "pending")
        if not description:
            raise ValueError("subtask description is required")
        if status != "pending":
            raise ValueError("new subtasks must start pending")
        subtasks.append({
            "description": description,
            "status": "pending",
            "files_touched": files,
            "blast_radius": int(item.get("blast_radius") or 0),
            "attempts": int(item.get("attempts") or 0),
        })
    if not subtasks:
        raise ValueError("planner output produced no subtasks")
    return subtasks


def validate_or_repair(raw: Any, repair_model: str | None = None, max_repairs: int = 2) -> PlannerSpecResult:
    repairs = 0
    last = raw
    while True:
        try:
            return PlannerSpecResult(normalize_planner_output(last), repairs)
        except ValueError as exc:
            if repairs >= max_repairs:
                raise
            repairs += 1
            prompt = (
                f"Validation error: {exc}\n\nMalformed output:\n"
                f"{json.dumps(last, indent=2, default=str)}\n\nRepair it now."
            )
            last = llm.generate_json(prompt, system=REPAIR_SYSTEM_PROMPT,
                                     model=repair_model or llm.MODEL)


def persist_subtasks(subtasks: list[dict], workspace: str | None = None) -> list[dict]:
    state_store.reset_subtasks()
    persisted = []
    for subtask in subtasks:
        subtask_id = state_store.create_subtask(
            subtask["description"],
            subtask.get("files_touched", []),
            subtask.get("blast_radius", 0),
            "pending",
            bool(subtask.get("inferred_target", False)),
        )
        saved = dict(subtask)
        saved["id"] = subtask_id
        persisted.append(saved)
    if workspace:
        state_store.write_project_state(workspace, persisted)
    return persisted
