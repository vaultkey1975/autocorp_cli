#!/usr/bin/env python3
"""Tests for the `analyze` CLI subcommand (autocorp.py, Phase 1B).

`analyze` wraps brains.analyzer.run_analysis and is entirely read-only/
offline: no Ollama check, no gate, no Session. These tests confirm the
subcommand is registered, cmd_analyze calls the analyzer with the repo root
and prints its fields, and running it does not touch the repository.
"""

import argparse
import os
import subprocess

import autocorp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_parser_registers_analyze_command():
    parser = autocorp.build_parser()
    args = parser.parse_args(["analyze"])
    assert args.command == "analyze"
    assert args.func is autocorp.cmd_analyze


class _FakeDirStat:
    def __init__(self, name, python_files, python_lines):
        self.name = name
        self.python_files = python_files
        self.python_lines = python_lines


class _FakeAnalysis:
    repo_path = "/fake/repo"
    project_type = "Python CLI"
    project_type_evidence = ["entry point (autocorp.py)", "`argparse` imported"]
    primary_language = "Python"
    entry_points = ["autocorp.py"]
    dependency_files = ["requirements.txt", "requirements-dev.txt"]
    test_framework = "pytest"
    python_file_count = 116
    total_python_lines = 6713
    average_file_size = 57.9
    largest_module = "brains/templates/sqlite_support.py"
    largest_module_lines = 512
    largest_package = "tests"
    largest_package_lines = 11420
    top_directories = [
        _FakeDirStat("tests", 77, 11420),
        _FakeDirStat("brains", 27, 4916),
        _FakeDirStat("core", 4, 670),
    ]
    todo_count = 7
    fixme_count = 7
    pass_count = 11
    not_implemented_count = 15
    overall_health = "Good"
    confidence = 88


def test_cmd_analyze_calls_run_analysis_with_repo_root(monkeypatch):
    captured = {}

    def _fake_run_analysis(repo_path):
        captured["repo_path"] = repo_path
        return _FakeAnalysis()

    monkeypatch.setattr(autocorp.analyzer, "run_analysis", _fake_run_analysis)

    rc = autocorp.cmd_analyze(argparse.Namespace())

    assert rc == 0
    assert captured["repo_path"] == os.path.dirname(os.path.abspath(autocorp.__file__))


def test_cmd_analyze_prints_all_key_fields(monkeypatch, capsys):
    monkeypatch.setattr(autocorp.analyzer, "run_analysis", lambda repo_path: _FakeAnalysis())

    rc = autocorp.cmd_analyze(argparse.Namespace())
    out = capsys.readouterr().out

    assert rc == 0
    assert "Project Analysis" in out
    assert "Python CLI" in out
    assert "Python" in out
    assert "autocorp.py" in out
    assert "pytest" in out
    assert "requirements.txt" in out
    assert "116" in out
    assert "tests" in out
    assert "brains/templates/sqlite_support.py" in out
    assert "Quality Indicators" in out
    assert "TODO: 7" in out
    assert "FIXME: 7" in out
    assert "pass Statements: 11" in out
    assert "NotImplementedError: 15" in out
    assert "Good" in out
    assert "88%" in out


def test_cmd_analyze_handles_empty_lists_gracefully(monkeypatch, capsys):
    class _EmptyAnalysis(_FakeAnalysis):
        entry_points = []
        dependency_files = []
        top_directories = []
        largest_module = ""
        largest_package = ""

    monkeypatch.setattr(autocorp.analyzer, "run_analysis", lambda repo_path: _EmptyAnalysis())

    rc = autocorp.cmd_analyze(argparse.Namespace())
    out = capsys.readouterr().out

    assert rc == 0
    assert "(none found)" in out
    assert "(none)" in out


def test_cmd_analyze_on_real_repo_does_not_modify_it():
    """End-to-end: run the actual analyze subcommand against this repository
    and confirm the working tree is untouched afterward."""
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout

    rc = autocorp.cmd_analyze(argparse.Namespace())

    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout

    assert rc == 0
    assert before == after
