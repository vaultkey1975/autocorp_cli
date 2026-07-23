#!/usr/bin/env python3
"""Tests for --repo CLI integration (autocorp.py, Phase 1F).

Covers: scan/analyze/plan-project/repair with --repo, omitted --repo default,
relative path rejection, missing path rejection, non-Git dir rejection,
existing commands unchanged, repair dry-run with external target.
"""

import argparse
import os
import subprocess

import pytest

import autocorp
from brains import workspace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _init_git(repo_path):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@a.local"],
                   cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo_path,
                   capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "i"],
                   cwd=repo_path, capture_output=True)


# --------------------------------------------------------------------------- #
# Default (--repo omitted) preserves behavior
# --------------------------------------------------------------------------- #

def test_scan_without_repo_uses_default(monkeypatch):
    captured_repo = {}
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: captured_repo.setdefault("repo", REPO_ROOT) or REPO_ROOT)
    captured_scan = {}

    def _fake_scan(repo):
        captured_scan["repo"] = repo
        from brains.scanner import ScanResult
        return ScanResult(
            repo_path=repo, branch="main", working_tree="clean",
            python_version="3", python_file_count=0, test_file_count=0,
            todo_count=0, fixme_count=0, pass_count=0, not_implemented_count=0,
        )
    monkeypatch.setattr(autocorp.scanner, "run_scan", _fake_scan)
    rc = autocorp.cmd_scan(argparse.Namespace(repo=None))
    assert rc == 0
    assert captured_scan["repo"] == REPO_ROOT


def test_analyze_without_repo_uses_default(monkeypatch):
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: REPO_ROOT)
    monkeypatch.setattr(autocorp.analyzer, "run_analysis",
                        lambda repo: autocorp.analyzer.ProjectAnalysis(
                            repo_path=repo,
                            project_type="Test",
                            overall_health="Good", confidence=50))
    rc = autocorp.cmd_analyze(argparse.Namespace(repo=None))
    assert rc == 0


def test_plan_project_without_repo_uses_default(monkeypatch):
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: REPO_ROOT)
    monkeypatch.setattr(autocorp.project_planner, "run_project_plan",
                        lambda repo: autocorp.project_planner.ProjectPlan(
                            repo_path=repo, project_type="Test",
                            overall_health="Good", summary="ok",
                            actions=(), blockers=(), confidence=50))
    rc = autocorp.cmd_plan_project(argparse.Namespace(repo=None))
    assert rc == 0


# --------------------------------------------------------------------------- #
# --repo argument registration
# --------------------------------------------------------------------------- #

def test_scan_registers_repo_option():
    parser = autocorp.build_parser()
    args = parser.parse_args(["scan", "--repo", "/tmp/foo"])
    assert args.repo == "/tmp/foo"
    assert args.func is autocorp.cmd_scan


def test_analyze_registers_repo_option():
    parser = autocorp.build_parser()
    args = parser.parse_args(["analyze", "--repo", "/tmp/foo"])
    assert args.repo == "/tmp/foo"
    assert args.func is autocorp.cmd_analyze


def test_plan_project_registers_repo_option():
    parser = autocorp.build_parser()
    args = parser.parse_args(["plan-project", "--repo", "/tmp/foo"])
    assert args.repo == "/tmp/foo"
    assert args.func is autocorp.cmd_plan_project


def test_repair_registers_repo_option():
    parser = autocorp.build_parser()
    args = parser.parse_args(["repair", "--action", "abc", "--repo", "/tmp/foo"])
    assert args.repo == "/tmp/foo"
    assert args.func is autocorp.cmd_repair


# --------------------------------------------------------------------------- #
# Invalid path rejection
# --------------------------------------------------------------------------- #

def test_scan_relative_path_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        autocorp.cmd_scan(argparse.Namespace(repo="relative/path"))
    out = capsys.readouterr().out
    assert exc.value.code != 0
    assert "Workspace Error" in out


def test_scan_missing_path_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        autocorp.cmd_scan(argparse.Namespace(repo="/nonexistent/path"))
    out = capsys.readouterr().out
    assert exc.value.code != 0
    assert "Workspace Error" in out


def test_analyze_non_git_dir_rejected(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        autocorp.cmd_analyze(argparse.Namespace(repo=str(tmp_path)))
    out = capsys.readouterr().out
    assert exc.value.code != 0
    assert "Workspace Error" in out


# --------------------------------------------------------------------------- #
# External target read-only proof
# --------------------------------------------------------------------------- #

def test_scan_external_target_is_read_only(tmp_path):
    _init_git(tmp_path)
    before = list(tmp_path.iterdir())
    autocorp.cmd_scan(argparse.Namespace(repo=str(tmp_path)))
    after = list(tmp_path.iterdir())
    assert before == after


def test_analyze_external_target_is_read_only(tmp_path):
    _init_git(tmp_path)
    (tmp_path / "main.py").write_text("import argparse\n")
    before = list(tmp_path.iterdir())
    autocorp.cmd_analyze(argparse.Namespace(repo=str(tmp_path)))
    after = list(tmp_path.iterdir())
    assert before == after


def test_plan_project_external_target_is_read_only(tmp_path):
    _init_git(tmp_path)
    (tmp_path / "main.py").write_text("import argparse\n")
    before = list(tmp_path.iterdir())
    autocorp.cmd_plan_project(argparse.Namespace(repo=str(tmp_path)))
    after = list(tmp_path.iterdir())
    assert before == after


# --------------------------------------------------------------------------- #
# Repair external target
# --------------------------------------------------------------------------- #

def test_repair_external_defaults_to_dry_run(tmp_path, capsys):
    _init_git(tmp_path)
    (tmp_path / "main.py").write_text("import argparse\n")
    rc = autocorp.cmd_repair(argparse.Namespace(
        repo=str(tmp_path), action="fake123", dry_run=False, approve=False))
    out = capsys.readouterr().out
    assert rc != 0


def test_repair_dry_run_external_no_changes(tmp_path):
    _init_git(tmp_path)
    (tmp_path / "main.py").write_text("import argparse\n")
    before = list(tmp_path.iterdir())
    autocorp.cmd_repair(argparse.Namespace(
        repo=str(tmp_path), action="fake123", dry_run=True, approve=False))
    after = list(tmp_path.iterdir())
    assert before == after


# --------------------------------------------------------------------------- #
# Existing commands unchanged
# --------------------------------------------------------------------------- #

def test_memory_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["memory"])
    assert args.func is autocorp.cmd_memory


def test_explain_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["explain", "somefile"])
    assert args.func is autocorp.cmd_explain


def test_scan_without_repo_prints_autocorp_mode(capsys):
    autocorp.cmd_scan(argparse.Namespace(repo=None))
    out = capsys.readouterr().out
    assert "AutoCorp Default" in out


# --------------------------------------------------------------------------- #
# Error output format
# --------------------------------------------------------------------------- #

def test_error_includes_requested_path(tmp_path, capsys):
    with pytest.raises(SystemExit):
        autocorp.cmd_scan(argparse.Namespace(repo=str(tmp_path / "noexist")))
    out = capsys.readouterr().out
    assert "Requested Path" in out
    assert "No Changes Made" in out
