#!/usr/bin/env python3
"""
Reviewer Brain detector + schema tests  (AutoCorp CLI - Phase 8B RED)
====================================================================

Drives the design of `brains/reviewer.py`: a deterministic, offline static
reviewer that inspects generated .py files BEFORE tests run and produces
structured findings plus a 0-100 quality score.

Everything here is fully offline and model-free: the Reviewer's default path is
pure static analysis (Python `ast`), so no engine/Ollama/network is involved. A
recording fake engine proves the Reviewer never calls a model.

RED note (Phase 8B): `brains/reviewer.py` does not exist yet, so every test that
touches it fails. The reviewer class is imported lazily inside the helpers/tests
so each test fails individually with a clear message rather than collapsing into
one collection error.

Focus detectors for this phase: missing_import, large_function, syntax_error,
plus the score model and the Finding/ReviewReport schema. duplicate_code tests
are included as xfail markers (intent documented) so they do NOT expand the 8B
GREEN implementation scope.
"""

import pytest


# --------------------------------------------------------------------------- #
# Helpers (lazy import -> per-test RED failures)
# --------------------------------------------------------------------------- #
class _RecordingEngine:
    """Stand-in engine that records calls, to prove the Reviewer is model-free."""

    name = "recording"

    def __init__(self):
        self.calls = []

    def generate(self, prompt, system=""):
        self.calls.append(prompt)
        return "ENGINE-OUTPUT\n"


def _reviewer(engine=None):
    from brains.reviewer import ReviewerBrain
    return ReviewerBrain(engine=engine)


def _review(tmp_path, files: dict, plan=None):
    """Write `files` (name -> source) into tmp_path and review the workspace."""
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return _reviewer().review(str(tmp_path), plan or {"project_name": "demo"})


def _by_cat(report, category):
    return [f for f in report.findings if f.category == category]


# Source fixtures -----------------------------------------------------------
CLEAN_SRC = "import sqlite3\n\n\ndef handler():\n    return sqlite3.connect('d.db')\n"
MISSING_IMPORT_SRC = "def handler():\n    return sqlite3.connect('d.db')\n"
BUILTIN_SRC = "def f(x):\n    return len(x) + int('3')\n"
STAR_IMPORT_SRC = "from os import *\n\n\ndef f():\n    return getcwd()\n"
SYNTAX_ERROR_SRC = "def broken(:\n    pass\n"
MANY_UNDEFINED_SRC = "def f():\n    return a + b + c + d + e + g + h + j\n"


def _large_function_src(n_lines=60):
    body = "\n".join(f"    x{i} = {i}" for i in range(n_lines))
    return f"def big():\n{body}\n    return x0\n"


def _small_function_src():
    return "def small():\n    return 1\n"


DUP_BLOCK = (
    "    total = 0\n"
    "    for i in range(10):\n"
    "        total += i\n"
    "        total *= 2\n"
    "        total -= 1\n"
    "    return total\n"
)
DUPLICATE_SRC = f"def a():\n{DUP_BLOCK}\n\ndef b():\n{DUP_BLOCK}"


# --------------------------------------------------------------------------- #
# Clean baseline
# --------------------------------------------------------------------------- #
def test_clean_code_has_no_findings(tmp_path):
    report = _review(tmp_path, {"main.py": CLEAN_SRC})
    assert report.findings == []


def test_clean_code_scores_full(tmp_path):
    report = _review(tmp_path, {"main.py": CLEAN_SRC})
    assert report.score == 100


def test_review_returns_report_object(tmp_path):
    from brains.reviewer import ReviewReport
    report = _review(tmp_path, {"main.py": CLEAN_SRC})
    assert isinstance(report, ReviewReport)


# --------------------------------------------------------------------------- #
# missing_import
# --------------------------------------------------------------------------- #
def test_missing_import_detected(tmp_path):
    report = _review(tmp_path, {"main.py": MISSING_IMPORT_SRC})
    assert _by_cat(report, "missing_import")


def test_missing_import_not_flagged_when_imported(tmp_path):
    report = _review(tmp_path, {"main.py": CLEAN_SRC})
    assert _by_cat(report, "missing_import") == []


def test_missing_import_ignores_builtins(tmp_path):
    report = _review(tmp_path, {"main.py": BUILTIN_SRC})
    assert _by_cat(report, "missing_import") == []


def test_missing_import_suppressed_under_star_import(tmp_path):
    report = _review(tmp_path, {"main.py": STAR_IMPORT_SRC})
    assert _by_cat(report, "missing_import") == []


# --------------------------------------------------------------------------- #
# large_function
# --------------------------------------------------------------------------- #
def test_large_function_detected(tmp_path):
    report = _review(tmp_path, {"main.py": _large_function_src(60)})
    assert _by_cat(report, "large_function")


def test_small_function_not_flagged(tmp_path):
    report = _review(tmp_path, {"main.py": _small_function_src()})
    assert _by_cat(report, "large_function") == []


# --------------------------------------------------------------------------- #
# syntax_error  (must not crash)
# --------------------------------------------------------------------------- #
def test_syntax_error_detected_without_crashing(tmp_path):
    report = _review(tmp_path, {"main.py": SYNTAX_ERROR_SRC})
    assert _by_cat(report, "syntax_error")


def test_syntax_error_file_does_not_abort_other_files(tmp_path):
    report = _review(tmp_path, {"bad.py": SYNTAX_ERROR_SRC, "good.py": CLEAN_SRC})
    # The clean file is still counted/reviewed despite the broken sibling.
    assert report.files_reviewed == 2
    assert _by_cat(report, "syntax_error")


# --------------------------------------------------------------------------- #
# Score model
# --------------------------------------------------------------------------- #
def test_score_drops_for_single_error(tmp_path):
    # One missing_import (severity error, weight -15) -> 100 - 15 = 85.
    report = _review(tmp_path, {"main.py": MISSING_IMPORT_SRC})
    assert report.score == 85


def test_score_clamped_at_zero(tmp_path):
    # Eight undefined names -> 8 errors -> well past 100 penalty -> clamped to 0.
    report = _review(tmp_path, {"main.py": MANY_UNDEFINED_SRC})
    assert report.score == 0


def test_score_is_int_in_range(tmp_path):
    report = _review(tmp_path, {"a.py": MISSING_IMPORT_SRC, "b.py": CLEAN_SRC})
    assert isinstance(report.score, int)
    assert 0 <= report.score <= 100


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_findings_are_deterministic(tmp_path):
    files = {"a.py": MISSING_IMPORT_SRC, "b.py": _large_function_src(60)}
    first = [f.to_dict() for f in _review(tmp_path, files).findings]
    second = [f.to_dict() for f in _review(tmp_path, files).findings]
    assert first == second


# --------------------------------------------------------------------------- #
# Finding schema
# --------------------------------------------------------------------------- #
def test_finding_schema_fields(tmp_path):
    report = _review(tmp_path, {"main.py": MISSING_IMPORT_SRC})
    d = report.findings[0].to_dict()
    for key in ("file", "line", "severity", "category", "symbol", "message", "source"):
        assert key in d


def test_finding_source_is_static(tmp_path):
    report = _review(tmp_path, {"main.py": MISSING_IMPORT_SRC})
    assert report.findings[0].to_dict()["source"] == "static"


# --------------------------------------------------------------------------- #
# ReviewReport schema
# --------------------------------------------------------------------------- #
def test_reviewreport_schema_fields(tmp_path):
    d = _review(tmp_path, {"main.py": MISSING_IMPORT_SRC}).to_dict()
    for key in ("project_name", "workspace", "ts", "files_reviewed",
                "score", "findings", "summary"):
        assert key in d
    assert isinstance(d["findings"], list)
    assert all(isinstance(f, dict) for f in d["findings"])


def test_files_reviewed_counts_only_nonempty_python(tmp_path):
    report = _review(tmp_path, {
        "good.py": CLEAN_SRC,
        "notes.txt": "not python at all",
        "empty.py": "",
    })
    assert report.files_reviewed == 1


# --------------------------------------------------------------------------- #
# Non-destructive + model-free
# --------------------------------------------------------------------------- #
def test_review_is_nondestructive(tmp_path):
    (tmp_path / "main.py").write_text(MISSING_IMPORT_SRC)
    _reviewer().review(str(tmp_path), {"project_name": "demo"})
    assert (tmp_path / "main.py").read_text() == MISSING_IMPORT_SRC


def test_review_does_not_call_engine(tmp_path):
    engine = _RecordingEngine()
    (tmp_path / "main.py").write_text(CLEAN_SRC)
    _reviewer(engine=engine).review(str(tmp_path), {"project_name": "demo"})
    assert engine.calls == []


# --------------------------------------------------------------------------- #
# duplicate_code (OPTIONAL, deferred) - xfail so it does not expand 8B GREEN
# --------------------------------------------------------------------------- #
@pytest.mark.xfail(reason="duplicate_code detector deferred beyond Phase 8B GREEN",
                   strict=False)
def test_duplicate_code_detected(tmp_path):
    report = _review(tmp_path, {"main.py": DUPLICATE_SRC})
    assert _by_cat(report, "duplicate_code")


@pytest.mark.xfail(reason="duplicate_code detector deferred beyond Phase 8B GREEN",
                   strict=False)
def test_unique_code_has_no_duplicate_findings(tmp_path):
    report = _review(tmp_path, {"main.py": CLEAN_SRC})
    assert _by_cat(report, "duplicate_code") == []
