"""Phase 7 (Step 4.4, TDD) — report summary CSV export.

Written FIRST, before reports.export_summary_csv exists, so these are expected to
fail. The function writes summary() ({table: count}) to a CSV with deterministic
metric,value columns in schema/table order; empty databases stay safe. Pure SQL +
stdlib csv - no Ollama, no Qt.
"""

import csv
import importlib
import sys

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


def _read(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --------------------------------------------------------------------------- #
# 1. Generated source exposes the export helper
# --------------------------------------------------------------------------- #
def test_reports_py_has_export_summary():
    src = ss.reports_py(ss.detect_schema(CRM))
    assert "import csv" in src
    assert "def export_summary_csv" in src


# --------------------------------------------------------------------------- #
# 2. Round-trip: summary written to CSV with correct counts
# --------------------------------------------------------------------------- #
def test_export_summary_csv_round_trip(tmp_path):
    crud, reports = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_customer("Bob", "b@x.com", "2")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_note(a, "n3")
    crud.add_interaction(a, "call", "hi")

    out = tmp_path / "summary.csv"
    returned = reports.export_summary_csv(str(out))
    assert returned == str(out)

    rows = _read(out)
    assert [r["metric"] for r in rows] == ["customers", "notes", "interactions"]
    data = {r["metric"]: r["value"] for r in rows}
    assert data == {"customers": "2", "notes": "3", "interactions": "1"}


# --------------------------------------------------------------------------- #
# 3. Empty database stays safe (all zeros)
# --------------------------------------------------------------------------- #
def test_export_summary_csv_empty(tmp_path):
    crud, reports = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    out = tmp_path / "summary.csv"
    reports.export_summary_csv(str(out))
    data = {r["metric"]: r["value"] for r in _read(out)}
    assert data == {"customers": "0", "notes": "0", "interactions": "0"}


# --------------------------------------------------------------------------- #
# 4. Single-table schema
# --------------------------------------------------------------------------- #
def test_export_summary_single_table(tmp_path):
    crud, reports = _load(tmp_path, ss.detect_schema(INVENTORY))
    crud.init_db()
    out = tmp_path / "summary.csv"
    reports.export_summary_csv(str(out))
    rows = _read(out)
    assert [r["metric"] for r in rows] == ["products"]
