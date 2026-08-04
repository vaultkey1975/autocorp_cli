#!/usr/bin/env python3
"""Shared repository path classification and exclusion policy.

This module is deterministic and model-free. It exists so source scanners,
test mapping, context builders, and readiness checks agree on which files are
project source and which files are generated local/runtime artifacts.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass


GENERATED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "workspace",
    "data",
    "dist",
    "build",
    "htmlcov",
    "node_modules",
    "site-packages",
}

CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox"}
VENV_DIRS = {".venv", "venv", "env"}
REPORT_DIRS = {"reports", "verification_output", "audit_output", "runtime_reports"}
SESSION_DIRS = {"guided_clonecast_episode_sessions", "sessions"}
UPLOAD_DIRS = {"uploads", "uploaded_files"}
RELIABILITY_DIRS = {".reliability_worktrees", "reliability_worktrees"}
BUILD_DIRS = {"dist", "build", "htmlcov"}

_GENERATED_FILENAMES = {
    ".coverage",
    "coverage.xml",
}

_GENERATED_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".db",
)

_GENERATED_ROOT_PATTERNS = [
    re.compile(r"^autocorp_full_verification_\d+_\d+\.txt$"),
    re.compile(r"^clonecast_.*_(report|audit)\.(txt|json)$"),
    re.compile(r"^claude_phase_.*_audit\.txt$"),
    re.compile(r"^phase_\w+_runtime_output\.txt$"),
    re.compile(r"^approved_script_.*_report\.txt$"),
]

_DOC_EXTS = {".md", ".rst", ".txt", ".adoc"}
_TEST_DIRS = {"tests", "test", "spec", "specs"}


@dataclass(frozen=True)
class PathClassification:
    rel_path: str
    category: str
    excluded: bool
    reason: str
    tracked: bool = False


def normalize_rel_path(repo_path: str, path: str) -> str:
    repo_abs = os.path.abspath(repo_path)
    full = path if os.path.isabs(path) else os.path.join(repo_abs, path)
    full = os.path.abspath(full)
    try:
        common = os.path.commonpath([repo_abs, full])
    except ValueError as exc:
        raise ValueError(f"path is outside repository: {path}") from exc
    if common != repo_abs:
        raise ValueError(f"path is outside repository: {path}")
    rel = os.path.relpath(full, repo_abs)
    return "." if rel == "." else rel.replace(os.sep, "/")


def _tracked_files(repo_path: str) -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, "ls-files", "-z"],
            capture_output=True,
            text=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()
    return {
        item.decode("utf-8", errors="replace").replace(os.sep, "/")
        for item in proc.stdout.split(b"\0")
        if item
    }


def is_tracked(repo_path: str, rel_path: str) -> bool:
    return rel_path.replace(os.sep, "/") in _tracked_files(os.path.abspath(repo_path))


def _parts(rel_path: str) -> list[str]:
    return [p for p in rel_path.replace(os.sep, "/").split("/") if p and p != "."]


def should_skip_dir(dirname: str) -> bool:
    if dirname in GENERATED_DIRS or dirname in REPORT_DIRS or dirname in RELIABILITY_DIRS:
        return True
    return dirname.startswith(".venv") or dirname.startswith("venv")


def classify_path(repo_path: str, path: str, *, tracked_files: set[str] | None = None) -> PathClassification:
    repo_abs = os.path.abspath(repo_path)
    rel = normalize_rel_path(repo_abs, path)
    parts = _parts(rel)
    name = parts[-1] if parts else ""
    lower_parts = [p.lower() for p in parts]
    lower_name = name.lower()
    tracked_set = _tracked_files(repo_abs) if tracked_files is None else tracked_files
    tracked = rel in tracked_set

    if not parts:
        return PathClassification(rel, "repository_root", False, "repository root", tracked)

    if any(p in RELIABILITY_DIRS for p in parts) or ".reliability_worktrees" in parts:
        return PathClassification(rel, "temporary_reliability_worktree", True, "temporary reliability worktree", tracked)
    if any(p in VENV_DIRS or p.startswith(".venv") or p.startswith("venv") for p in parts):
        return PathClassification(rel, "virtual_environment", True, "virtual environment", tracked)
    if any(p in CACHE_DIRS for p in parts):
        return PathClassification(rel, "cache", True, "cache directory", tracked)
    if any(p in BUILD_DIRS for p in parts):
        return PathClassification(rel, "build_output", True, "build output", tracked)
    if lower_parts[0] == "workspace":
        return PathClassification(rel, "generated_runtime_data", True, "workspace output", tracked)
    if lower_parts[0] == "data":
        return PathClassification(rel, "application_session_files", True, "runtime data directory", tracked)
    if any(p in UPLOAD_DIRS for p in lower_parts):
        return PathClassification(rel, "uploaded_user_files", True, "uploaded user files", tracked)
    if any(p in SESSION_DIRS for p in lower_parts):
        return PathClassification(rel, "application_session_files", True, "application session files", tracked)
    if any(p in REPORT_DIRS for p in lower_parts):
        return PathClassification(rel, "generated_reports", True, "generated reports directory", tracked)
    if lower_name in _GENERATED_FILENAMES or lower_name.endswith(_GENERATED_SUFFIXES):
        return PathClassification(rel, "generated_runtime_data", True, "generated runtime file", tracked)
    if len(parts) == 1 and any(pat.match(lower_name) for pat in _GENERATED_ROOT_PATTERNS):
        return PathClassification(rel, "generated_reports", True, "known generated root report", tracked)
    if lower_name.endswith(".code-workspace"):
        return PathClassification(rel, "generated_runtime_data", True, "local workspace file", tracked)

    # Prefer Git tracking evidence for legitimate project files. Tracked docs
    # such as audit/report design notes are not hidden by generic words.
    ext = os.path.splitext(name)[1].lower()
    if any(p in _TEST_DIRS for p in lower_parts) or (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py"):
        return PathClassification(rel, "tests", False, "test source", tracked)
    if ext in _DOC_EXTS:
        return PathClassification(rel, "tracked_project_documentation" if tracked else "project_documentation", False, "documentation", tracked)
    if tracked:
        return PathClassification(rel, "tracked_project_source", False, "tracked source", tracked)
    return PathClassification(rel, "project_source", False, "source candidate", tracked)


def is_excluded(repo_path: str, path: str, *, tracked_files: set[str] | None = None) -> bool:
    return classify_path(repo_path, path, tracked_files=tracked_files).excluded


def walk_source_files(repo_path: str, *, suffixes: tuple[str, ...] | None = None, include_tests: bool = True):
    repo_abs = os.path.abspath(repo_path)
    tracked = _tracked_files(repo_abs)
    for root, dirs, files in os.walk(repo_abs, followlinks=False):
        dirs[:] = [
            d for d in dirs
            if not should_skip_dir(d)
            and not classify_path(repo_abs, os.path.join(root, d), tracked_files=tracked).excluded
        ]
        for name in files:
            full = os.path.join(root, name)
            rel = normalize_rel_path(repo_abs, full)
            cls = classify_path(repo_abs, rel, tracked_files=tracked)
            if cls.excluded:
                continue
            if not include_tests and cls.category == "tests":
                continue
            if suffixes and not name.endswith(suffixes):
                continue
            yield full, rel, cls
