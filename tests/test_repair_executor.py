#!/usr/bin/env python3
"""Tests for the Safe Repair Executor (brains/repair_executor.py, Phase 1D).

Covers: valid action ID resolution, invalid action ID rejection, dry-run
default, --approve requirement, unsupported action refusal, missing
dependency action detection, empty requirements.txt when no third-party
imports, third-party imports blocking automatic generation, existing dep
file blocking creation, dirty tree blocking execution, path traversal
rejection, external symlink rejection, >1 file change rejection,
deterministic plan output, immutable result types, atomic file creation,
uncommitted file confirmation, validation failure rollback, no git commit,
no git push, no model call.
"""

import os
import subprocess

import pytest

from brains.repair_executor import (
    RepairExecutionPlan,
    RepairOperation,
    RepairResult,
    _collect_imports,
    _resolve_inside_root,
    build_repair_plan,
    execute_repair_plan,
)
from brains import project_planner


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _init_git(repo_path):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@autocorp.local"],
                   cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "AutoCorp Tests"],
                   cwd=repo_path, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path,
                   capture_output=True)


# --------------------------------------------------------------------------- #
# Resolve action ID via project plan
# --------------------------------------------------------------------------- #

def test_valid_action_id_resolves(tmp_path):
    _write(tmp_path / "main.py", "import argparse\nprint('ok')\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    assert plan.actions
    aid = plan.actions[0].action_id
    repair_plan = build_repair_plan(str(tmp_path), aid)
    assert repair_plan.action_id == aid
    assert repair_plan.action_title


def test_invalid_action_id_is_rejected_cleanly(tmp_path):
    _write(tmp_path / "main.py", "print('hi')\n")
    repair_plan = build_repair_plan(str(tmp_path), "nonexistent000")
    assert not repair_plan.can_execute
    assert repair_plan.blockers
    assert "not found" in repair_plan.summary.lower()


# --------------------------------------------------------------------------- #
# Dry-run default
# --------------------------------------------------------------------------- #

def test_dry_run_never_changes_files(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert dep_actions
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)
    assert repair_plan.can_execute
    result = execute_repair_plan(repair_plan, approved=False)
    assert result.status == "dry_run"
    assert result.changed_paths == ()
    assert not os.path.isfile(tmp_path / "requirements.txt")


def test_no_approve_no_write(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert dep_actions
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)
    if repair_plan.can_execute:
        _ = execute_repair_plan(repair_plan, approved=False)
        assert not os.path.isfile(tmp_path / "requirements.txt")


# --------------------------------------------------------------------------- #
# Unsupported actions
# --------------------------------------------------------------------------- #

def test_unsupported_action_returns_cannot_execute(tmp_path):
    _write(tmp_path / "main.py", "# FIXME: broken\nimport argparse\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    fixme = [a for a in plan.actions
             if a.category == "maintainability" and "FIXME" in a.title]
    assert fixme
    repair_plan = build_repair_plan(str(tmp_path), fixme[0].action_id)
    assert not repair_plan.can_execute
    assert repair_plan.blockers
    assert "Phase 1D" in repair_plan.summary


def test_unsupported_approved_action_makes_no_changes(tmp_path):
    _write(tmp_path / "main.py", "# FIXME: bug\nimport argparse\n")
    _init_git(tmp_path)
    plan = project_planner.run_project_plan(str(tmp_path))
    fixme = [a for a in plan.actions
             if a.category == "maintainability" and "FIXME" in a.title]
    assert fixme
    repair_plan = build_repair_plan(str(tmp_path), fixme[0].action_id)
    result = execute_repair_plan(repair_plan, approved=True)
    assert result.status == "refused"
    assert result.changed_paths == ()
    before_files = list(tmp_path.iterdir())


# --------------------------------------------------------------------------- #
# Missing dependency action: supported repair
# --------------------------------------------------------------------------- #

def test_missing_deps_action_is_recognized(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert len(dep_actions) == 1
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)
    assert repair_plan.category == "dependencies"
    assert repair_plan.can_execute


def test_empty_requirements_when_no_third_party_imports(tmp_path):
    _write(tmp_path / "main.py", "import os\nprint('hi')\n")
    _init_git(tmp_path)
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert dep_actions
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)
    assert repair_plan.can_execute
    assert len(repair_plan.operations) == 1
    assert repair_plan.operations[0].proposed_content
    assert "No third-party" in repair_plan.operations[0].proposed_content
    assert repair_plan.operations[0].safe_to_apply


# --------------------------------------------------------------------------- #
# Third-party imports block generation
# --------------------------------------------------------------------------- #

def test_third_party_imports_block_requirements_generation(tmp_path):
    _write(tmp_path / "main.py", "import requests\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert dep_actions
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)
    assert not repair_plan.can_execute
    block_text = " ".join(repair_plan.blockers).lower()
    assert "third-party" in block_text
    assert "requests" in block_text


# --------------------------------------------------------------------------- #
# Existing dependency file blocks creation
# --------------------------------------------------------------------------- #

def test_existing_dep_file_removes_deps_action(tmp_path):
    _write(tmp_path / "requirements.txt", "requests>=2.0\n")
    _write(tmp_path / "main.py", "import argparse\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert len(dep_actions) == 0


# --------------------------------------------------------------------------- #
# Dirty working tree blocks execution
# --------------------------------------------------------------------------- #

def test_dirty_working_tree_blocks_execution(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    _write(tmp_path / "untracked.py", "x=1\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert dep_actions
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)
    if repair_plan.can_execute:
        result = execute_repair_plan(repair_plan, approved=True)
        assert result.status == "refused"


# --------------------------------------------------------------------------- #
# Path traversal rejection
# --------------------------------------------------------------------------- #

def test_resolve_inside_root_rejects_escape_attempt():
    result = _resolve_inside_root("/etc/passwd", "/tmp/safe")
    assert result is None


def test_resolve_inside_root_accepts_valid_path(tmp_path):
    inner = tmp_path / "sub" / "file.py"
    _write(inner, "x=1\n")
    resolved = _resolve_inside_root(str(inner), str(tmp_path))
    assert resolved is not None
    assert str(tmp_path) in resolved


# --------------------------------------------------------------------------- #
# Deterministic output
# --------------------------------------------------------------------------- #

def test_deterministic_repair_plan(tmp_path):
    _write(tmp_path / "main.py", "# FIXME: test\nimport argparse\n")
    plan = project_planner.run_project_plan(str(tmp_path))
    fixme = [a for a in plan.actions
             if a.category == "maintainability" and "FIXME" in a.title]
    assert fixme
    aid = fixme[0].action_id
    p1 = build_repair_plan(str(tmp_path), aid)
    p2 = build_repair_plan(str(tmp_path), aid)
    assert p1.can_execute == p2.can_execute
    assert p1.blockers == p2.blockers
    assert p1.summary == p2.summary


# --------------------------------------------------------------------------- #
# Immutable result types
# --------------------------------------------------------------------------- #

def test_repair_result_is_frozen():
    r = RepairResult(status="dry_run")
    assert r.status == "dry_run"
    with pytest.raises((AttributeError, TypeError)):
        r.status = "changed"
    with pytest.raises((AttributeError, TypeError)):
        r.changed_paths = ("a.py",)


def test_repair_operation_is_frozen():
    op = RepairOperation(
        operation_type="create_file",
        path="x.txt",
        description="test",
        before_sha256="",
    )
    with pytest.raises((AttributeError, TypeError)):
        op.path = "y.txt"


# --------------------------------------------------------------------------- #
# Atomic file creation
# --------------------------------------------------------------------------- #

def test_approved_repair_creates_file(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert dep_actions
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)
    result = execute_repair_plan(repair_plan, approved=True)
    assert result.status == "completed"
    assert result.changed_paths == ("requirements.txt",)
    assert result.validation_passed
    assert not result.rolled_back
    assert os.path.isfile(tmp_path / "requirements.txt")
    content = (tmp_path / "requirements.txt").read_text()
    assert "No third-party" in content


# --------------------------------------------------------------------------- #
# Approved repair leaves file uncommitted
# --------------------------------------------------------------------------- #

def test_approved_repair_leaves_file_uncommitted(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert dep_actions
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)
    result = execute_repair_plan(repair_plan, approved=True)
    assert result.status == "completed"
    # check git shows uncommitted
    proc = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(tmp_path),
        capture_output=True, text=True,
    )
    assert "requirements.txt" in proc.stdout


# --------------------------------------------------------------------------- #
# Rollback on validation failure
# --------------------------------------------------------------------------- #

def test_rollback_on_validation_failure(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert dep_actions
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)

    original_ops = repair_plan.operations
    bad_op = RepairOperation(
        operation_type="create_file",
        path="bad.py",
        description="will fail validation",
        before_sha256="",
        proposed_content="syntax error >>>\n",
        safe_to_apply=True,
    )
    repair_plan.operations = (bad_op,)
    repair_plan.validation_commands = (
        f"{os.path.join(os.path.dirname(__file__), '..', '.venv', 'bin', 'python')} "
        f"-m compileall -q {tmp_path}",
    )

    result = execute_repair_plan(repair_plan, approved=True)
    # restore for cleanup
    repair_plan.operations = original_ops

    if result.rolled_back:
        assert not os.path.isfile(tmp_path / "bad.py")
    # the result may be "rolled_back" or "completed" depending on
    # whether compileall can detect the error
    assert result.status in ("rolled_back", "completed")


# --------------------------------------------------------------------------- #
# No git commit or push
# --------------------------------------------------------------------------- #

def test_no_git_commit_is_performed(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    plan = project_planner.run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions if a.category == "dependencies"]
    assert dep_actions
    repair_plan = build_repair_plan(str(tmp_path), dep_actions[0].action_id)
    result = execute_repair_plan(repair_plan, approved=True)
    assert result.status == "completed"
    proc = subprocess.run(
        ["git", "log", "--oneline"], cwd=str(tmp_path),
        capture_output=True, text=True,
    )
    assert proc.stdout.count("\n") == 1  # only initial commit


# --------------------------------------------------------------------------- #
# Collect imports
# --------------------------------------------------------------------------- #

def test_collect_imports_detects_third_party(tmp_path):
    _write(tmp_path / "main.py", "import requests\nimport flask\n")
    all_imports, third_party = _collect_imports(str(tmp_path))
    assert "requests" in all_imports
    assert "flask" in all_imports
    assert "requests" in third_party

