#!/usr/bin/env python3
"""
Repository Scanner  (AutoCorp CLI - brains)  [Phase 1A]
=========================================================

A read-only inspector that reports the current state of a repository: git
branch/working-tree status, the running Python version, and counts of Python
source/test files plus a few code-health markers (TODO, FIXME, bare `pass`
statements, `NotImplementedError`).

READ-ONLY: this module only opens files for reading and runs non-mutating git
plumbing commands (`rev-parse`, `status --porcelain`). It never writes to the
repository, stages anything, or changes git state.

Public API:
    run_scan(repo_path) -> ScanResult

All values in the returned ScanResult come from inspecting `repo_path` at call
time - nothing here is hardcoded or estimated.
"""

import ast
import os
import platform
import re
import subprocess
from dataclasses import dataclass

# Directories that never hold project source and would otherwise inflate or
# skew the counts (VCS internals, virtualenvs, caches, build artifacts).
_IGNORE_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist"}

_TODO_RE = re.compile(r"\bTODO\b")
_FIXME_RE = re.compile(r"\bFIXME\b")
_NOT_IMPLEMENTED_RE = re.compile(r"\bNotImplementedError\b")
_PASS_LINE_RE = re.compile(r"(?m)^\s*pass\s*(?:#.*)?$")


@dataclass
class ScanResult:
    """A snapshot of a repository's structure and health markers.

    Pure data - the scanner never mutates one of these after it is built, and
    nothing here is printed; that is the CLI's job."""
    repo_path: str
    branch: str
    working_tree: str          # "clean", "dirty", or "unknown"
    python_version: str
    python_file_count: int
    test_file_count: int
    todo_count: int
    fixme_count: int
    pass_count: int
    not_implemented_count: int


# --------------------------------------------------------------------------- #
# Git (read-only)
# --------------------------------------------------------------------------- #
def _git_info(repo_path: str) -> tuple:
    """Return (branch, working_tree). Prefers GitPython when it's installed;
    otherwise shells out to `git` via subprocess. Only ever reads git state
    (rev-parse / status --porcelain / is_dirty) - never writes it. Falls back
    to ("unknown", "unknown") if there's no git repo or no git available."""
    try:
        import git  # GitPython - optional dependency
    except ImportError:
        git = None

    if git is not None:
        try:
            repo = git.Repo(repo_path, search_parent_directories=True)
            if repo.head.is_detached:
                branch = repo.head.commit.hexsha[:7]
            else:
                branch = repo.active_branch.name
            working_tree = "dirty" if repo.is_dirty(untracked_files=True) else "clean"
            return branch, working_tree
        except Exception:
            pass  # fall through to the subprocess path

    return _git_info_via_subprocess(repo_path)


def _git_info_via_subprocess(repo_path: str) -> tuple:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return "unknown", "unknown"
        branch = proc.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unknown"

    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        working_tree = "dirty" if proc.stdout.strip() else "clean" if proc.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        working_tree = "unknown"

    return branch, working_tree


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #
def _is_test_file(name: str) -> bool:
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _iter_python_files(repo_path: str):
    """Yield full paths of every .py file under `repo_path`, skipping
    _IGNORE_DIRS anywhere in the tree."""
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name), name


# --------------------------------------------------------------------------- #
# Marker counting
# --------------------------------------------------------------------------- #
def _count_pass_statements(content: str) -> int:
    """Count actual `pass` statements via ast (so `password = 1` or a `pass`
    inside a string/comment never counts). Falls back to a line-anchored regex
    if the file doesn't parse, so a syntax error never crashes the scan."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return len(_PASS_LINE_RE.findall(content))
    return sum(1 for node in ast.walk(tree) if isinstance(node, ast.Pass))


def _count_markers(content: str) -> dict:
    return {
        "todo": len(_TODO_RE.findall(content)),
        "fixme": len(_FIXME_RE.findall(content)),
        "pass": _count_pass_statements(content),
        "not_implemented": len(_NOT_IMPLEMENTED_RE.findall(content)),
    }


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def run_scan(repo_path: str) -> ScanResult:
    """Inspect `repo_path` and return a ScanResult. Read-only: only opens files
    for reading and runs non-mutating git commands. Never raises for an
    unreadable or unparsable file - it is simply skipped/degraded."""
    repo_path = os.path.abspath(repo_path)
    branch, working_tree = _git_info(repo_path)

    python_files = 0
    test_files = 0
    todo = fixme = pass_count = not_implemented = 0

    for full_path, name in _iter_python_files(repo_path):
        python_files += 1
        if _is_test_file(name):
            test_files += 1
        try:
            with open(full_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue

        counts = _count_markers(content)
        todo += counts["todo"]
        fixme += counts["fixme"]
        pass_count += counts["pass"]
        not_implemented += counts["not_implemented"]

    return ScanResult(
        repo_path=repo_path,
        branch=branch,
        working_tree=working_tree,
        python_version=platform.python_version(),
        python_file_count=python_files,
        test_file_count=test_files,
        todo_count=todo,
        fixme_count=fixme,
        pass_count=pass_count,
        not_implemented_count=not_implemented,
    )
