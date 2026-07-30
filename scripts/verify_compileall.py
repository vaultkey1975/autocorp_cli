#!/usr/bin/env python3
"""Compile maintained repository Python files.

This is the repository-approved replacement for `python -m compileall .`.
It follows the same ownership boundary as pytest and git: tracked Python
files plus non-ignored untracked Python files are maintained source; ignored
directories such as `.venv/`, `workspace/`, and `data/` are runtime,
dependency, or generated artifacts and are not part of repository source
verification.
"""

from __future__ import annotations

import argparse
import os
import py_compile
import subprocess
import sys
from pathlib import Path


def _git_files(repo: Path, args: list[str]) -> list[Path]:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git file discovery failed")
    return [repo / line for line in proc.stdout.splitlines() if line.endswith(".py")]


def maintained_python_files(repo: Path) -> list[Path]:
    tracked = _git_files(repo, ["ls-files", "*.py"])
    untracked = _git_files(repo, ["ls-files", "--others", "--exclude-standard", "*.py"])
    return sorted(set(tracked + untracked), key=lambda path: path.relative_to(repo).as_posix())


def verify(repo: Path) -> tuple[bool, list[str]]:
    errors = []
    for path in maintained_python_files(repo):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            rel = path.relative_to(repo).as_posix()
            errors.append(f"{rel}: {exc.msg}")
    return not errors, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile tracked and non-ignored untracked Python source files.",
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="repository root to verify (default: current directory)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"Not a git repository root: {repo}", file=sys.stderr)
        return 2
    try:
        ok, errors = verify(repo)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not ok:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Compiled {len(maintained_python_files(repo))} maintained Python file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
