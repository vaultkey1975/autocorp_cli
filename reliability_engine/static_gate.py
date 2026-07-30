#!/usr/bin/env python3
"""Syntax, lint, and mypy gate."""

import os
import py_compile
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass
class GateResult:
    ok: bool
    stage: str
    output: str = ""
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StaticIssue:
    tool: str
    path: str
    code: str
    message: str
    line: int = 0

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.tool, self.path, self.code, self.message)

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{self.tool}:{location}: {self.code} {self.message}".strip()


def _python_files(root: str) -> list[str]:
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv"}]
        for filename in files:
            if filename.endswith(".py"):
                out.append(os.path.join(dirpath, filename))
    return out


def _scoped_python_files(root: str, paths: list[str] | None) -> list[str]:
    if not paths:
        return _python_files(root)
    out = []
    abs_root = os.path.abspath(root)
    for item in paths:
        path = item if os.path.isabs(item) else os.path.join(root, item)
        path = os.path.abspath(path)
        if not path.startswith(abs_root + os.sep) and path != abs_root:
            continue
        if os.path.isdir(path):
            out.extend(_python_files(path))
        elif path.endswith(".py") and os.path.exists(path):
            out.append(path)
    return sorted(set(out))


def _tool_path(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidate = os.path.join(os.path.dirname(sys.executable), name)
    return candidate if os.path.exists(candidate) else None


def _rel(workspace: str, path: str) -> str:
    return os.path.relpath(path, workspace).replace(os.sep, "/")


def _parse_mypy(output: str) -> list[StaticIssue]:
    issues = []
    pattern = re.compile(r"^(.*?):(\d+):\s+error:\s+(.*?)(?:\s+\[([A-Za-z0-9_-]+)\])?$")
    for line in output.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        path, line_no, message, code = match.groups()
        issues.append(StaticIssue("mypy", path.replace(os.sep, "/"), code or "error", message, int(line_no)))
    return issues


def _parse_ruff(output: str) -> list[StaticIssue]:
    issues = []
    current_code = ""
    current_message = ""
    for line in output.splitlines():
        header = re.match(r"^([A-Z]\d{3}|[A-Z]+[0-9]+)\s+(?:\[[* ]\]\s+)?(.+)$", line.strip())
        if header:
            current_code, current_message = header.groups()
            continue
        location = re.match(r"^\s*-->\s+(.*?):(\d+):\d+", line)
        if location and current_code:
            path, line_no = location.groups()
            issues.append(
                StaticIssue("ruff", path.replace(os.sep, "/"), current_code, current_message, int(line_no))
            )
            current_code = ""
            current_message = ""
    return issues


def _parse_syntax(output: str, workspace: str) -> list[StaticIssue]:
    issues = []
    match = re.search(r'File "([^"]+)", line (\d+)', output)
    if match:
        path, line_no = match.groups()
        rel = path if not os.path.isabs(path) else _rel(workspace, path)
        issues.append(StaticIssue("syntax", rel, "syntax", output.strip(), int(line_no)))
    elif output.strip():
        issues.append(StaticIssue("syntax", "", "syntax", output.strip()))
    return issues


class StaticGate:
    def __init__(self, require_mypy: bool = True, require_lint: bool = True):
        self.require_mypy = require_mypy
        self.require_lint = require_lint

    def collect_issues(self, workspace: str, paths: list[str] | None = None) -> list[StaticIssue]:
        files = _scoped_python_files(workspace, paths)
        if not files:
            return []

        issues: list[StaticIssue] = []
        rel_files = [_rel(workspace, path) for path in files]
        for path in files:
            try:
                py_compile.compile(path, doraise=True)
            except py_compile.PyCompileError as exc:
                issues.extend(_parse_syntax(str(exc), workspace))

        lint_cmd = None
        ruff = _tool_path("ruff")
        flake8 = _tool_path("flake8")
        if ruff:
            lint_cmd = [ruff, "check", *rel_files]
        elif flake8:
            lint_cmd = [flake8, *rel_files]
        if lint_cmd:
            proc = subprocess.run(lint_cmd, cwd=workspace, text=True, capture_output=True)
            if proc.returncode != 0:
                combined = (proc.stdout + proc.stderr).strip()
                if ruff:
                    issues.extend(_parse_ruff(combined))
                else:
                    issues.append(StaticIssue("lint", ",".join(rel_files), "lint", combined))
        elif self.require_lint:
            issues.append(StaticIssue("lint", ",".join(rel_files), "missing", "ruff or flake8 is not installed"))

        if self.require_mypy:
            mypy = _tool_path("mypy")
            if not mypy:
                issues.append(StaticIssue("mypy", ",".join(rel_files), "missing", "mypy is not installed"))
                return issues
            proc = subprocess.run([mypy, *rel_files], cwd=workspace, text=True, capture_output=True)
            if proc.returncode != 0:
                issues.extend(_parse_mypy((proc.stdout + proc.stderr).strip()))
        return issues

    def run(self, workspace: str, paths: list[str] | None = None) -> GateResult:
        files = _scoped_python_files(workspace, paths)
        if not files:
            return GateResult(True, "passed", "no Python files to check")
        rel_files = [_rel(workspace, path) for path in files]
        issues = self.collect_issues(workspace, paths)
        if issues:
            stage = issues[0].tool if issues[0].tool in {"syntax", "mypy"} else "lint"
            return GateResult(False, stage, "\n".join(issue.render() for issue in issues), {
                "issues": issues,
            })

        return GateResult(True, "passed", f"static gate passed for {', '.join(rel_files)}")

    def run_delta(self, workspace: str, paths: list[str], before: list[StaticIssue] | None = None) -> GateResult:
        before_issues = before if before is not None else self.collect_issues(workspace, paths)
        after_issues = self.collect_issues(workspace, paths)
        before_ids = {issue.identity for issue in before_issues}
        new_issues = [issue for issue in after_issues if issue.identity not in before_ids]
        rel_files = [_rel(workspace, path) for path in _scoped_python_files(workspace, paths)]
        if new_issues:
            stage = new_issues[0].tool if new_issues[0].tool in {"syntax", "mypy"} else "lint"
            return GateResult(False, stage, "\n".join(issue.render() for issue in new_issues), {
                "before": before_issues,
                "after": after_issues,
                "new": new_issues,
                "pre_existing": before_issues,
            })
        return GateResult(True, "passed", f"static delta passed for {', '.join(rel_files)}", {
            "before": before_issues,
            "after": after_issues,
            "new": [],
            "pre_existing": before_issues,
        })
