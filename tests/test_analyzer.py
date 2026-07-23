#!/usr/bin/env python3
"""Tests for the Project Intelligence Engine (brains/analyzer.py, Phase 1B).

Covers: project-type detection (including the regression where an incidental
import deep in the tree - e.g. a template generator - must NOT override what
the entry point actually imports), dependency-file / entry-point detection,
test-framework detection (including the unittest.mock false-positive fix),
repository layout, code statistics, quality-indicator reuse from Phase 1A's
scanner, overall health, confidence, and read-only behaviour.
"""

import os
import subprocess

from brains import scanner
from brains.analyzer import DirectoryStat, ProjectAnalysis, run_analysis


def _write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #
def test_run_analysis_returns_project_analysis(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    result = run_analysis(str(tmp_path))
    assert isinstance(result, ProjectAnalysis)
    assert result.repo_path == str(tmp_path)


# --------------------------------------------------------------------------- #
# Project type detection
# --------------------------------------------------------------------------- #
def test_detects_python_cli_from_entry_point_and_argparse(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n\ndef main():\n    pass\n")
    result = run_analysis(str(tmp_path))
    assert result.project_type == "Python CLI"
    assert "main.py" in result.entry_points


def test_detects_flask_from_entry_point_import(tmp_path):
    _write(tmp_path / "app.py", "from flask import Flask\napp = Flask(__name__)\n")
    result = run_analysis(str(tmp_path))
    assert result.project_type == "Flask"


def test_detects_fastapi_from_entry_point_import(tmp_path):
    _write(tmp_path / "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    result = run_analysis(str(tmp_path))
    assert result.project_type == "FastAPI"


def test_detects_django_from_manage_py(tmp_path):
    _write(tmp_path / "manage.py", (
        "import django\n"
        "from django.core.management import execute_from_command_line\n"
    ))
    result = run_analysis(str(tmp_path))
    assert result.project_type == "Django"
    assert any("manage.py" in e for e in result.project_type_evidence)


def test_detects_desktop_application_from_entry_point_import(tmp_path):
    _write(tmp_path / "main.py", "import tkinter\nroot = tkinter.Tk()\n")
    result = run_analysis(str(tmp_path))
    assert result.project_type == "Desktop application"


def test_incidental_import_outside_entry_point_does_not_override_type(tmp_path):
    """Regression: a template/helper file deep in the tree that imports a
    desktop toolkit must NOT make the whole project look like a desktop app
    when the entry point itself is a plain argparse CLI."""
    _write(tmp_path / "main.py", "import argparse\n")
    _write(tmp_path / "app" / "gui_template.py", "from PySide6.QtWidgets import QApplication\n")
    result = run_analysis(str(tmp_path))
    assert result.project_type == "Python CLI"


def test_detects_python_library_with_packaging_and_package_structure(tmp_path):
    _write(tmp_path / "pyproject.toml", "[project]\nname = \"x\"\n")
    _write(tmp_path / "mylib" / "__init__.py", "")
    _write(tmp_path / "mylib" / "core.py", "x = 1\n")
    result = run_analysis(str(tmp_path))
    assert result.project_type == "Python library"


def test_detects_package_with_packaging_only(tmp_path):
    _write(tmp_path / "setup.py", "from setuptools import setup\nsetup(name='x')\n")
    _write(tmp_path / "loose.py", "x = 1\n")
    result = run_analysis(str(tmp_path))
    assert result.project_type == "Package"


def test_detects_unknown_when_no_evidence(tmp_path):
    _write(tmp_path / "notes.py", "x = 1\n")
    result = run_analysis(str(tmp_path))
    assert result.project_type == "Unknown"
    assert result.confidence < 50


# --------------------------------------------------------------------------- #
# Entry points / dependency files
# --------------------------------------------------------------------------- #
def test_all_entry_point_candidates_detected(tmp_path):
    for name in ("autocorp.py", "main.py", "app.py", "manage.py", "__main__.py"):
        _write(tmp_path / name, "x = 1\n")
    result = run_analysis(str(tmp_path))
    assert set(result.entry_points) == {
        "autocorp.py", "main.py", "app.py", "manage.py", "__main__.py",
    }


def test_dependency_files_detected(tmp_path):
    _write(tmp_path / "requirements.txt", "requests\n")
    _write(tmp_path / "pyproject.toml", "[project]\nname = \"x\"\n")
    _write(tmp_path / "main.py", "x = 1\n")
    result = run_analysis(str(tmp_path))
    assert set(result.dependency_files) == {"requirements.txt", "pyproject.toml"}


def test_no_dependency_files_returns_empty_list(tmp_path):
    _write(tmp_path / "main.py", "x = 1\n")
    result = run_analysis(str(tmp_path))
    assert result.dependency_files == []


# --------------------------------------------------------------------------- #
# Test framework detection
# --------------------------------------------------------------------------- #
def test_pytest_detected_via_config_file(tmp_path):
    _write(tmp_path / "pytest.ini", "[pytest]\ntestpaths = tests\n")
    _write(tmp_path / "tests" / "test_x.py", "def test_x():\n    assert True\n")
    result = run_analysis(str(tmp_path))
    assert result.test_framework == "pytest"


def test_pytest_detected_via_conftest_presence(tmp_path):
    _write(tmp_path / "tests" / "conftest.py", "")
    _write(tmp_path / "tests" / "test_x.py", "def test_x():\n    assert True\n")
    result = run_analysis(str(tmp_path))
    assert result.test_framework == "pytest"


def test_unittest_detected_via_testcase_subclass(tmp_path):
    _write(tmp_path / "tests" / "test_x.py", (
        "import unittest\n\n"
        "class MyTest(unittest.TestCase):\n"
        "    def test_ok(self):\n"
        "        self.assertTrue(True)\n"
    ))
    result = run_analysis(str(tmp_path))
    assert result.test_framework == "unittest"


def test_unittest_mock_import_alone_is_not_unittest_framework(tmp_path):
    """Regression: `from unittest.mock import patch` is common in pytest-style
    tests and must not, by itself, be read as 'this project uses unittest'."""
    _write(tmp_path / "tests" / "test_x.py", (
        "from unittest.mock import patch\n\n"
        "def test_x():\n"
        "    with patch('os.getcwd'):\n"
        "        assert True\n"
    ))
    result = run_analysis(str(tmp_path))
    assert result.test_framework == "unknown"


def test_mixed_test_framework_detected(tmp_path):
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(tmp_path / "tests" / "test_a.py", "def test_a():\n    assert True\n")
    _write(tmp_path / "tests" / "test_b.py", (
        "import unittest\n\n"
        "class B(unittest.TestCase):\n"
        "    def test_b(self):\n"
        "        self.assertTrue(True)\n"
    ))
    result = run_analysis(str(tmp_path))
    assert result.test_framework == "mixed"


def test_unknown_test_framework_when_no_signal(tmp_path):
    _write(tmp_path / "main.py", "x = 1\n")
    result = run_analysis(str(tmp_path))
    assert result.test_framework == "unknown"


# --------------------------------------------------------------------------- #
# Repository layout / code statistics
# --------------------------------------------------------------------------- #
def test_directory_layout_and_code_statistics(tmp_path):
    _write(tmp_path / "pkg_a" / "one.py", "\n".join(f"x{i} = {i}" for i in range(10)) + "\n")
    _write(tmp_path / "pkg_a" / "two.py", "\n".join(f"y{i} = {i}" for i in range(5)) + "\n")
    _write(tmp_path / "pkg_b" / "big.py", "\n".join(f"z{i} = {i}" for i in range(50)) + "\n")
    _write(tmp_path / "root_module.py", "\n".join(f"r{i} = {i}" for i in range(3)) + "\n")

    result = run_analysis(str(tmp_path))

    assert result.python_file_count == 4
    assert result.total_python_lines == 10 + 5 + 50 + 3
    assert result.average_file_size == round((10 + 5 + 50 + 3) / 4, 1)
    assert result.largest_module == "pkg_b/big.py"
    assert result.largest_module_lines == 50
    assert result.largest_package == "pkg_b"
    assert result.largest_package_lines == 50

    names = [d.name for d in result.top_directories]
    assert names[0] == "pkg_b"  # largest by total lines
    assert "pkg_a" in names
    assert all(isinstance(d, DirectoryStat) for d in result.top_directories)

    pkg_a_stat = next(d for d in result.top_directories if d.name == "pkg_a")
    assert pkg_a_stat.python_files == 2
    assert pkg_a_stat.python_lines == 15


def test_workspace_and_data_dirs_excluded_from_architecture_scope(tmp_path):
    _write(tmp_path / "brains_like" / "core.py", "x = 1\n")
    _write(tmp_path / "workspace" / "generated_app" / "main.py",
           "\n".join(f"g{i} = {i}" for i in range(200)) + "\n")
    _write(tmp_path / "data" / "junk.py", "y = 1\n")

    result = run_analysis(str(tmp_path))

    names = [d.name for d in result.top_directories]
    assert "workspace" not in names
    assert "data" not in names
    assert result.python_file_count == 1        # only brains_like/core.py
    assert result.largest_module == "brains_like/core.py"


def test_average_file_size_zero_when_no_python_files(tmp_path):
    _write(tmp_path / "README.md", "no python here\n")
    result = run_analysis(str(tmp_path))
    assert result.python_file_count == 0
    assert result.average_file_size == 0.0
    assert result.largest_module == ""
    assert result.largest_package == ""
    assert result.top_directories == []


# --------------------------------------------------------------------------- #
# Quality indicators (reused from Phase 1A scanner)
# --------------------------------------------------------------------------- #
def test_quality_indicators_match_scanner_exactly(tmp_path):
    _write(tmp_path / "main.py", (
        "# TODO: one\n"
        "# FIXME: two\n"
        "def f():\n"
        "    pass\n"
        "\n"
        "def g():\n"
        "    raise NotImplementedError\n"
    ))
    _write(tmp_path / "workspace" / "app" / "extra.py", "# TODO: inside workspace\npass\n")

    scan = scanner.run_scan(str(tmp_path))
    analysis = run_analysis(str(tmp_path))

    assert analysis.todo_count == scan.todo_count
    assert analysis.fixme_count == scan.fixme_count
    assert analysis.pass_count == scan.pass_count
    assert analysis.not_implemented_count == scan.not_implemented_count
    # Sanity: the workspace TODO/pass really were counted (whole-repo scope).
    assert analysis.todo_count == 2
    assert analysis.pass_count == 2


# --------------------------------------------------------------------------- #
# Overall health
# --------------------------------------------------------------------------- #
def test_overall_health_excellent_with_no_issues(tmp_path):
    _write(tmp_path / "main.py", "def f():\n    return 1\n")
    result = run_analysis(str(tmp_path))
    assert result.overall_health == "Excellent"


def test_overall_health_needs_attention_with_heavy_issue_density(tmp_path):
    body = "\n".join(
        f"def f{i}():\n    raise NotImplementedError\n" for i in range(5)
    )
    _write(tmp_path / "main.py", body)
    result = run_analysis(str(tmp_path))
    assert result.overall_health == "Needs Attention"


def test_overall_health_unknown_with_no_python_files(tmp_path):
    _write(tmp_path / "README.md", "text\n")
    result = run_analysis(str(tmp_path))
    assert result.overall_health == "Unknown"


# --------------------------------------------------------------------------- #
# Confidence
# --------------------------------------------------------------------------- #
def test_confidence_higher_with_more_corroborating_evidence(tmp_path):
    _write(tmp_path / "weak" / "main.py", "print('hi')\n")
    weak = run_analysis(str(tmp_path / "weak"))

    _write(tmp_path / "strong" / "main.py", "import argparse\n")
    strong = run_analysis(str(tmp_path / "strong"))

    assert strong.project_type == "Python CLI"
    assert weak.project_type == "Python CLI"  # entry point alone is still CLI evidence
    assert strong.confidence >= weak.confidence


def test_confidence_is_low_and_fixed_for_unknown(tmp_path):
    _write(tmp_path / "notes.py", "x = 1\n")
    result = run_analysis(str(tmp_path))
    assert result.project_type == "Unknown"
    assert result.confidence == 35


def test_confidence_never_reaches_100(tmp_path):
    _write(tmp_path / "manage.py", (
        "import django\nimport argparse\nimport click\n"
        "from django.core.management import execute_from_command_line\n"
    ))
    result = run_analysis(str(tmp_path))
    assert result.confidence < 100


# --------------------------------------------------------------------------- #
# Read-only guarantee
# --------------------------------------------------------------------------- #
def test_run_analysis_never_modifies_the_directory(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    before_files = sorted(os.listdir(tmp_path))
    before_mtime = os.path.getmtime(tmp_path / "main.py")

    run_analysis(str(tmp_path))

    after_files = sorted(os.listdir(tmp_path))
    after_mtime = os.path.getmtime(tmp_path / "main.py")

    assert before_files == after_files
    assert before_mtime == after_mtime


def test_run_analysis_on_real_repo_does_not_modify_it():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True,
    ).stdout

    result = run_analysis(repo_root)

    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True,
    ).stdout

    assert before == after
    assert result.project_type == "Python CLI"
    assert result.test_framework == "pytest"
    assert result.python_file_count > 0
