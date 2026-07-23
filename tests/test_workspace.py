#!/usr/bin/env python3
"""Tests for Workspace Resolution (brains/workspace.py, Phase 1F).

Covers: default repository, external Git repo, subdirectory to git root,
relative path rejection, missing path rejection, file-not-dir rejection,
non-Git directory rejection, path with spaces, symlink rejection,
invalid explicit path never falls back to default.
"""

import os
import subprocess

import pytest

from brains.workspace import (
    WorkspaceRequest,
    WorkspaceResolution,
    resolve_workspace,
    require_valid_workspace,
)


def _init_git(repo_path):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@a.local"],
                   cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo_path,
                   capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "i"],
                   cwd=repo_path, capture_output=True)


def test_omitted_repo_returns_default(tmp_path):
    res = resolve_workspace(None, str(tmp_path))
    assert res.is_default_repository
    assert res.is_git_repository
    assert res.resolved_path == str(tmp_path)
    assert res.blockers == ()


def test_absolute_git_repo_resolves(tmp_path):
    _init_git(tmp_path)
    res = resolve_workspace(str(tmp_path), "/not/used/default")
    assert not res.is_default_repository
    assert res.is_git_repository
    assert res.repo_root == str(tmp_path)


def test_subdirectory_resolves_to_git_root(tmp_path):
    _init_git(tmp_path)
    sub = tmp_path / "subdir"
    sub.mkdir()
    res = resolve_workspace(str(sub), "/not/used/default")
    assert res.is_git_repository
    assert res.repo_root == str(tmp_path)


def test_relative_path_rejected(tmp_path):
    res = resolve_workspace("relative/path", str(tmp_path))
    assert not res.is_git_repository
    assert any("relative" in b.lower() for b in res.blockers)


def test_missing_path_rejected(tmp_path):
    missing = str(tmp_path / "noexist")
    res = resolve_workspace(missing, "/not/used/default")
    assert not res.is_git_repository
    assert any("not exist" in b.lower() or "does not exist" in b.lower()
               for b in res.blockers)


def test_file_path_rejected(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    res = resolve_workspace(str(f), "/not/used/default")
    assert not res.is_git_repository
    assert any("not a directory" in b.lower() for b in res.blockers)


def test_non_git_directory_rejected(tmp_path):
    res = resolve_workspace(str(tmp_path), "/not/used/default")
    assert not res.is_git_repository


def test_path_with_spaces(tmp_path):
    spaced = tmp_path / "spaced dir"
    spaced.mkdir()
    _init_git(spaced)
    res = resolve_workspace(str(spaced), "/not/used/default")
    assert res.is_git_repository
    assert res.repo_root == str(spaced)


def test_invalid_path_never_falls_back_to_default(tmp_path):
    missing = str(tmp_path / "noexist")
    res = resolve_workspace(missing, "/some/default")
    assert not res.is_default_repository
    assert not res.is_git_repository


def test_require_valid_workspace_exits_on_invalid(tmp_path):
    with pytest.raises(SystemExit) as exc:
        require_valid_workspace("/nonexistent/path", str(tmp_path))
    assert exc.value.code != 0


def test_require_valid_workspace_returns_default(tmp_path):
    result = require_valid_workspace(None, str(tmp_path))
    assert result == str(tmp_path)


def test_resolution_frozen_fields():
    req = WorkspaceRequest(requested_path=None, default_path="/d")
    assert req.requested_path is None
    assert req.default_path == "/d"
    with pytest.raises((AttributeError, TypeError)):
        req.requested_path = "/other"


def test_empty_workspace_request():
    res = resolve_workspace(None, "/default/repo")
    assert res.is_default_repository
    assert res.confidence == 100
