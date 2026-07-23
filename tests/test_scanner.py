#!/usr/bin/env python3
"""Tests for the read-only Repository Scanner (brains/scanner.py, Phase 1A).

Covers: correct dataclass shape, accurate counts against a known temp-directory
fixture, ignored directories are actually skipped, git branch/clean-vs-dirty
detection against a real throwaway git repo, and that scanning never writes
anything (no files created/modified, no git state changed).
"""

import os
import subprocess
import sys

import pytest

from brains.scanner import ScanResult, run_scan

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is not available in this environment",
)


def _write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit_all(path, message="init"):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #
def test_run_scan_returns_scan_result(tmp_path):
    _init_git_repo(tmp_path)
    _write(tmp_path / "a.py", "x = 1\n")
    _commit_all(tmp_path)

    result = run_scan(str(tmp_path))

    assert isinstance(result, ScanResult)
    assert result.repo_path == str(tmp_path)
    assert result.python_version == f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


# --------------------------------------------------------------------------- #
# Counts
# --------------------------------------------------------------------------- #
def test_python_and_test_file_counts(tmp_path):
    _init_git_repo(tmp_path)
    _write(tmp_path / "app.py", "x = 1\n")
    _write(tmp_path / "util.py", "y = 2\n")
    _write(tmp_path / "tests" / "test_app.py", "def test_x():\n    assert True\n")
    _write(tmp_path / "tests" / "helpers_test.py", "z = 3\n")  # matches the *_test.py convention
    _write(tmp_path / "README.md", "not python\n")
    _commit_all(tmp_path)

    result = run_scan(str(tmp_path))

    assert result.python_file_count == 4  # app.py, util.py, test_app.py, helpers_test.py
    assert result.test_file_count == 2    # test_app.py (test_*), helpers_test.py (*_test.py)


def test_marker_counts_todo_fixme_pass_notimplemented(tmp_path):
    _init_git_repo(tmp_path)
    _write(tmp_path / "mod.py", (
        "# TODO: refactor this\n"
        "def a():\n"
        "    pass\n"
        "\n"
        "def b():\n"
        "    # FIXME: handle edge case\n"
        "    pass\n"
        "\n"
        "def c():\n"
        "    raise NotImplementedError\n"
    ))
    _commit_all(tmp_path)

    result = run_scan(str(tmp_path))

    assert result.todo_count == 1
    assert result.fixme_count == 1
    assert result.pass_count == 2
    assert result.not_implemented_count == 1


def test_pass_substring_in_identifier_is_not_counted(tmp_path):
    """A bare `pass` statement should be counted; the substring 'pass' inside
    an identifier like `password` must not be."""
    _init_git_repo(tmp_path)
    _write(tmp_path / "mod.py", "password = 'secret'\n\ndef f():\n    pass\n")
    _commit_all(tmp_path)

    result = run_scan(str(tmp_path))

    assert result.pass_count == 1


def test_ignored_directories_are_skipped(tmp_path):
    _init_git_repo(tmp_path)
    _write(tmp_path / "keep.py", "pass\n")
    _write(tmp_path / "__pycache__" / "junk.py", "pass\npass\npass\n")
    _write(tmp_path / ".venv" / "lib" / "site.py", "pass\npass\n")
    _write(tmp_path / "build" / "out.py", "pass\n")
    _write(tmp_path / "dist" / "out.py", "pass\n")
    _write(tmp_path / ".pytest_cache" / "x.py", "pass\n")
    _commit_all(tmp_path)

    result = run_scan(str(tmp_path))

    assert result.python_file_count == 1
    assert result.pass_count == 1


# --------------------------------------------------------------------------- #
# Git
# --------------------------------------------------------------------------- #
def test_git_branch_and_clean_tree_detected(tmp_path):
    _init_git_repo(tmp_path)
    _write(tmp_path / "a.py", "x = 1\n")
    _commit_all(tmp_path)

    result = run_scan(str(tmp_path))

    assert result.branch == "main"
    assert result.working_tree == "clean"


def test_git_dirty_tree_detected(tmp_path):
    _init_git_repo(tmp_path)
    _write(tmp_path / "a.py", "x = 1\n")
    _commit_all(tmp_path)

    _write(tmp_path / "a.py", "x = 2\n")  # unstaged modification -> dirty

    result = run_scan(str(tmp_path))

    assert result.working_tree == "dirty"


# --------------------------------------------------------------------------- #
# Read-only guarantee
# --------------------------------------------------------------------------- #
def test_scan_never_modifies_the_repository(tmp_path):
    _init_git_repo(tmp_path)
    _write(tmp_path / "a.py", "x = 1\n")
    _commit_all(tmp_path)

    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True,
    ).stdout
    before_files = sorted(os.listdir(tmp_path))

    run_scan(str(tmp_path))

    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True,
    ).stdout
    after_files = sorted(os.listdir(tmp_path))

    assert before == after == ""
    assert before_files == after_files


def test_scan_on_this_repo_does_not_dirty_the_working_tree():
    """Scanning the real AutoCorp CLI repo must leave its own working tree
    exactly as it found it (no incidental writes anywhere on the walk)."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True,
    ).stdout

    result = run_scan(repo_root)

    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True,
    ).stdout

    assert before == after
    assert result.python_file_count > 0
    assert result.test_file_count > 0
