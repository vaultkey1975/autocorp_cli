#!/usr/bin/env python3
"""
Acceptance Gate: deterministic checks  (AutoCorp CLI - Phase 8F RED)
===================================================================

Drives the per-check registry of `brains/acceptance.py`: each check is a pure,
offline function of an AcceptanceContext (files on disk + already-computed test
result + review findings). RED: `brains.acceptance` does not exist yet.

Fully offline: no engine, no model, no network, no subprocess. The module is
imported lazily so each test fails individually.
"""

import pytest

from brains.reviewer import Finding


def _ctx(tmp_path, test_passed=True, review_findings=None, plan=None, request="req"):
    from brains.acceptance import AcceptanceContext
    return AcceptanceContext(
        workspace=str(tmp_path), plan=plan or {}, request=request,
        test_passed=test_passed, review_findings=review_findings or [],
    )


def _check(name, ctx):
    from brains.acceptance import CHECKS
    return CHECKS[name](ctx)


CLEAN_PY = "import os\n\n\ndef f():\n    return os.getcwd()\n"
BROKEN_PY = "def broken(:\n    pass\n"
MAIN_WITH_ENTRY = (
    "def main():\n    return 0\n\n\nif __name__ == '__main__':\n    main()\n"
)
CRUD_GOOD = (
    "def add_customer(conn, name, email, phone):\n"
    "    conn.execute(\"INSERT INTO customers (name, email, phone) "
    "VALUES (?, ?, ?)\", (name, email, phone))\n"
)
CRUD_BAD = (
    "def add_customer(conn, name, email, phone):\n"
    "    conn.execute(\"INSERT INTO customers (name, email, phone) "
    "VALUES (?, ?)\", (name, email))\n"
)


# --------------------------------------------------------------------------- #
# tests_pass
# --------------------------------------------------------------------------- #
def test_tests_pass_true(tmp_path):
    ok, _ = _check("tests_pass", _ctx(tmp_path, test_passed=True))
    assert ok is True


def test_tests_pass_false(tmp_path):
    ok, _ = _check("tests_pass", _ctx(tmp_path, test_passed=False))
    assert ok is False


# --------------------------------------------------------------------------- #
# imports_parse_clean
# --------------------------------------------------------------------------- #
def test_imports_clean_true(tmp_path):
    (tmp_path / "main.py").write_text(CLEAN_PY)
    ok, _ = _check("imports_parse_clean", _ctx(tmp_path))
    assert ok is True


def test_imports_clean_false_on_syntax_error(tmp_path):
    (tmp_path / "main.py").write_text(BROKEN_PY)
    ok, _ = _check("imports_parse_clean", _ctx(tmp_path))
    assert ok is False


def test_imports_clean_false_on_review_finding(tmp_path):
    (tmp_path / "main.py").write_text(CLEAN_PY)
    findings = [Finding(file="main.py", line=1, severity="error",
                        category="missing_import", symbol="sqlite3",
                        message="Name 'sqlite3' used but never imported.")]
    ok, _ = _check("imports_parse_clean", _ctx(tmp_path, review_findings=findings))
    assert ok is False


# --------------------------------------------------------------------------- #
# crud_placeholders_match
# --------------------------------------------------------------------------- #
def test_crud_placeholders_match_true(tmp_path):
    (tmp_path / "crud.py").write_text(CRUD_GOOD)
    ok, _ = _check("crud_placeholders_match", _ctx(tmp_path))
    assert ok is True


def test_crud_placeholders_match_false(tmp_path):
    (tmp_path / "crud.py").write_text(CRUD_BAD)
    ok, _ = _check("crud_placeholders_match", _ctx(tmp_path))
    assert ok is False


# --------------------------------------------------------------------------- #
# exports_present
# --------------------------------------------------------------------------- #
def test_exports_present_true(tmp_path):
    (tmp_path / "export.py").write_text("# export\n")
    (tmp_path / "reports.py").write_text("# reports\n")
    ok, _ = _check("exports_present", _ctx(tmp_path))
    assert ok is True


def test_exports_present_false(tmp_path):
    (tmp_path / "export.py").write_text("# export only\n")
    ok, _ = _check("exports_present", _ctx(tmp_path))
    assert ok is False


# --------------------------------------------------------------------------- #
# entry_point_ok
# --------------------------------------------------------------------------- #
def test_entry_point_ok_true(tmp_path):
    (tmp_path / "main.py").write_text(MAIN_WITH_ENTRY)
    ok, _ = _check("entry_point_ok", _ctx(tmp_path))
    assert ok is True


def test_entry_point_ok_false_when_missing(tmp_path):
    ok, _ = _check("entry_point_ok", _ctx(tmp_path))
    assert ok is False
