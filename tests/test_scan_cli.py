#!/usr/bin/env python3
"""Tests for the `scan` CLI subcommand (autocorp.py, Phase 1A).

`scan` wraps brains.scanner.run_scan and is entirely read-only/offline: no
Ollama check, no gate, no Session. These tests confirm the subcommand is
registered, cmd_scan calls the scanner with the repo root and prints its
fields, and running it does not touch the repository.
"""

import argparse
import os
import subprocess

import autocorp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_parser_registers_scan_command():
    parser = autocorp.build_parser()
    args = parser.parse_args(["scan"])
    assert args.command == "scan"
    assert args.func is autocorp.cmd_scan


def test_cmd_scan_calls_run_scan_with_repo_root(monkeypatch):
    captured = {}

    class _FakeResult:
        repo_path = "/fake/repo"
        branch = "main"
        working_tree = "clean"
        python_version = "3.13.7"
        python_file_count = 10
        test_file_count = 5
        todo_count = 1
        fixme_count = 2
        pass_count = 3
        not_implemented_count = 4

    def _fake_run_scan(repo_path):
        captured["repo_path"] = repo_path
        return _FakeResult()

    monkeypatch.setattr(autocorp.scanner, "run_scan", _fake_run_scan)

    rc = autocorp.cmd_scan(argparse.Namespace())

    assert rc == 0
    assert captured["repo_path"] == os.path.dirname(os.path.abspath(autocorp.__file__))


def test_cmd_scan_prints_all_fields(monkeypatch, capsys):
    class _FakeResult:
        repo_path = "/fake/repo"
        branch = "feature/x"
        working_tree = "dirty"
        python_version = "3.13.7"
        python_file_count = 87
        test_file_count = 61
        todo_count = 3
        fixme_count = 2
        pass_count = 9
        not_implemented_count = 1

    monkeypatch.setattr(autocorp.scanner, "run_scan", lambda repo_path: _FakeResult())

    rc = autocorp.cmd_scan(argparse.Namespace())
    out = capsys.readouterr().out

    assert rc == 0
    assert "/fake/repo" in out
    assert "feature/x" in out
    assert "dirty" in out
    assert "3.13.7" in out
    assert "87" in out
    assert "61" in out
    assert "9" in out


def test_cmd_scan_on_real_repo_does_not_modify_it():
    """End-to-end: run the actual scan subcommand against this repository and
    confirm the working tree is untouched afterward."""
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout

    rc = autocorp.cmd_scan(argparse.Namespace())

    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout

    assert rc == 0
    assert before == after
