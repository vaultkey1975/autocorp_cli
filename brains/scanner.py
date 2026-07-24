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
    iter_python_files(repo_path, ignore_dirs=None) -> yields (full_path, name)
    is_test_file(name) -> bool
    count_markers(content) -> dict

The last three are exposed (not just `run_scan`) so other brains - e.g. the
Phase 1B analyzer - can reuse the exact same file walk, test-file convention,
and marker-counting rules instead of duplicating them.

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
IGNORE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", "build", "dist", "site-packages", "node_modules",
    ".venv", "venv", "env",
}

# Directories whose NAME STARTS WITH these prefixes are also excluded.
# This catches .venv-chatterbox, .venv-anything, venv-backup, etc.
_IGNORE_DIR_PREFIXES = (".venv", "venv", "env")

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
def is_test_file(name: str) -> bool:
    """Whether `name` follows this project's test-file naming convention
    (matches pytest's own default discovery: `test_*.py` or `*_test.py`)."""
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _should_skip_dir(dirname: str, ignore_dirs: set) -> bool:
    """Return True if `dirname` should be excluded from the file walk."""
    if dirname in ignore_dirs:
        return True
    for prefix in _IGNORE_DIR_PREFIXES:
        if dirname.startswith(prefix) and dirname != prefix:
            return True
    return False


def iter_python_files(repo_path: str, ignore_dirs: set = None):
    """Yield (full_path, name) for every .py file under `repo_path`, skipping
    `ignore_dirs` (defaults to IGNORE_DIRS) anywhere in the tree. Callers that
    need to additionally skip generated/output directories (e.g. the Phase 1B
    analyzer excluding workspace/) can pass a superset here instead of
    re-walking the tree themselves."""
    ignore = IGNORE_DIRS if ignore_dirs is None else ignore_dirs
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not _should_skip_dir(d, ignore)]
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


def count_markers(content: str) -> dict:
    """Count TODO/FIXME/pass/NotImplementedError markers in one file's
    source. Returns {"todo", "fixme", "pass", "not_implemented"}."""
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

    for full_path, name in iter_python_files(repo_path):
        python_files += 1
        if is_test_file(name):
            test_files += 1
        try:
            with open(full_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue

        counts = count_markers(content)
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
