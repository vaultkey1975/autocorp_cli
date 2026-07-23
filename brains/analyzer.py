#!/usr/bin/env python3
"""
Project Intelligence Engine  (AutoCorp CLI - brains)  [Phase 1B]
==================================================================

A read-only analyzer that goes one level beyond Phase 1A's Repository
Scanner: instead of raw counts, it forms a picture of the repository's
ARCHITECTURE - what kind of project this is, how it's organized, and how
healthy it looks - purely from evidence found on disk (file names, import
statements, directory structure, packaging metadata). Nothing here is
guessed or hardcoded.

READ-ONLY: only opens files for reading, and only via brains.scanner's
non-mutating git-independent helpers. Never writes, never calls a model.

Public API:
    run_analysis(repo_path) -> ProjectAnalysis

Reuse of brains.scanner (Phase 1A), instead of duplicating its logic:
  * `scanner.iter_python_files`  - the ignore-dir-aware file walk
  * `scanner.is_test_file`       - the test-file naming convention
  * `scanner.count_markers`      - TODO/FIXME/pass/NotImplementedError rules
  * `scanner.run_scan`           - the whole-repository Quality Indicators
                                   (matches `scan`'s own numbers exactly)

Scope note - two deliberately different universes:
  * Quality Indicators (todo/fixme/pass/not_implemented) describe the WHOLE
    physical repository, reusing `scanner.run_scan` verbatim, so they always
    agree with `python autocorp.py scan`.
  * Everything else here - project type, entry points, test framework,
    directory layout, largest module/package, code statistics - describes
    AutoCorp CLI's OWN source tree. Generated/output directories (workspace/,
    data/) are excluded on top of scanner.IGNORE_DIRS, mirroring pytest.ini's
    own `norecursedirs`. This repo's workspace/ alone holds hundreds of
    separate AI-generated demo projects (PySide6 desktop apps, etc.) - if
    those were folded into the architecture analysis, "project type" and
    "top directories" would describe the generated OUTPUT of AutoCorp CLI
    instead of AutoCorp CLI itself, which would be actively misleading.
"""

import ast
import os
from dataclasses import dataclass, field

from brains import scanner

# On top of scanner.IGNORE_DIRS: directories that hold generated output
# rather than AutoCorp CLI's own source. See the scope note above.
_ARCH_IGNORE_DIRS = scanner.IGNORE_DIRS | {"workspace", "data"}

_ENTRY_POINT_NAMES = ["autocorp.py", "main.py", "app.py", "manage.py", "__main__.py"]

_DEPENDENCY_FILE_NAMES = [
    "pyproject.toml", "requirements.txt", "requirements-dev.txt",
    "Pipfile", "poetry.lock", "setup.py",
]
_PACKAGING_FILES = {"setup.py", "pyproject.toml"}

_WEB_FRAMEWORKS = ("django", "fastapi", "flask")  # checked in this priority order
_WEB_FRAMEWORK_LABELS = {"django": "Django", "fastapi": "FastAPI", "flask": "Flask"}
_DESKTOP_MODULES = {"tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "kivy"}
_CLI_MODULES = {"argparse", "click"}

_LANGUAGE_EXTENSIONS = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".go": "Go",
    ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".c": "C", ".cpp": "C++",
    ".cs": "C#", ".php": "PHP",
}

_TEST_FRAMEWORK_MODULES = {"pytest", "unittest", "nose"}

# Health: weighted "issues per file" density -> a plain, transparent scale.
# Weights reflect severity: an unfinished branch (NotImplementedError) is
# worse than a to-do note. Thresholds are on the density, not raw counts, so
# health scales sensibly with repository size.
_HEALTH_WEIGHTS = {"todo": 1, "fixme": 2, "not_implemented": 3}
_HEALTH_THRESHOLDS = [
    (0.05, "Excellent"),
    (0.15, "Good"),
    (0.35, "Fair"),
]  # anything above the last threshold -> "Needs Attention"

# Confidence: a base score for having ANY matching evidence, plus a per-item
# bonus for each additional corroborating signal, capped below 100 (a static
# analyzer should never claim certainty). Unknown gets a low, fixed score.
_BASE_CONFIDENCE = 70
_EVIDENCE_BONUS = 9
_MAX_CONFIDENCE = 98
_UNKNOWN_CONFIDENCE = 35


@dataclass
class DirectoryStat:
    """One top-level source directory's share of the codebase."""
    name: str
    python_files: int
    python_lines: int


@dataclass
class ProjectAnalysis:
    """A structured picture of the repository's architecture. Pure data -
    nothing here is printed; that's the CLI's job. See the module docstring
    for which fields cover the whole repo vs. AutoCorp CLI's own source."""
    repo_path: str
    project_type: str
    project_type_evidence: list = field(default_factory=list)
    primary_language: str = "Unknown"
    entry_points: list = field(default_factory=list)
    dependency_files: list = field(default_factory=list)
    test_framework: str = "unknown"

    python_file_count: int = 0          # AutoCorp CLI's own source only
    total_python_lines: int = 0
    average_file_size: float = 0.0
    largest_module: str = ""
    largest_module_lines: int = 0
    largest_package: str = ""
    largest_package_lines: int = 0
    top_directories: list = field(default_factory=list)

    todo_count: int = 0                 # whole repository (reused from scanner)
    fixme_count: int = 0
    pass_count: int = 0
    not_implemented_count: int = 0

    overall_health: str = "Unknown"
    confidence: int = 0


# --------------------------------------------------------------------------- #
# AST-based import evidence
# --------------------------------------------------------------------------- #
# Deliberately AST-based, not a text/regex scan: a module name that merely
# appears in a string literal, comment, or docstring (e.g. a test fixture
# that writes example source as a string) must never be mistaken for a real
# import statement. Mirrors scanner.py's own precedent of using ast for
# `pass`-statement counting over a naive text search.
def _import_roots(content: str) -> set:
    """Top-level imported module root names (e.g. `flask` from both
    `import flask` and `from flask.views import View`). Returns an empty set
    if the file doesn't parse - degrades gracefully rather than crashing on
    an unrelated syntax error elsewhere in the repository."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _test_framework_signals(content: str) -> set:
    """Which of {pytest, unittest, nose} this file's real import statements
    show. Exact module match (not the split-root `_import_roots` reduction):
    `from unittest.mock import patch` is common alongside pytest and must
    NOT be read as "this file uses the unittest framework" - only an import
    of the `unittest` package itself counts."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()
    hits = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _TEST_FRAMEWORK_MODULES:
                    hits.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module in _TEST_FRAMEWORK_MODULES:
                hits.add(node.module)
    return hits


# --------------------------------------------------------------------------- #
# Entry points / dependency files
# --------------------------------------------------------------------------- #
def _detect_entry_points(repo_path: str) -> list:
    """Common startup files present at the repository root."""
    return [name for name in _ENTRY_POINT_NAMES
            if os.path.isfile(os.path.join(repo_path, name))]


def _detect_dependency_files(repo_path: str) -> list:
    """Common dependency/packaging manifests present at the repository root."""
    return [name for name in _DEPENDENCY_FILE_NAMES
            if os.path.isfile(os.path.join(repo_path, name))]


def _entry_point_imports(repo_path: str, entry_points: list) -> set:
    """The top-level imported module names of the entry-point file(s) only.
    This is what a project TYPE detector should trust most: how the project
    actually starts, not an incidental import found anywhere in the tree
    (e.g. a template generator or a single helper module)."""
    imports = set()
    for name in entry_points:
        try:
            with open(os.path.join(repo_path, name), encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue
        imports |= _import_roots(content)
    return imports


def _has_top_level_package(repo_path: str) -> bool:
    """Whether a top-level directory (excluding generated/ignored ones) is an
    importable package (contains __init__.py) - evidence of a library/
    package layout rather than a loose script collection."""
    try:
        entries = os.listdir(repo_path)
    except OSError:
        return False
    for name in entries:
        if name in _ARCH_IGNORE_DIRS or name.startswith("."):
            continue
        full = os.path.join(repo_path, name)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
            return True
    return False


# --------------------------------------------------------------------------- #
# Primary language
# --------------------------------------------------------------------------- #
def _detect_primary_language(repo_path: str) -> str:
    """The most common recognized source-file language under the
    architecture scope. Evidence-based rather than assuming "Python"."""
    counts = {}
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _ARCH_IGNORE_DIRS]
        for name in files:
            lang = _LANGUAGE_EXTENSIONS.get(os.path.splitext(name)[1])
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return "Unknown"
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


# --------------------------------------------------------------------------- #
# Single pass over the architecture-scoped source tree
# --------------------------------------------------------------------------- #
def _analyze_source_tree(repo_path: str):
    """One walk over AutoCorp CLI's own Python files (workspace/data
    excluded). Collects per-directory file/line totals, the largest single
    module, the set of top-level imported module names (project-type / test-
    framework evidence), and whether a conftest.py exists anywhere.

    Returns (dir_stats, largest_module, largest_module_lines, total_lines,
    file_count, all_imports, test_framework_hits)."""
    dir_stats = {}  # top-level dir name -> [file_count, line_count]
    largest_module = ""
    largest_module_lines = 0
    total_lines = 0
    file_count = 0
    all_imports = set()
    test_framework_hits = set()

    for full_path, name in scanner.iter_python_files(repo_path, ignore_dirs=_ARCH_IGNORE_DIRS):
        file_count += 1
        try:
            with open(full_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue

        lines = len(content.splitlines())
        total_lines += lines
        rel = os.path.relpath(full_path, repo_path).replace(os.sep, "/")

        if lines > largest_module_lines:
            largest_module, largest_module_lines = rel, lines

        parts = rel.split("/")
        if len(parts) > 1:  # file lives inside a top-level directory
            bucket = dir_stats.setdefault(parts[0], [0, 0])
            bucket[0] += 1
            bucket[1] += lines

        all_imports |= _import_roots(content)

        if name == "conftest.py":
            test_framework_hits.add("pytest")
        elif scanner.is_test_file(name):
            test_framework_hits |= _test_framework_signals(content)

    return (dir_stats, largest_module, largest_module_lines, total_lines,
            file_count, all_imports, test_framework_hits)


def _directory_stats(dir_stats: dict) -> list:
    """DirectoryStat list, largest (by total Python lines) first; ties broken
    by name for a deterministic, reproducible order."""
    stats = [DirectoryStat(name=name, python_files=v[0], python_lines=v[1])
              for name, v in dir_stats.items()]
    stats.sort(key=lambda d: (-d.python_lines, d.name))
    return stats


# --------------------------------------------------------------------------- #
# Test framework
# --------------------------------------------------------------------------- #
def _has_pytest_config(repo_path: str) -> bool:
    """A pytest.ini, or a [tool.pytest.ini_options] / [tool:pytest] section,
    is itself strong evidence of pytest - stronger than per-file imports,
    since pytest's own assert-rewriting means most test files never need to
    `import pytest` explicitly."""
    if os.path.isfile(os.path.join(repo_path, "pytest.ini")):
        return True
    for name, needle in (("pyproject.toml", "[tool.pytest"), ("setup.cfg", "[tool:pytest]")):
        path = os.path.join(repo_path, name)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    if needle in fh.read():
                        return True
            except OSError:
                pass  # unreadable config file - fall through to the next candidate
    return False


def _detect_test_framework(repo_path: str, test_framework_hits: set) -> str:
    hits = set(test_framework_hits)
    if _has_pytest_config(repo_path):
        hits.add("pytest")
    if len(hits) > 1:
        return "mixed"
    if hits:
        return hits.pop()
    return "unknown"


# --------------------------------------------------------------------------- #
# Project type
# --------------------------------------------------------------------------- #
def _detect_project_type(repo_path: str, entry_points: list, dependency_files: list,
                          entry_imports: set, all_imports: set):
    """Returns (project_type, evidence_list).

    Primary evidence is what the entry point(s) actually import - how the
    project starts is the strongest signal of what it IS. A framework import
    found only deep in the tree (a template, a test helper, a single
    feature module) does NOT get to override that: it's evidence about one
    file, not the project. Only when there is no detected entry point at all
    does this fall back to a repository-wide import scan.

    Evidence is checked in a fixed priority order (web framework > desktop
    toolkit > CLI > packaged library), since more than one signal could be
    present and the most specific one should win.
    """
    evidence = []
    primary_imports = entry_imports if entry_points else all_imports

    for module in _WEB_FRAMEWORKS:
        if module in primary_imports:
            evidence.append(f"`{module}` imported")
            if module == "django" and "manage.py" in entry_points:
                evidence.append("manage.py entry point")
            return _WEB_FRAMEWORK_LABELS[module], evidence

    desktop_hits = sorted(primary_imports & _DESKTOP_MODULES)
    if desktop_hits:
        evidence.extend(f"`{m}` imported" for m in desktop_hits)
        return "Desktop application", evidence

    cli_hits = sorted(primary_imports & _CLI_MODULES)
    if entry_points or cli_hits:
        if entry_points:
            evidence.append(f"entry point ({', '.join(entry_points)})")
        evidence.extend(f"`{m}` imported" for m in cli_hits)
        return "Python CLI", evidence

    packaging_hits = [f for f in dependency_files if f in _PACKAGING_FILES]
    if packaging_hits:
        evidence.append(f"packaging metadata ({', '.join(packaging_hits)})")
        if _has_top_level_package(repo_path):
            evidence.append("installable package structure (__init__.py)")
            return "Python library", evidence
        return "Package", evidence

    return "Unknown", evidence


def _confidence(project_type: str, evidence: list) -> int:
    """A base score for reaching a non-Unknown verdict at all, plus a bonus
    per corroborating piece of evidence, capped below 100."""
    if project_type == "Unknown":
        return _UNKNOWN_CONFIDENCE
    return min(_MAX_CONFIDENCE, _BASE_CONFIDENCE + _EVIDENCE_BONUS * len(evidence))


# --------------------------------------------------------------------------- #
# Overall health
# --------------------------------------------------------------------------- #
def _overall_health(python_file_count: int, todo: int, fixme: int, not_implemented: int) -> str:
    if python_file_count == 0:
        return "Unknown"
    weighted = (todo * _HEALTH_WEIGHTS["todo"]
                + fixme * _HEALTH_WEIGHTS["fixme"]
                + not_implemented * _HEALTH_WEIGHTS["not_implemented"])
    density = weighted / python_file_count
    for ceiling, label in _HEALTH_THRESHOLDS:
        if density <= ceiling:
            return label
    return "Needs Attention"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def run_analysis(repo_path: str) -> ProjectAnalysis:
    """Analyze `repo_path` and return a ProjectAnalysis. Read-only throughout
    - only opens files for reading; never writes, never calls a model."""
    repo_path = os.path.abspath(repo_path)

    entry_points = _detect_entry_points(repo_path)
    dependency_files = _detect_dependency_files(repo_path)

    (dir_stats, largest_module, largest_module_lines, total_lines,
     file_count, all_imports, test_framework_hits) = _analyze_source_tree(repo_path)

    directories = _directory_stats(dir_stats)
    largest_package = directories[0] if directories else DirectoryStat("", 0, 0)

    entry_imports = _entry_point_imports(repo_path, entry_points)
    project_type, evidence = _detect_project_type(
        repo_path, entry_points, dependency_files, entry_imports, all_imports)

    scan = scanner.run_scan(repo_path)  # whole-repo Quality Indicators (reused, not duplicated)

    return ProjectAnalysis(
        repo_path=repo_path,
        project_type=project_type,
        project_type_evidence=evidence,
        primary_language=_detect_primary_language(repo_path),
        entry_points=entry_points,
        dependency_files=dependency_files,
        test_framework=_detect_test_framework(repo_path, test_framework_hits),
        python_file_count=file_count,
        total_python_lines=total_lines,
        average_file_size=round(total_lines / file_count, 1) if file_count else 0.0,
        largest_module=largest_module,
        largest_module_lines=largest_module_lines,
        largest_package=largest_package.name,
        largest_package_lines=largest_package.python_lines,
        top_directories=directories,
        todo_count=scan.todo_count,
        fixme_count=scan.fixme_count,
        pass_count=scan.pass_count,
        not_implemented_count=scan.not_implemented_count,
        overall_health=_overall_health(
            scan.python_file_count, scan.todo_count, scan.fixme_count,
            scan.not_implemented_count),
        confidence=_confidence(project_type, evidence),
    )
