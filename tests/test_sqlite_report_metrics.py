"""Phase 7 (Step 4.6, TDD) — full metrics CSV export.

Written FIRST, before reports.export_metrics_csv exists, so these are expected to
fail. The function writes ALL dashboard metrics (counts first in schema order, then
averages in FK order) to a CSV with metric,value columns; returns the path; empty
databases stay safe. Pure SQL + stdlib csv - no Ollama, no Qt.
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
def test_reports_py_has_export_metrics():
    src = ss.reports_py(ss.detect_schema(CRM))
    assert "def export_metrics_csv" in src


# --------------------------------------------------------------------------- #
# 2. Round-trip: counts first (schema order), averages second (FK order)
# --------------------------------------------------------------------------- #
def test_export_metrics_round_trip(tmp_path):
    crud, reports = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_customer("Bob", "b@x.com", "2")          # customers = 2
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_note(a, "n3")                             # notes = 3
    crud.add_interaction(a, "call", "hi")             # interactions = 1

    out = tmp_path / "metrics.csv"
    returned = reports.export_metrics_csv(str(out))
    assert returned == str(out)

    rows = _read(out)
    assert list(rows[0].keys()) == ["metric", "value"]
    assert [r["metric"] for r in rows] == [
        "count_customers", "count_notes", "count_interactions",
        "avg_notes_per_customer", "avg_interactions_per_customer",
    ]
    data = {r["metric"]: r["value"] for r in rows}
    assert data == {
        "count_customers": "2",
        "count_notes": "3",
        "count_interactions": "1",
        "avg_notes_per_customer": "1.5",          # 3 / 2
        "avg_interactions_per_customer": "0.5",   # 1 / 2
    }


# --------------------------------------------------------------------------- #
# 3. Empty database stays safe (counts and averages all 0)
# --------------------------------------------------------------------------- #
def test_export_metrics_empty(tmp_path):
    crud, reports = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    out = tmp_path / "metrics.csv"
    reports.export_metrics_csv(str(out))
    data = {r["metric"]: r["value"] for r in _read(out)}
    assert data == {
        "count_customers": "0",
        "count_notes": "0",
        "count_interactions": "0",
        "avg_notes_per_customer": "0",
        "avg_interactions_per_customer": "0",
    }


# --------------------------------------------------------------------------- #
# 4. Single-table schema -> only the count metric (no averages)
# --------------------------------------------------------------------------- #
def test_export_metrics_single_table(tmp_path):
    crud, reports = _load(tmp_path, ss.detect_schema(INVENTORY))
    crud.init_db()
    out = tmp_path / "metrics.csv"
    reports.export_metrics_csv(str(out))
    rows = _read(out)
    assert [r["metric"] for r in rows] == ["count_products"]
