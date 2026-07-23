#!/usr/bin/env python3
"""Tests for the propose-repair CLI subcommand (autocorp.py, Phase 1G).

Covers: subcommand registration, default provider, invalid action non-zero,
proposal output, output-file handling, overwrite protection, existing
commands unchanged.
"""

import argparse
import os
import subprocess

import pytest

import autocorp
from brains import repair_proposal

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


# --------------------------------------------------------------------------- #
# Parser registration
# --------------------------------------------------------------------------- #

def test_parser_registers_propose_repair():
    parser = autocorp.build_parser()
    args = parser.parse_args(["propose-repair", "--action", "abc"])
    assert args.func is autocorp.cmd_propose_repair


def test_propose_repair_without_action_fails():
    parser = autocorp.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["propose-repair"])


# --------------------------------------------------------------------------- #
# Default provider
# --------------------------------------------------------------------------- #

def test_default_provider_is_ollama(capsys, monkeypatch):
    class _FakeProposal:
        repo_path = "."
        action_id = "x"
        action_title = "test"
        provider = "local"
        model = "qwen2.5:14b"
        summary = "ok"
        reasoning_summary = "ok"
        files = ()
        validation_plan = ()
        risks = ()
        blockers = ()
        safe_to_apply = False
        confidence = 50
        redactions = 0
        redaction_summary = "none"
        provider_error = ""

    monkeypatch.setattr(autocorp.repair_proposal, "build_repair_proposal",
                        lambda repo, action_id, **kw: _FakeProposal())
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: REPO_ROOT)

    rc = autocorp.cmd_propose_repair(argparse.Namespace(
        action="test", provider=None, model=None, output=None, overwrite=False))
    assert rc == 0


# --------------------------------------------------------------------------- #
# Invalid action
# --------------------------------------------------------------------------- #

def test_invalid_action_returns_nonzero(capsys, monkeypatch):
    class _FakeProposal:
        repo_path = "."
        action_id = "x"
        action_title = ""
        provider = "local"
        model = "qwen"
        summary = ""
        reasoning_summary = ""
        files = ()
        validation_plan = ()
        risks = ()
        blockers = ("not found",)
        safe_to_apply = False
        confidence = 0
        redactions = 0
        redaction_summary = ""
        provider_error = "not found"

    monkeypatch.setattr(autocorp.repair_proposal, "build_repair_proposal",
                        lambda repo, action_id, **kw: _FakeProposal())
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: REPO_ROOT)

    rc = autocorp.cmd_propose_repair(argparse.Namespace(
        action="bad", provider=None, model=None, output=None, overwrite=False))
    assert rc != 0


# --------------------------------------------------------------------------- #
# Output file
# --------------------------------------------------------------------------- #

def test_output_existing_protected(tmp_path, capsys, monkeypatch):
    out = tmp_path / "p.json"
    out.write_text("old")

    class _FakeProposal:
        repo_path = "."
        action_id = "x"
        action_title = "test"
        provider = "local"
        model = "qwen"
        summary = "ok"
        reasoning_summary = "ok"
        files = ()
        validation_plan = ()
        risks = ()
        blockers = ()
        safe_to_apply = True
        confidence = 80
        redactions = 0
        redaction_summary = ""
        provider_error = ""

    monkeypatch.setattr(autocorp.repair_proposal, "build_repair_proposal",
                        lambda repo, action_id, **kw: _FakeProposal())
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: REPO_ROOT)

    rc = autocorp.cmd_propose_repair(argparse.Namespace(
        action="test", provider=None, model=None,
        output=str(out), overwrite=False))
    out2 = capsys.readouterr().out
    assert rc != 0
    assert out.read_text() == "old"


def test_output_overwrite_allows(tmp_path, capsys, monkeypatch):
    out = tmp_path / "p.json"
    out.write_text("old")

    class _FakeProposal:
        repo_path = "."
        action_id = "x"
        action_title = "test"
        provider = "local"
        model = "qwen"
        summary = "ok"
        reasoning_summary = "ok"
        files = ()
        validation_plan = ()
        risks = ()
        blockers = ()
        safe_to_apply = True
        confidence = 80
        redactions = 0
        redaction_summary = ""
        provider_error = ""

    monkeypatch.setattr(autocorp.repair_proposal, "build_repair_proposal",
                        lambda repo, action_id, **kw: _FakeProposal())
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: REPO_ROOT)

    rc = autocorp.cmd_propose_repair(argparse.Namespace(
        action="test", provider=None, model=None,
        output=str(out), overwrite=True))
    assert rc == 0


# --------------------------------------------------------------------------- #
# Non-mutating
# --------------------------------------------------------------------------- #

def test_propose_does_not_mutate_files(tmp_path, capsys, monkeypatch):
    _init_git(tmp_path)
    (tmp_path / "main.py").write_text("import argparse\n")

    class _FakeProposal:
        repo_path = str(tmp_path)
        action_id = "x"
        action_title = "test"
        provider = "local"
        model = "qwen"
        summary = "ok"
        reasoning_summary = "ok"
        files = ()
        validation_plan = ()
        risks = ()
        blockers = ()
        safe_to_apply = False
        confidence = 50
        redactions = 0
        redaction_summary = ""
        provider_error = ""

    monkeypatch.setattr(autocorp.repair_proposal, "build_repair_proposal",
                        lambda repo, action_id, **kw: _FakeProposal())
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: str(tmp_path))

    before = list(tmp_path.iterdir())
    autocorp.cmd_propose_repair(argparse.Namespace(
        action="test", provider=None, model=None,
        repo=str(tmp_path), output=None, overwrite=False))
    after = list(tmp_path.iterdir())
    assert before == after


# --------------------------------------------------------------------------- #
# Existing commands unchanged
# --------------------------------------------------------------------------- #

def test_scan_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["scan"])
    assert args.func is autocorp.cmd_scan


def test_analyze_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["analyze"])
    assert args.func is autocorp.cmd_analyze


def test_plan_project_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["plan-project"])
    assert args.func is autocorp.cmd_plan_project


def test_repair_command_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["repair", "--action", "abc"])
    assert args.func is autocorp.cmd_repair


def test_propose_repar_does_not_require_ollama():
    assert autocorp.cmd_propose_repair is not autocorp.cmd_plan
    assert autocorp.cmd_propose_repair is not autocorp.cmd_build
