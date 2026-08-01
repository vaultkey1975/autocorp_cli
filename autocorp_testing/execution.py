"""Real subprocess execution: compile checks, import checks, and pytest runs.

Every check here actually executes code — there are no shortcuts, hidden
exclusions, or synthesized results. FULL runs the discovered strict command
completely unmodified so its exit code and warnings-as-errors behavior are
preserved exactly as the target repository defined them.
"""

from __future__ import annotations

import json
import os
import py_compile
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from autocorp_testing.schemas import TestResult

_SUMMARY_RE = re.compile(r"(?P<count>\d+) (?P<kind>passed|failed|errors?|skipped|deselected)\b")
_ITEM_RE = re.compile(r"^(?P<node>\S+::\S+|\S+\.py)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED)\b")
_COLLECTED_RE = re.compile(r"\bcollected (?P<count>\d+) item")
_SLOW_RE = re.compile(r"^(?P<duration>\d+(?:\.\d+)?)s\s+(?P<phase>setup|call|teardown)\s+(?P<node>\S+)")


@dataclass
class CompileCheckResult:
    ok: bool
    errors: list[str]
    duration_seconds: float
    files_checked: int
    command: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "duration_seconds": self.duration_seconds,
            "files_checked": self.files_checked,
            "command": self.command,
        }


def run_fast_compile_check(repo_path: str, changed_python_files: list[str]) -> CompileCheckResult:
    """In-process syntax check restricted to changed files — the FAST path."""
    start = time.perf_counter()
    errors = []
    checked = 0
    for rel in changed_python_files:
        if not rel.endswith(".py"):
            continue
        full = os.path.join(repo_path, rel)
        if not os.path.isfile(full):
            continue
        checked += 1
        try:
            py_compile.compile(full, doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"{rel}: {exc.msg}")
    return CompileCheckResult(ok=not errors, errors=errors, duration_seconds=time.perf_counter() - start, files_checked=checked)


def run_full_compile_check(compile_command: list[str], *, cwd: str, timeout: int = 180) -> CompileCheckResult:
    """Runs the discovered repository compile command as a subprocess."""
    start = time.perf_counter()
    try:
        proc = subprocess.run(compile_command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CompileCheckResult(ok=False, errors=[str(exc)], duration_seconds=time.perf_counter() - start, files_checked=0, command=compile_command)
    duration = time.perf_counter() - start
    ok = proc.returncode == 0
    errors = [] if ok else [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()]
    match = re.search(r"Compiled (\d+) maintained Python file", proc.stdout)
    checked = int(match.group(1)) if match else 0
    return CompileCheckResult(ok=ok, errors=errors, duration_seconds=duration, files_checked=checked, command=compile_command)


def run_import_checks(venv_python: str, repo_path: str, modules: list[str], timeout: int = 60) -> list[dict[str, Any]]:
    if not modules:
        return []
    script = (
        "import sys, json\n"
        f"sys.path.insert(0, {repo_path!r})\n"
        "results = []\n"
        f"for m in {modules!r}:\n"
        "    try:\n"
        "        __import__(m)\n"
        "        results.append({'module': m, 'ok': True, 'error': None})\n"
        "    except Exception as exc:\n"
        "        results.append({'module': m, 'ok': False, 'error': f'{type(exc).__name__}: {exc}'})\n"
        "print(json.dumps(results))\n"
    )
    try:
        proc = subprocess.run(
            [venv_python, "-c", script], cwd=repo_path, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [{"module": m, "ok": False, "error": str(exc)} for m in modules]
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else [
            {"module": m, "ok": False, "error": proc.stderr[-500:]} for m in modules
        ]
    except (json.JSONDecodeError, IndexError):
        return [{"module": m, "ok": False, "error": proc.stderr[-500:]} for m in modules]


@dataclass
class PytestRunResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    passed: int
    failed: int
    errors: int
    skipped: int
    deselected: int = 0
    collected: int = 0
    slowest_tests: list[dict[str, Any]] = field(default_factory=list)
    collection_duration_seconds: float | None = None
    per_test: list[TestResult] = field(default_factory=list)
    timed_out: bool = False


def _parse_summary(stdout: str) -> tuple[int, int, int, int, int]:
    lines = [line.strip("= ") for line in stdout.splitlines() if line.strip()]
    summary = next((line for line in reversed(lines) if re.search(r"\b(passed|failed|errors?|skipped|deselected)\b", line) and " in " in line), "")
    counts = {"passed": 0, "failed": 0, "error": 0, "errors": 0, "skipped": 0, "deselected": 0}
    for match in _SUMMARY_RE.finditer(summary):
        counts[match.group("kind")] = int(match.group("count"))
    return counts["passed"], counts["failed"], counts["error"] + counts["errors"], counts["skipped"], counts["deselected"]


def _parse_collected(stdout: str) -> int:
    for line in stdout.splitlines():
        match = _COLLECTED_RE.search(line)
        if match:
            return int(match.group("count"))
    passed, failed, errors, skipped, _ = _parse_summary(stdout)
    return passed + failed + errors + skipped


def _parse_slowest(stdout: str) -> list[dict[str, Any]]:
    out = []
    for line in stdout.splitlines():
        match = _SLOW_RE.match(line.strip())
        if not match:
            continue
        out.append({
            "duration_seconds": float(match.group("duration")),
            "phase": match.group("phase"),
            "test": match.group("node"),
        })
    return out


def _parse_items(stdout: str, per_test_durations: dict[str, float] | None = None) -> list[TestResult]:
    per_test_durations = per_test_durations or {}
    out = []
    for line in stdout.splitlines():
        match = _ITEM_RE.match(line.strip())
        if not match:
            continue
        node = match.group("node")
        outcome = match.group("outcome").lower()
        out.append(TestResult(node_id=node, outcome=outcome, duration_seconds=per_test_durations.get(node, 0.0)))
    return out


def run_pytest(
    venv_python: str,
    repo_path: str,
    targets: list[str],
    *,
    extra_args: list[str] | None = None,
    itemized: bool = True,
    timeout: int = 1800,
) -> PytestRunResult:
    args = [venv_python, "-m", "pytest"]
    if itemized:
        # -vv (not -v) so verbose reporting wins even when the target
        # repository's own addopts already set -q (verbosity is additive:
        # count(-v) - count(-q)).
        args.extend(["-vv"])
    args.extend(extra_args or [])
    args.extend(targets)
    start = time.perf_counter()
    try:
        proc = subprocess.run(args, cwd=repo_path, capture_output=True, text=True, timeout=timeout)
        timed_out = False
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
    duration = time.perf_counter() - start
    passed, failed, errors, skipped, deselected = _parse_summary(stdout)
    collected = _parse_collected(stdout)
    per_test = _parse_items(stdout) if itemized else []
    return PytestRunResult(
        command=args, returncode=returncode, stdout=stdout, stderr=stderr,
        duration_seconds=duration, passed=passed, failed=failed, errors=errors,
        skipped=skipped, deselected=deselected, collected=collected,
        slowest_tests=_parse_slowest(stdout), per_test=per_test, timed_out=timed_out,
    )


def run_strict_full(strict_full_command: list[str], *, cwd: str, timeout: int = 3600) -> PytestRunResult:
    """Runs the discovered strict command completely unmodified."""
    start = time.perf_counter()
    try:
        proc = subprocess.run(strict_full_command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        timed_out = False
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
    duration = time.perf_counter() - start
    passed, failed, errors, skipped, deselected = _parse_summary(stdout)
    return PytestRunResult(
        command=strict_full_command, returncode=returncode, stdout=stdout, stderr=stderr,
        duration_seconds=duration, passed=passed, failed=failed, errors=errors,
        skipped=skipped, deselected=deselected, collected=_parse_collected(stdout),
        slowest_tests=_parse_slowest(stdout), per_test=[], timed_out=timed_out,
    )
