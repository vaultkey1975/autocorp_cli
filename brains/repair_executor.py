#!/usr/bin/env python3
"""
Safe Repair Executor  (AutoCorp CLI - brains)  [Phase 1D]
===========================================================

A controlled repair layer that selects one Phase 1C action by ID, builds
a safe repair execution plan, and executes a very narrow set of
deterministic repairs only. Running without --approve never changes files.

Public API:
    build_repair_plan(repo_path, action_id) -> RepairExecutionPlan
    execute_repair_plan(plan, approved=False) -> RepairResult

Design:
    Every operation is justified by evidence from the Phase 1C plan.
    Phase 1D is intentionally narrow. Only one repair type is executable:
    creating an empty requirements.txt for Python projects with no
    third-party imports. All other action categories are inspection-only.

Safety:
    - Dry-run by default (no --approve means zero file changes)
    - Atomic writes via temp-file + os.replace
    - SHA-256 checkpoint of every existing file before modification
    - Fixed validation commands only (never from repo content)
    - Automatic rollback on any validation failure
    - Never commits, never pushes, never calls a model
    - Path-traversal and symlink-exfiltration protection
"""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from brains import project_planner, scanner

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RepairRequest:
    repo_path: str
    action_id: str
    approved: bool


@dataclass(frozen=True)
class RepairOperation:
    operation_type: str
    path: str
    description: str
    before_sha256: str
    proposed_content: str | None = None
    safe_to_apply: bool = False


@dataclass
class RepairExecutionPlan:
    repo_path: str
    action_id: str
    action_title: str = ""
    priority: str = ""
    category: str = ""
    summary: str = ""
    operations: tuple[RepairOperation, ...] = ()
    validation_commands: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    can_execute: bool = False
    confidence: int = 0


@dataclass(frozen=True)
class RepairResult:
    status: str
    changed_paths: tuple[str, ...] = ()
    validation_passed: bool = False
    rolled_back: bool = False
    message: str = ""


# --------------------------------------------------------------------------- #
# Import scanning (safe static AST parsing)
# --------------------------------------------------------------------------- #

_SYMLINK_LOOP_GUARD = 20


def _import_roots(content: str) -> set[str]:
    """Top-level imported module root names. AST-based; degrades gracefully
    on SyntaxError."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _collect_imports(repo_path: str) -> tuple[set[str], set[str]]:
    """Walk all Python source files, collect all top-level import roots.
    Returns (all_imports, third_party_imports)."""
    all_imports: set[str] = set()
    for full_path, _name in scanner.iter_python_files(repo_path):
        try:
            with open(full_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue
        all_imports |= _import_roots(content)

    stdlib = sys.stdlib_module_names if hasattr(sys, "stdlib_module_names") else set()
    third_party = all_imports - stdlib
    return all_imports, third_party


# --------------------------------------------------------------------------- #
# Safety checks
# --------------------------------------------------------------------------- #


def _sha256_file(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _resolve_inside_root(path: str, root: str) -> str | None:
    """Resolve the absolute path, follow symlinks (with loop guard), and
    return the resolved path only if it stays inside root. Returns None
    if resolution fails or escapes root."""
    try:
        real = os.path.abspath(path)
        seen: set[str] = set()
        steps = 0
        while os.path.islink(real) and steps < _SYMLINK_LOOP_GUARD:
            if real in seen:
                return None
            seen.add(real)
            target = os.readlink(real)
            real = os.path.join(os.path.dirname(real), target)
            real = os.path.abspath(real)
            steps += 1
        if steps >= _SYMLINK_LOOP_GUARD:
            return None
        real_root = os.path.abspath(root)
        if os.path.commonpath([real, real_root]) != real_root:
            return None
        return real
    except (OSError, ValueError):
        return None


def _run_safety_checks(plan: RepairExecutionPlan,
                       repo_path: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Return (error_blockers, file_checksums). Each checksum is (path, sha256)."""
    blockers: list[str] = []
    checksums: list[tuple[str, str]] = []

    if not os.path.isdir(repo_path):
        blockers.append("Repository path does not exist or is not a directory.")
        return blockers, checksums

    git_info = scanner._git_info(repo_path)
    if git_info[1] == "dirty":
        blockers.append(
            "Working tree is dirty — commit or stash changes before repair."
        )
    if git_info[1] == "unknown":
        blockers.append("Cannot determine git status — aborting for safety.")

    fresh_plan = project_planner.run_project_plan(repo_path)
    matching = [a for a in fresh_plan.actions if a.action_id == plan.action_id]
    if not matching:
        blockers.append(
            "Selected action no longer exists in a fresh Phase 1C plan. "
            "The repository state may have changed."
        )

    for op in plan.operations:
        target = os.path.join(repo_path, op.path)
        resolved = _resolve_inside_root(target, repo_path)
        if resolved is None:
            blockers.append(f"Path escapes repository root or resolves outside: {op.path}")
            continue
        if os.path.exists(resolved):
            blockers.append(f"Planned file already exists: {op.path}")
            continue
        checksums.append((op.path, ""))

    if len(plan.operations) > 1:
        blockers.append(
            f"Phase 1D supports at most one file change per repair; got {len(plan.operations)}."
        )

    return blockers, checksums


# --------------------------------------------------------------------------- #
# Supported-repair builders
# --------------------------------------------------------------------------- #

_DEPS_DETECTED = {"requirements.txt", "requirements-dev.txt",
                  "pyproject.toml", "Pipfile", "poetry.lock", "setup.py"}


def _build_missing_deps_plan(repo_path: str,
                              action: project_planner.ProjectAction
                              ) -> RepairExecutionPlan:
    """Build a plan for the 'dependencies' category: create requirements.txt
    only when no third-party imports exist."""
    plan = RepairExecutionPlan(
        repo_path=repo_path,
        action_id=action.action_id,
        action_title=action.title,
        priority=action.priority,
        category=action.category,
    )

    existing = [f for f in _DEPS_DETECTED
                if os.path.isfile(os.path.join(repo_path, f))]
    if existing:
        plan.blockers = (
            f"Dependency file(s) already exist: {', '.join(existing)}. "
            "Repair not applicable.",
        )
        plan.summary = "Dependency file already present — no repair needed."
        plan.can_execute = False
        plan.confidence = 100
        return plan

    all_imports, third_party = _collect_imports(repo_path)
    if third_party:
        deps_str = ", ".join(sorted(third_party)[:10])
        plan.blockers = (
            f"Third-party imports detected ({deps_str}). "
            "Automatic dependency generation is not supported in Phase 1D. "
            "Manually review and add the required packages to a dependency file.",
        )
        plan.summary = (
            f"Found {len(third_party)} third-party import(s); "
            "cannot auto-generate requirements.txt."
        )
        plan.can_execute = False
        plan.confidence = 90
        return plan

    content = "# No third-party dependencies detected by AutoCorp.\n"
    op = RepairOperation(
        operation_type="create_file",
        path="requirements.txt",
        description="Create an empty requirements.txt (no third-party imports detected).",
        before_sha256="",
        proposed_content=content,
        safe_to_apply=True,
    )

    plan.operations = (op,)
    plan.validation_commands = (
        "python -m compileall -q .",
    )
    plan.summary = (
        "No third-party imports detected. Creating an empty requirements.txt "
        "with a clear comment."
    )
    plan.can_execute = True
    plan.confidence = 95
    return plan


def _build_unsupported_plan(repo_path: str,
                             action: project_planner.ProjectAction
                             ) -> RepairExecutionPlan:
    """Return a valid inspection-only plan for unsupported categories."""
    return RepairExecutionPlan(
        repo_path=repo_path,
        action_id=action.action_id,
        action_title=action.title,
        priority=action.priority,
        category=action.category,
        summary=(
            f"Action category '{action.category}' is not supported for "
            "automatic repair in Phase 1D. Manual investigation is required."
        ),
        blockers=(
            f"Category '{action.category}' has no executable repair in Phase 1D. "
            "Review the action manually.",
        ),
        can_execute=False,
        confidence=100,
    )


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def build_repair_plan(repo_path: str, action_id: str) -> RepairExecutionPlan:
    """Resolve action_id via run_project_plan and build a safe repair
    execution plan. Read-only: never writes to the repository."""
    repo_path = os.path.abspath(repo_path)

    try:
        full_plan = project_planner.run_project_plan(repo_path)
    except Exception as exc:
        return RepairExecutionPlan(
            repo_path=repo_path,
            action_id=action_id,
            summary=f"Failed to run project plan: {exc}",
            blockers=(f"Project planner error: {exc}",),
            can_execute=False,
            confidence=0,
        )

    matching = [a for a in full_plan.actions if a.action_id == action_id]
    if not matching:
        return RepairExecutionPlan(
            repo_path=repo_path,
            action_id=action_id,
            summary=f"Action ID '{action_id}' not found in the current project plan.",
            blockers=(f"No action with ID '{action_id}' found. "
                       "Run 'python autocorp.py plan-project' to see available actions.",),
            can_execute=False,
            confidence=100,
        )

    action = matching[0]
    if action.category == "dependencies":
        return _build_missing_deps_plan(repo_path, action)
    return _build_unsupported_plan(repo_path, action)


def execute_repair_plan(plan: RepairExecutionPlan,
                        approved: bool = False) -> RepairResult:
    """Execute a repair plan. If not approved, perform dry-run only.
    Returns a RepairResult; never raises for expected refusal paths."""
    if not approved:
        return RepairResult(
            status="dry_run",
            changed_paths=(),
            validation_passed=False,
            rolled_back=False,
            message=(
                "Dry-run completed. No changes were made. "
                "Re-run with --approve to apply the repair."
            ),
        )

    if not plan.can_execute:
        return RepairResult(
            status="refused",
            changed_paths=(),
            validation_passed=False,
            rolled_back=False,
            message=(
                "Execution refused: this action cannot be repaired automatically "
                "in Phase 1D. See blockers for details."
            ),
        )

    blockers, checksums = _run_safety_checks(plan, plan.repo_path)
    if blockers:
        return RepairResult(
            status="refused",
            changed_paths=(),
            validation_passed=False,
            rolled_back=False,
            message=f"Safety checks failed:\n" + "\n".join(f"- {b}" for b in blockers),
        )

    changed: list[str] = []
    venv_python = os.path.join(plan.repo_path, ".venv", "bin", "python")
    if not os.path.isfile(venv_python):
        venv_python = sys.executable

    for op in plan.operations:
        target = os.path.join(plan.repo_path, op.path)
        if op.proposed_content is None:
            continue

        try:
            os.makedirs(os.path.dirname(target) or plan.repo_path, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(target) or plan.repo_path,
                prefix=".autocorp_repair_",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(op.proposed_content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, target)
            changed.append(op.path)
        except OSError as exc:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            return RepairResult(
                status="rolled_back",
                changed_paths=tuple(changed),
                validation_passed=False,
                rolled_back=True,
                message=f"Write failed for {op.path}: {exc}. "
                         "All changes have been reverted.",
            )

    for cmd_template in plan.validation_commands:
        cmd = cmd_template.replace("python", venv_python, 1)
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=plan.repo_path,
                capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _rollback_changes(plan.repo_path, changed)
            return RepairResult(
                status="rolled_back",
                changed_paths=(),
                validation_passed=False,
                rolled_back=True,
                message=f"Validation command failed to run: {exc}. "
                         "All changes have been reverted.",
            )
        if proc.returncode != 0:
            _rollback_changes(plan.repo_path, changed)
            return RepairResult(
                status="rolled_back",
                changed_paths=(),
                validation_passed=False,
                rolled_back=True,
                message=(
                    f"Validation command failed (exit {proc.returncode}):\n"
                    f"{proc.stderr.strip() or proc.stdout.strip()}\n"
                    "All changes have been reverted."
                ),
            )

    return RepairResult(
        status="completed",
        changed_paths=tuple(changed),
        validation_passed=True,
        rolled_back=False,
        message=(
            f"Repair applied successfully. {len(changed)} file(s) changed. "
            "Changes are uncommitted — review them before committing."
        ),
    )


def _rollback_changes(repo_path: str, changed: list[str]) -> None:
    for rel in changed:
        target = os.path.join(repo_path, rel)
        if os.path.isfile(target):
            try:
                os.unlink(target)
            except OSError:
                pass
