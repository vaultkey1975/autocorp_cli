#!/usr/bin/env python3
"""
Workspace Resolution  (AutoCorp CLI - brains)  [Phase 1F]
===========================================================

Resolves a user-requested repository path (--repo) against safety rules,
returning a WorkspaceResolution. Ensures strong workspace boundaries:
the resolved path must be inside a Git working tree and never escapes
the user's requested directory unexpectedly.

Public API:
    resolve_workspace(requested_path, default_path) -> WorkspaceResolution
    require_valid_workspace(requested_path, default_path) -> str
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

_SYMLINK_LOOP_GUARD = 20
_KNOWN_GIT_DIRS = {".git"}


@dataclass(frozen=True)
class WorkspaceRequest:
    requested_path: str | None
    default_path: str


@dataclass
class WorkspaceResolution:
    requested_path: str | None
    resolved_path: str
    repo_root: str
    is_git_repository: bool = False
    is_default_repository: bool = False
    blockers: tuple[str, ...] = ()
    confidence: int = 0


# --------------------------------------------------------------------------- #
# Path resolution helpers
# --------------------------------------------------------------------------- #


def _resolve_safely(path: str) -> tuple[str | None, tuple[str, ...]]:
    """Resolve a path, check for symlink loops, and return (resolved_path,
    blockers). Returns (None, blockers) when path is dangerous."""
    blockers: list[str] = []
    if not os.path.isabs(path):
        blockers.append("Relative paths are not accepted. Provide an absolute path.")
        return None, tuple(blockers)
    if not os.path.exists(path):
        blockers.append(f"Path does not exist: {path}")
        return None, tuple(blockers)
    if not os.path.isdir(path):
        blockers.append(f"Path is not a directory: {path}")
        return None, tuple(blockers)

    try:
        real = os.path.abspath(path)
        seen: set[str] = set()
        steps = 0
        while os.path.islink(real) and steps < _SYMLINK_LOOP_GUARD:
            if real in seen:
                blockers.append("Symlink loop detected.")
                return None, tuple(blockers)
            seen.add(real)
            target = os.readlink(real)
            real = os.path.join(os.path.dirname(real), target)
            real = os.path.abspath(real)
            steps += 1
        if steps >= _SYMLINK_LOOP_GUARD:
            blockers.append("Symlink resolution depth exceeded.")
            return None, tuple(blockers)

        if not os.path.isdir(real):
            blockers.append(f"Resolved symlink target is not a directory: {real}")
            return None, tuple(blockers)

        return real, tuple(blockers)
    except OSError as exc:
        blockers.append(f"Cannot access path: {exc}")
        return None, tuple(blockers)


def _git_repo_root(path: str) -> str | None:
    """Return the top-level Git repository root for `path`, or None."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            root = proc.stdout.strip()
            if root:
                return root
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def resolve_workspace(
    requested_path: str | None,
    default_path: str,
) -> WorkspaceResolution:
    """Resolve a workspace path against safety rules.

    When requested_path is None, returns the default (AutoCorp) repository.
    When requested_path is provided, validates and resolves it to a Git
    repository root. Never falls back silently.

    Read-only: never mutates the repository."""
    if requested_path is None:
        default = os.path.abspath(default_path)
        return WorkspaceResolution(
            requested_path=None,
            resolved_path=default,
            repo_root=default,
            is_git_repository=True,
            is_default_repository=True,
            confidence=100,
        )

    resolved, blockers = _resolve_safely(requested_path)
    if resolved is None:
        return WorkspaceResolution(
            requested_path=requested_path,
            resolved_path=requested_path,
            repo_root=requested_path,
            is_git_repository=False,
            is_default_repository=False,
            blockers=blockers,
            confidence=100,
        )

    git_root = _git_repo_root(resolved)
    if git_root is None:
        return WorkspaceResolution(
            requested_path=requested_path,
            resolved_path=resolved,
            repo_root=resolved,
            is_git_repository=False,
            is_default_repository=False,
            blockers=(
                "The requested path is not inside a Git repository. "
                "Only Git working trees are supported.",
            ),
            confidence=100,
        )

    repo_root_abs = os.path.abspath(git_root)
    return WorkspaceResolution(
        requested_path=requested_path,
        resolved_path=resolved,
        repo_root=repo_root_abs,
        is_git_repository=True,
        is_default_repository=False,
        confidence=100,
    )


def require_valid_workspace(
    requested_path: str | None,
    default_path: str,
) -> str:
    """Resolve and enforce validity. Returns the repository root on success.
    Raises SystemExit with a controlled message on failure. Never raises
    for the default path."""
    resolution = resolve_workspace(requested_path, default_path)
    if not resolution.is_git_repository:
        print("Workspace Error")
        print("===============")
        print()
        if resolution.requested_path:
            print(f"Requested Path:  {resolution.requested_path}")
        print(f"Resolved Path:   {resolution.resolved_path}")
        print()
        print("Reason:")
        for b in resolution.blockers:
            print(f"  - {b}")
        print()
        print("No Changes Made: Yes")
        raise SystemExit(1)
    return resolution.repo_root
