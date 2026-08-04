"""Safe, read-only Git change detection and repository fingerprinting.

Only invokes read-only Git subcommands (status, diff, rev-parse, ls-files).
Never mutates the target repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from brains import repo_policy


@dataclass
class ChangeSet:
    repo_path: str
    branch: str
    commit_sha: str
    is_git_repo: bool
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    committed_vs_base: list[str] = field(default_factory=list)
    base_branch: str | None = None
    explicit_paths: list[str] = field(default_factory=list)
    explicit_node_ids: list[str] = field(default_factory=list)

    @property
    def changed_files(self) -> list[str]:
        seen = []
        for group in (self.staged, self.unstaged, self.untracked, self.committed_vs_base, self.explicit_paths):
            for rel in group:
                if rel and rel not in seen:
                    seen.append(rel)
        return seen


def _git(repo_path: str, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def is_git_repo(repo_path: str) -> bool:
    return os.path.isdir(os.path.join(repo_path, ".git")) or bool(
        subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip() == "true"
    )


def detect_changes(
    repo_path: str,
    *,
    base_branch: str | None = None,
    explicit_paths: list[str] | None = None,
    explicit_node_ids: list[str] | None = None,
) -> ChangeSet:
    repo_path = os.path.abspath(repo_path)
    if not is_git_repo(repo_path):
        return ChangeSet(
            repo_path=repo_path, branch="", commit_sha="", is_git_repo=False,
            explicit_paths=explicit_paths or [], explicit_node_ids=explicit_node_ids or [],
        )

    branch = _git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    commit_sha = _git(repo_path, ["rev-parse", "HEAD"]).strip()

    status = _git(repo_path, ["status", "--porcelain=v1", "--untracked-files=all"])
    staged, unstaged, untracked = [], [], []
    for line in status.splitlines():
        if not line or len(line) < 4:
            continue
        index_state, worktree_state = line[0], line[1]
        rel = line[3:]
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        if repo_policy.is_excluded(repo_path, rel):
            continue
        if index_state == "?" and worktree_state == "?":
            untracked.append(rel)
            continue
        if index_state not in (" ", "?"):
            staged.append(rel)
        if worktree_state not in (" ", "?"):
            unstaged.append(rel)

    committed_vs_base: list[str] = []
    if base_branch:
        diff = _git(repo_path, ["diff", "--name-only", f"{base_branch}...HEAD"])
        committed_vs_base = [line for line in diff.splitlines() if line and not repo_policy.is_excluded(repo_path, line)]

    return ChangeSet(
        repo_path=repo_path,
        branch=branch,
        commit_sha=commit_sha,
        is_git_repo=True,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        committed_vs_base=committed_vs_base,
        base_branch=base_branch,
        explicit_paths=explicit_paths or [],
        explicit_node_ids=explicit_node_ids or [],
    )


def _content_hash(repo_path: str, rel: str) -> str:
    path = os.path.join(repo_path, rel)
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return "missing"


def fingerprint(
    changeset: ChangeSet,
    *,
    python_version: str,
    pytest_version: str | None,
    config_fingerprint_inputs: list[str],
) -> str:
    content_hashes = {
        rel: _content_hash(changeset.repo_path, rel) for rel in sorted(changeset.changed_files)
    }
    config_hashes = {
        rel: _content_hash(changeset.repo_path, rel) for rel in sorted(config_fingerprint_inputs)
    }
    payload = {
        "repo_path": changeset.repo_path,
        "branch": changeset.branch,
        "commit_sha": changeset.commit_sha,
        "changed_files": sorted(changeset.changed_files),
        "content_hashes": content_hashes,
        "config_hashes": config_hashes,
        "python_version": python_version,
        "pytest_version": pytest_version,
        "explicit_node_ids": sorted(changeset.explicit_node_ids),
    }
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
