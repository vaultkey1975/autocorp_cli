"""Phase 7 (Step 1, TDD) — deterministic reporting layer (reports.py).

Written FIRST, before sqlite_support.reports_py exists, so these are expected to
fail. They generate database.py + crud.py + reports.py into a temp dir, import
them, and exercise the analytics functions against a real SQLite DB. No Ollama,
no Qt needed.
"""

import importlib
import sys

import pytest

from brains.templates import sqlite_support as ss

CRM = "Build a customer CRM desktop app with SQLite"
INVENTORY = "a desktop inventory manager with sqlite"


def _load(tmp_path, schema):
    """Write generated database.py + crud.py + reports.py, import fresh, return
    (crud_module, reports_module)."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    (tmp_path / "reports.py").write_text(ss.reports_py(schema))
    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud", "reports"):
        sys.modules.pop(name, None)
    try:
        crud = importlib.import_module("crud")
        reports = importlib.import_module("reports")
    finally:
        sys.path.remove(str(tmp_path))
    return crud, reports


# --------------------------------------------------------------------------- #
# Generated source shape + compiles
# --------------------------------------------------------------------------- #
def test_reports_py_source_shape():
    src = ss.reports_py(ss.detect_schema(CRM))
    for fn in (
        "def count_customers(",
        "def count_notes(",
        "def count_interactions(",
        "def avg_notes_per_customer(",
        "def avg_interactions_per_customer(",
        "def summary(",
    ):
        assert fn in src, fn
    assert "from database import get_connection" in src
    compile(src, "reports.py", "exec")


def test_reports_single_table_has_counts_but_no_averages():
    src = ss.reports_py(ss.detect_schema(INVENTORY))
    assert "def count_products(" in src
    assert "avg_" not in src
    assert "_per_" not in src


# --------------------------------------------------------------------------- #
# Generated code actually works
# --------------------------------------------------------------------------- #
def test_counts_and_averages(tmp_path):
    crud, reports = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_customer("Bob", "b@x.com", "2")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_note(a, "n3")
    crud.add_interaction(a, "call", "hi")

    assert reports.count_customers() == 2
    assert reports.count_notes() == 3
    assert reports.count_interactions() == 1
    assert reports.avg_notes_per_customer() == pytest.approx(1.5)        # 3 / 2
    assert reports.avg_interactions_per_customer() == pytest.approx(0.5)  # 1 / 2


def test_average_is_zero_with_no_parents(tmp_path):
    crud, reports = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    assert reports.count_customers() == 0
    assert reports.avg_notes_per_customer() == 0  # no divide-by-zero


def test_summary_returns_counts_per_table(tmp_path):
    crud, reports = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_interaction(a, "call", "hi")

    assert reports.summary() == {"customers": 1, "notes": 2, "interactions": 1}
