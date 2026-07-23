#!/usr/bin/env python3
"""Tests for live-readiness CLI subcommand (autocorp.py, Phase 1H)."""

import argparse
import os
import subprocess

import pytest

import autocorp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _init_git(repo_path):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"],
                   cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=repo_path, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo_path,
                   capture_output=True)


def test_parser_registers_live_readiness():
    parser = autocorp.build_parser()
    args = parser.parse_args(["live-readiness"])
    assert args.func is autocorp.cmd_live_readiness


def test_live_readiness_accepts_repo_arg():
    parser = autocorp.build_parser()
    args = parser.parse_args(["live-readiness", "--repo", "/tmp/test"])
    assert args.repo == "/tmp/test"


def test_live_readiness_default_runs_on_autocorp(capsys, monkeypatch):
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: REPO_ROOT)
    rc = autocorp.cmd_live_readiness(argparse.Namespace(repo=None))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Live Application Readiness" in out


def test_live_readiness_external_target(tmp_path, capsys, monkeypatch):
    (tmp_path / "main.py").write_text("import argparse\n")
    _init_git(tmp_path)
    monkeypatch.setattr(autocorp, "_resolve_repo",
                        lambda args: str(tmp_path))
    rc = autocorp.cmd_live_readiness(argparse.Namespace(repo=str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 0
    assert str(tmp_path) in out


def test_live_readiness_static_no_changes(tmp_path):
    (tmp_path / "main.py").write_text("import argparse\n")
    _init_git(tmp_path)
    before = set(os.listdir(str(tmp_path)))
    live_readiness = pytest.importorskip("brains.live_readiness")
    report = live_readiness.run_live_readiness(str(tmp_path))
    after = set(os.listdir(str(tmp_path)))
    assert before == after


def test_scan_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["scan"])
    assert args.func is autocorp.cmd_scan


def test_analyze_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["analyze"])
    assert args.func is autocorp.cmd_analyze
