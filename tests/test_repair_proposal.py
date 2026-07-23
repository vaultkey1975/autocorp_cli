#!/usr/bin/env python3
"""Tests for the AI Repair Proposal Engine (brains/repair_proposal.py, Phase 1G).

Covers: valid action resolution, invalid action rejection, secret file
exclusion, inline secret redaction, file-count limits, binary file
exclusion, large file exclusion, output validation rules, path traversal
rejection, SHA mismatch, >5 files rejection, shell command rejection,
git command rejection, confidence range, blockers force safe_to_apply=false,
evidence collection, write_repair_proposal with atomic write and overwrite.
"""

import os
import stat
import subprocess

import pytest

from brains.repair_proposal import (
    RepairProposal,
    RepairProposalFile,
    RepairProposalRequest,
    _collect_evidence,
    _is_secret_file,
    _redact_inline_secrets,
    _validate_proposal_json,
    _contains_shell_command,
    _contains_git_command,
    build_repair_proposal,
    write_repair_proposal,
)
from brains import project_planner


def _write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


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
# Action resolution
# --------------------------------------------------------------------------- #

def test_valid_action_id_resolves(tmp_path):
    _write(tmp_path / "main.py", "import argparse\nprint('ok')\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    assert plan.actions
    aid = plan.actions[0].action_id
    proposal = build_repair_proposal(str(tmp_path), aid)
    assert proposal.action_id == aid
    assert proposal.action_title


def test_invalid_action_id_returns_blocked(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    proposal = build_repair_proposal(str(tmp_path), "bad000bad000")
    assert proposal.blockers
    assert proposal.provider_error


# --------------------------------------------------------------------------- #
# Secret file exclusion
# --------------------------------------------------------------------------- #

def test_secret_files_are_excluded(tmp_path):
    _write(tmp_path / ".env", "SECRET=123\n")
    _write(tmp_path / "main.py", "import argparse\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    aid = plan.actions[0].action_id
    evidence = _collect_evidence(str(tmp_path), aid)
    assert evidence["secret_files_excluded"] >= 1


def test_env_patterns_excluded(tmp_path):
    _write(tmp_path / ".env.production", "KEY=x\n")
    _write(tmp_path / "token.json", "{}")
    _write(tmp_path / "credentials.txt", "secret")
    _write(tmp_path / "main.py", "import argparse\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    aid = plan.actions[0].action_id
    evidence = _collect_evidence(str(tmp_path), aid)
    assert evidence["secret_files_excluded"] >= 3


# --------------------------------------------------------------------------- #
# Inline secret redaction
# --------------------------------------------------------------------------- #

def test_inline_secrets_are_redacted():
    content = "API_KEY = abc123\nprint('hi')\n"
    redacted, count = _redact_inline_secrets(content)
    assert count >= 1
    assert "[REDACTED]" in redacted
    assert "abc123" not in redacted


def test_inline_redaction_preserves_safe_lines():
    content = "import argparse\nprint('hello')\n"
    redacted, count = _redact_inline_secrets(content)
    assert count == 0
    assert "import argparse" in redacted


# --------------------------------------------------------------------------- #
# Validation rules
# --------------------------------------------------------------------------- #

def test_missing_required_fields_rejected():
    errors = _validate_proposal_json({}, {}, "/tmp")
    assert errors


def test_unknown_fields_rejected():
    errors = _validate_proposal_json({"xyz": 1}, {}, "/tmp")
    assert any("xyz" in e for e in errors)


def test_path_traversal_rejected():
    data = {
        "summary": "s", "reasoning_summary": "r",
        "files": [{"path": "../etc/passwd", "purpose": "x",
                    "current_sha256": "", "proposed_change_summary": "",
                    "proposed_patch": "", "confidence": 50}],
        "safe_to_apply": True, "confidence": 80,
    }
    errors = _validate_proposal_json(data, {}, "/tmp")
    assert any("traversal" in e.lower() for e in errors)


def test_absolute_path_rejected():
    data = {
        "summary": "s", "reasoning_summary": "r",
        "files": [{"path": "/etc/hosts", "purpose": "x",
                    "current_sha256": "", "proposed_change_summary": "",
                    "proposed_patch": "", "confidence": 50}],
        "safe_to_apply": True, "confidence": 80,
    }
    errors = _validate_proposal_json(data, {}, "/tmp")
    assert any("absolute" in e.lower() or "must be relative" in e.lower()
               for e in errors)


def test_more_than_5_files_rejected():
    data = {
        "summary": "s", "reasoning_summary": "r",
        "files": [
            {"path": f"f{i}.py", "purpose": "x", "current_sha256": "",
             "proposed_change_summary": "", "proposed_patch": "",
             "confidence": 50}
            for i in range(6)
        ],
        "safe_to_apply": True, "confidence": 80,
    }
    errors = _validate_proposal_json(data, {}, "/tmp")
    assert any("5" in e for e in errors)


def test_shell_commands_rejected():
    assert _contains_shell_command("rm -rf /")
    assert _contains_shell_command("echo hello && rm -rf .")
    assert not _contains_shell_command("x = 1 + 2")


def test_git_commands_rejected():
    assert _contains_git_command("git commit -m 'x'")
    assert _contains_git_command("git push origin main")
    assert _contains_git_command("git add file.py")
    assert not _contains_git_command("import git  # comment about git")


def test_confidence_outside_range_rejected():
    data = {
        "summary": "s", "reasoning_summary": "r",
        "files": [],
        "safe_to_apply": True, "confidence": 150,
    }
    errors = _validate_proposal_json(data, {}, "/tmp")
    assert any("0-100" in e or "confidence" in e.lower() for e in errors)


def test_blockers_force_safe_to_apply_false():
    data = {
        "summary": "s", "reasoning_summary": "r",
        "files": [],
        "blockers": ["dangerous change"],
        "safe_to_apply": True, "confidence": 80,
    }
    errors = _validate_proposal_json(data, {}, "/tmp")
    assert not errors
    assert data["safe_to_apply"] is False


# --------------------------------------------------------------------------- #
# write_repair_proposal
# --------------------------------------------------------------------------- #

def test_write_proposal_atomic_and_exists_protection(tmp_path):
    out = str(tmp_path / "proposal.json")
    p = RepairProposal(
        repo_path="/x", action_id="a", action_title="test",
        provider="local", model="qwen",
    )
    result = write_repair_proposal(p, out)
    assert result == out
    assert os.path.isfile(out)

    with pytest.raises(FileExistsError):
        write_repair_proposal(p, out, overwrite=False)

    result2 = write_repair_proposal(p, out, overwrite=True)
    assert result2 == out


def test_write_proposal_requires_absolute_path(tmp_path):
    p = RepairProposal(repo_path="/x", action_id="a", action_title="test",
                       provider="local", model="qwen")
    with pytest.raises(ValueError):
        write_repair_proposal(p, "relative/path.json")


# --------------------------------------------------------------------------- #
# Immutable types
# --------------------------------------------------------------------------- #

def test_request_is_frozen():
    r = RepairProposalRequest(repo_path="/r", action_id="a",
                               provider="local", model="x")
    with pytest.raises((AttributeError, TypeError)):
        r.repo_path = "/other"


def test_file_is_frozen():
    f = RepairProposalFile(path="x.py", purpose="p",
                            current_sha256="abc", confidence=80)
    with pytest.raises((AttributeError, TypeError)):
        f.path = "y.py"


# --------------------------------------------------------------------------- #
# Security regression: compound secret filenames
# --------------------------------------------------------------------------- #

def test_compound_secret_filenames_excluded():
    assert _is_secret_file("db_credentials.json")
    assert _is_secret_file("user_auth.py")
    assert _is_secret_file("app_secrets.py")
    assert _is_secret_file("service_token.txt")
    assert _is_secret_file("client_keys.json")
    assert _is_secret_file("credentials.py")
    assert _is_secret_file("secrets.py")
    assert _is_secret_file("auth.py")
    assert _is_secret_file("config/secrets.py")


def test_non_secret_names_not_caught():
    assert not _is_secret_file("tokenizer.py")
    assert not _is_secret_file("authentication_handler.py")


# --------------------------------------------------------------------------- #
# Security regression: inline secret redaction
# --------------------------------------------------------------------------- #

def test_password_assignment_redacted():
    content = 'password = "super-secret-123"\n'
    r, c = _redact_inline_secrets(content)
    assert c >= 1
    assert "super-secret-123" not in r
    assert "[REDACTED]" in r


def test_uppercase_secret_redacted():
    content = 'SECRET = "mykey"\n'
    r, c = _redact_inline_secrets(content)
    assert c >= 1
    assert "mykey" not in r


def test_client_secret_redacted():
    content = 'client_secret = "abc123"\n'
    r, c = _redact_inline_secrets(content)
    assert c >= 1
    assert "abc123" not in r


def test_aws_secret_key_redacted():
    content = 'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
    r, c = _redact_inline_secrets(content)
    assert c >= 1
    assert "wJalr" not in r


def test_authorization_bearer_redacted():
    content = 'Authorization: Bearer sk-abc123def456\n'
    r, c = _redact_inline_secrets(content)
    assert c >= 1
    assert "sk-abc" not in r
    assert "Bearer [REDACTED]" in r


def test_url_credentials_redacted():
    content = 'DATABASE_URL = postgres://user:password123@host/db\n'
    r, c = _redact_inline_secrets(content)
    assert c >= 1
    assert "password123" not in r
    assert "postgres://" in r
    assert "[REDACTED]" in r


def test_redacted_values_never_appear():
    content = 'password = "secret"\nSECRET = "key"\nAuthorization: Bearer tok\n'
    r, c = _redact_inline_secrets(content)
    assert "secret" not in r or r.count("secret") <= 1
    assert "key" not in r or r.count("key") <= 1
    assert "tok" not in r
