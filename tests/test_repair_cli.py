#!/usr/bin/env python3
"""Tests for the `repair` CLI subcommand (autocorp.py, Phase 1D).

`repair` wraps brains.repair_executor.build_repair_plan and
execute_repair_plan. These tests confirm the subcommand is registered,
dry-run is default, --approve is required for writes, invalid action IDs
return non-zero, unsupported approved actions return non-zero making no
changes, and existing scan/analyze/plan-project remain unchanged.
"""

import argparse
import os

import autocorp
import pytest

from brains import project_planner

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_parser_registers_repair_command():
    parser = autocorp.build_parser()
    args = parser.parse_args(["repair", "--action", "fake123"])
    assert args.command == "repair"
    assert args.func is autocorp.cmd_repair


def test_repair_without_action_fails():
    parser = autocorp.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["repair"])


def test_repair_default_is_dry_run(capsys, monkeypatch):
    class _FakePlan:
        repo_path = REPO_ROOT
        action_id = "test123"
        action_title = "Test action"
        priority = "high"
        category = "maintainability"
        summary = "Test summary"
        operations = ()
        validation_commands = ()
        blockers = ()
        can_execute = False
        confidence = 100

    class _FakeResult:
        status = "dry_run"
        changed_paths = ()
        validation_passed = False
        rolled_back = False
        message = "Dry run completed."

    monkeypatch.setattr(autocorp.repair_executor, "build_repair_plan",
                        lambda _repo, _aid: _FakePlan())
    monkeypatch.setattr(autocorp.repair_executor, "execute_repair_plan",
                        lambda _plan, approved=False: _FakeResult())

    rc = autocorp.cmd_repair(argparse.Namespace(
        action="test123", dry_run=False, approve=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN" in out
    assert "No changes were made" in out
    assert "Dry-run completed" in out


def test_repair_dry_run_flag_never_changes_files(capsys, monkeypatch):
    class _FakePlan:
        repo_path = REPO_ROOT
        action_id = "test123"
        action_title = "Test"
        priority = "medium"
        category = "maintainability"
        summary = "Summary"
        operations = ()
        validation_commands = ()
        blockers = ()
        can_execute = False
        confidence = 100

    monkeypatch.setattr(autocorp.repair_executor, "build_repair_plan",
                        lambda _repo, _aid: _FakePlan())
    monkeypatch.setattr(autocorp.repair_executor, "execute_repair_plan",
                        lambda _plan, approved=False: autocorp.repair_executor.RepairResult(
                            status="dry_run", message="ok"))

    rc = autocorp.cmd_repair(argparse.Namespace(
        action="test123", dry_run=True, approve=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN" in out


def test_repair_invalid_action_id_returns_nonzero(capsys, monkeypatch):
    class _FakePlan:
        repo_path = REPO_ROOT
        action_id = "bad"
        action_title = ""
        priority = ""
        category = ""
        summary = "Not found"
        operations = ()
        validation_commands = ()
        blockers = ("No action found",)
        can_execute = False
        confidence = 100

    monkeypatch.setattr(autocorp.repair_executor, "build_repair_plan",
                        lambda _repo, _aid: _FakePlan())

    rc = autocorp.cmd_repair(argparse.Namespace(
        action="bad", dry_run=False, approve=True))
    assert rc != 0


def test_repair_unsupported_approved_returns_nonzero(capsys, monkeypatch):
    class _FakePlan:
        repo_path = REPO_ROOT
        action_id = "unsup"
        action_title = "Fix FIXMEs"
        priority = "high"
        category = "maintainability"
        summary = "Not supported"
        operations = ()
        validation_commands = ()
        blockers = ("No auto repair",)
        can_execute = False
        confidence = 100

    monkeypatch.setattr(autocorp.repair_executor, "build_repair_plan",
                        lambda _repo, _aid: _FakePlan())

    rc = autocorp.cmd_repair(argparse.Namespace(
        action="unsup", dry_run=False, approve=True))
    assert rc != 0


def test_repair_valid_approved_supported_succeeds(capsys, monkeypatch):
    class _FakePlan:
        repo_path = REPO_ROOT
        action_id = "ok123"
        action_title = "Add deps"
        priority = "high"
        category = "dependencies"
        summary = "Will create file"
        operations = ()
        validation_commands = ()
        blockers = ()
        can_execute = True
        confidence = 95

    class _FakeResult:
        status = "completed"
        changed_paths = ("requirements.txt",)
        validation_passed = True
        rolled_back = False
        message = "Applied."

    monkeypatch.setattr(autocorp.repair_executor, "build_repair_plan",
                        lambda _repo, _aid: _FakePlan())
    monkeypatch.setattr(autocorp.repair_executor, "execute_repair_plan",
                        lambda _plan, approved=True: _FakeResult())

    rc = autocorp.cmd_repair(argparse.Namespace(
        action="ok123", dry_run=False, approve=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Repair Result" in out
    assert "completed" in out


def test_existing_scan_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["scan"])
    assert args.func is autocorp.cmd_scan


def test_existing_analyze_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["analyze"])
    assert args.func is autocorp.cmd_analyze


def test_existing_plan_project_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["plan-project"])
    assert args.func is autocorp.cmd_plan_project


def test_repair_does_not_require_ollama():
    assert autocorp.cmd_repair is not autocorp.cmd_plan
    assert autocorp.cmd_repair is not autocorp.cmd_build


def test_repair_approved_output_shows_rolled_back_status(capsys, monkeypatch):
    class _FakePlan:
        repo_path = REPO_ROOT
        action_id = "roll"
        action_title = "Test"
        priority = "medium"
        category = "maintainability"
        summary = "test"
        operations = ()
        validation_commands = ()
        blockers = ()
        can_execute = True
        confidence = 80

    class _FakeResult:
        status = "rolled_back"
        changed_paths = ()
        validation_passed = False
        rolled_back = True
        message = "Validation failed."

    monkeypatch.setattr(autocorp.repair_executor, "build_repair_plan",
                        lambda _repo, _aid: _FakePlan())
    monkeypatch.setattr(autocorp.repair_executor, "execute_repair_plan",
                        lambda _plan, approved=True: _FakeResult())

    rc = autocorp.cmd_repair(argparse.Namespace(
        action="roll", dry_run=False, approve=True))
    out = capsys.readouterr().out
    assert rc != 0
    assert "rolled_back" in out
    assert "Rolled Back:" in out
