#!/usr/bin/env python3
"""Tests for the `plan-project` CLI subcommand (autocorp.py, Phase 1C).

`plan-project` wraps brains.project_planner.run_project_plan and is
entirely read-only/offline: no Ollama check, no gate, no Session. These
tests confirm the subcommand is registered, calls the planner with the
repo root, prints expected sections, does not mutate repository files or
Git state, and that existing scan/analyze commands remain unchanged.
"""

import argparse
import os
import subprocess

import autocorp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_parser_registers_plan_project_command():
    parser = autocorp.build_parser()
    args = parser.parse_args(["plan-project"])
    assert args.command == "plan-project"
    assert args.func is autocorp.cmd_plan_project


def test_cmd_plan_project_calls_planner_with_repo_root(monkeypatch):
    captured = {}

    class _FakeAction:
        def __init__(self, action_id, priority, category, title, reason,
                     evidence, recommended_next_step, safe_to_automate,
                     confidence):
            self.action_id = action_id
            self.priority = priority
            self.category = category
            self.title = title
            self.reason = reason
            self.evidence = evidence
            self.recommended_next_step = recommended_next_step
            self.affected_paths = ()
            self.safe_to_automate = safe_to_automate
            self.confidence = confidence

    class _FakePlan:
        repo_path = "/fake/repo"
        project_type = "Python CLI"
        overall_health = "Good"
        summary = "Found 1 high-priority action(s) based on repository evidence."
        actions = (
            _FakeAction("a1b2c3d4e5f6", "high", "maintainability",
                         "Review something", "Some reason",
                         ("Evidence 1",), "Next step here",
                         False, 85),
        )
        blockers = ("Blocked by review",)
        confidence = 85

    def _fake_planner(repo_path):
        captured["repo_path"] = repo_path
        return _FakePlan()

    monkeypatch.setattr(autocorp.project_planner, "run_project_plan", _fake_planner)

    rc = autocorp.cmd_plan_project(argparse.Namespace())
    assert rc == 0
    assert captured["repo_path"] == REPO_ROOT


def test_cmd_plan_project_output_includes_required_sections(capsys, monkeypatch):
    class _FakeAction:
        def __init__(self, action_id, priority, category, title, reason,
                     evidence, recommended_next_step, safe_to_automate,
                     confidence):
            self.action_id = action_id
            self.priority = priority
            self.category = category
            self.title = title
            self.reason = reason
            self.evidence = evidence
            self.recommended_next_step = recommended_next_step
            self.affected_paths = ()
            self.safe_to_automate = safe_to_automate
            self.confidence = confidence

    class _FakePlan:
        repo_path = REPO_ROOT
        project_type = "Python CLI"
        overall_health = "Good"
        summary = "Found 1 action."
        actions = (
            _FakeAction("abc123def456", "high", "testing",
                         "Establish a test framework", "No tests found",
                         ("Evidence A",), "Add tests", False, 90),
        )
        blockers = ("blocker one",)
        confidence = 90

    monkeypatch.setattr(autocorp.project_planner, "run_project_plan",
                        lambda _path: _FakePlan())

    rc = autocorp.cmd_plan_project(argparse.Namespace())
    out = capsys.readouterr().out

    assert rc == 0
    assert "Project Action Plan" in out
    assert "Recommended Actions" in out
    assert "[HIGH]" in out
    assert "Category:" in out
    assert "Reason:" in out
    assert "Evidence:" in out
    assert "Next Step:" in out
    assert "Safe to Automate:" in out
    assert "Confidence:" in out
    assert "90%" in out
    assert "Blockers:" in out
    assert "blocker one" in out


def test_cmd_plan_project_shows_none_for_empty_blockers(capsys, monkeypatch):
    class _FakeAction:
        def __init__(self, action_id, priority, category, title, reason,
                     evidence, recommended_next_step, safe_to_automate,
                     confidence):
            self.action_id = action_id
            self.priority = priority
            self.category = category
            self.title = title
            self.reason = reason
            self.evidence = evidence
            self.recommended_next_step = recommended_next_step
            self.affected_paths = ()
            self.safe_to_automate = safe_to_automate
            self.confidence = confidence

    class _FakePlan:
        repo_path = REPO_ROOT
        project_type = "Python CLI"
        overall_health = "Good"
        summary = "No material findings."
        actions = (
            _FakeAction("low1low2low3l", "low", "repository",
                         "Project appears healthy", "No findings",
                         (), "", True, 70),
        )
        blockers = ()
        confidence = 70

    monkeypatch.setattr(autocorp.project_planner, "run_project_plan",
                        lambda _path: _FakePlan())

    rc = autocorp.cmd_plan_project(argparse.Namespace())
    out = capsys.readouterr().out

    assert rc == 0
    assert "(none)" in out


def test_plan_project_does_not_require_ollama():
    # plan-project is read-only - does not call _require_ollama
    assert autocorp.cmd_plan_project is not autocorp.cmd_plan
    assert autocorp.cmd_plan_project is not autocorp.cmd_build


def test_plan_project_does_not_mutate_files(monkeypatch):
    class _FakeAction:
        def __init__(self, action_id, priority, category, title, reason,
                     evidence, recommended_next_step, safe_to_automate,
                     confidence):
            self.action_id = action_id
            self.priority = priority
            self.category = category
            self.title = title
            self.reason = reason
            self.evidence = evidence
            self.recommended_next_step = recommended_next_step
            self.affected_paths = ()
            self.safe_to_automate = safe_to_automate
            self.confidence = confidence

    class _FakePlan:
        repo_path = REPO_ROOT
        project_type = "Python CLI"
        overall_health = "Good"
        summary = "Test plan."
        actions = (
            _FakeAction("idididididid", "medium", "incomplete-code",
                         "Test action", "Test reason", (), "Do X", False, 80),
        )
        blockers = ()
        confidence = 80

    monkeypatch.setattr(autocorp.project_planner, "run_project_plan",
                        lambda _path: _FakePlan())
    # The function doesn't write anything by itself
    assert autocorp.cmd_plan_project(argparse.Namespace()) == 0


def test_existing_scan_and_analyze_commands_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["scan"])
    assert args.func is autocorp.cmd_scan

    args = parser.parse_args(["analyze"])
    assert args.func is autocorp.cmd_analyze


def test_plan_project_command_returns_zero_on_success(capsys, monkeypatch):
    class _FakeAction:
        def __init__(self, action_id, priority, category, title, reason,
                     evidence, recommended_next_step, safe_to_automate,
                     confidence):
            self.action_id = action_id
            self.priority = priority
            self.category = category
            self.title = title
            self.reason = reason
            self.evidence = evidence
            self.recommended_next_step = recommended_next_step
            self.affected_paths = ()
            self.safe_to_automate = safe_to_automate
            self.confidence = confidence

    class _FakePlan:
        repo_path = REPO_ROOT
        project_type = "Python CLI"
        overall_health = "Excellent"
        summary = "No findings."
        actions = (
            _FakeAction("xoxoxoxoxoxo", "low", "repository",
                         "Project appears healthy", "No findings",
                         (), "", True, 70),
        )
        blockers = ()
        confidence = 70

    monkeypatch.setattr(autocorp.project_planner, "run_project_plan",
                        lambda _path: _FakePlan())

    rc = autocorp.cmd_plan_project(argparse.Namespace())
    assert rc == 0
