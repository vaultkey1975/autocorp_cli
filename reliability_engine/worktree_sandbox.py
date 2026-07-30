#!/usr/bin/env python3
"""Git worktree isolation and rollback for subtasks."""

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass


@dataclass
class Worktree:
    path: str
    branch: str


class WorktreeSandbox:
    def __init__(self, repo_root: str, base_branch: str = "main", scratch_dir: str | None = None,
                 run_id: str | None = None):
        self.repo_root = os.path.abspath(repo_root)
        self.base_branch = base_branch
        self.scratch_dir = scratch_dir or os.path.join(self.repo_root, "workspace", ".reliability_worktrees")
        # Subtask IDs come from an autoincrement-less SQLite column that is
        # fully cleared (state_store.reset_subtasks()) at the start of every
        # edit-mode run, so the same subtask_id is reused across separate,
        # unrelated runs. Without a per-run namespace, a worktree deliberately
        # preserved after a `blocked` result (see orchestrator.py's
        # rollback(..., keep=True)) would be silently destroyed by create()
        # below the next time a run's first subtask reuses id 1 (or whichever
        # id collides) - undermining the whole point of preserving it for
        # inspection. One random id per WorktreeSandbox instance (i.e. per
        # ReliabilityOrchestrator, i.e. per run) makes that collision
        # structurally impossible instead of relying on callers to avoid it.
        self.run_id = run_id or uuid.uuid4().hex[:8]

    def _run(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(args, cwd=cwd or self.repo_root, text=True, capture_output=True)

    def create(self, subtask_id: int) -> Worktree:
        os.makedirs(self.scratch_dir, exist_ok=True)
        branch = f"reliability/subtask-{self.run_id}-{subtask_id}"
        path = os.path.join(self.scratch_dir, f"subtask-{self.run_id}-{subtask_id}")
        if os.path.exists(path):
            self.rollback(Worktree(path, branch), keep=False)
        proc = self._run(["git", "worktree", "add", "-B", branch, path, self.base_branch])
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        return Worktree(path, branch)

    def has_changes(self, worktree: Worktree) -> bool:
        proc = self._run(["git", "status", "--porcelain"], cwd=worktree.path)
        return bool(proc.stdout.strip())

    def merge_to_main(self, worktree: Worktree) -> None:
        proc = self._run(["git", "diff", "--binary", self.base_branch], cwd=worktree.path)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "git diff failed")
        if not proc.stdout:
            return
        apply_proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=self.repo_root,
            input=proc.stdout,
            text=True,
            capture_output=True,
        )
        if apply_proc.returncode != 0:
            raise RuntimeError(apply_proc.stderr.strip() or "merge apply failed")

    def rollback(self, worktree: Worktree, keep: bool = True) -> None:
        if keep:
            return
        self._run(["git", "worktree", "remove", "--force", worktree.path])
        if os.path.exists(worktree.path):
            shutil.rmtree(worktree.path, ignore_errors=True)
        self._run(["git", "branch", "-D", worktree.branch])
