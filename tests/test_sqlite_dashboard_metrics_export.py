"""Phase 7 (Step 4.7, TDD) — dashboard "Export Metrics" button.

Written FIRST, before DashboardWidget gains an Export Metrics button + on_export_metrics
handler, so these are expected to fail. The handler simply calls
reports.export_metrics_csv("metrics.csv") (no dialogs, no save-as, no message boxes,
no new dependency) — the exact parallel to Step 4.5's "Export Summary" button, but
wired to the full-metrics helper from Step 4.6 (counts then averages). Constructed
offscreen; the CSV is isolated via monkeypatch.chdir. No Ollama.
"""

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must precede PySide6 import

import importlib
import sys

import pytest

from brains.templates import sqlite_support as ss

CRM = "Build a customer CRM desktop app with SQLite"
INVENTORY = "a desktop inventory manager with sqlite"


@pytest.fixture(scope="session", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _load(tmp_path, schema):
    """Materialise db/crud/reports + ui/dashboard, import fresh, return
    (crud, reports, dashboard_module)."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    (tmp_path / "reports.py").write_text(ss.reports_py(schema))
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "__init__.py").write_text("# ui package\n")
    (ui / "dashboard.py").write_text(ss.dashboard_py(schema))

    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud", "reports", "ui", "ui.charts", "ui.dashboard"):
        sys.modules.pop(name, None)
    try:
        crud = importlib.import_module("crud")
        reports = importlib.import_module("reports")
        dashboard = importlib.import_module("ui.dashboard")
    finally:
        sys.path.remove(str(tmp_path))
    return crud, reports, dashboard


def _read(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --------------------------------------------------------------------------- #
# 1. Generated source has the button + handler wired to reports export
# --------------------------------------------------------------------------- #
def test_dashboard_source_has_export_metrics_button():
    src = ss.dashboard_py(ss.detect_schema(CRM))
    assert "Export Metrics" in src
    assert "def on_export_metrics" in src
    assert "reports.export_metrics_csv(" in src


# --------------------------------------------------------------------------- #
# 2. Handler writes a correct metrics.csv (counts then averages)
# --------------------------------------------------------------------------- #
def test_dashboard_export_metrics_writes_csv(tmp_path, monkeypatch):
    crud, reports, dash = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_customer("Bob", "b@x.com", "2")          # customers = 2
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_note(a, "n3")                             # notes = 3
    crud.add_interaction(a, "call", "hi")             # interactions = 1

    monkeypatch.chdir(tmp_path)
    widget = dash.DashboardWidget()
    widget.on_export_metrics()

    out = tmp_path / "metrics.csv"
    assert out.exists()
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
# 3. Handler output matches reports.export_metrics_csv round-trip
# --------------------------------------------------------------------------- #
def test_dashboard_export_metrics_matches_reports(tmp_path, monkeypatch):
    crud, reports, dash = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_customer("Bob", "b@x.com", "2")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_interaction(a, "call", "hi")

    reference = tmp_path / "reference.csv"
    reports.export_metrics_csv(str(reference))

    monkeypatch.chdir(tmp_path)
    widget = dash.DashboardWidget()
    widget.on_export_metrics()

    assert _read(tmp_path / "metrics.csv") == _read(reference)


# --------------------------------------------------------------------------- #
# 4. Empty database stays safe (zeros, no crash)
# --------------------------------------------------------------------------- #
def test_dashboard_export_metrics_empty_safe(tmp_path, monkeypatch):
    crud, reports, dash = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()

    monkeypatch.chdir(tmp_path)
    widget = dash.DashboardWidget()
    widget.on_export_metrics()

    out = tmp_path / "metrics.csv"
    assert out.exists()
    data = {r["metric"]: r["value"] for r in _read(out)}
    assert data == {
        "count_customers": "0",
        "count_notes": "0",
        "count_interactions": "0",
        "avg_notes_per_customer": "0",
        "avg_interactions_per_customer": "0",
    }


# --------------------------------------------------------------------------- #
# 5. Single-table schema -> only the count metric (no averages)
# --------------------------------------------------------------------------- #
def test_dashboard_export_metrics_single_table(tmp_path, monkeypatch):
    crud, reports, dash = _load(tmp_path, ss.detect_schema(INVENTORY))
    crud.init_db()

    monkeypatch.chdir(tmp_path)
    widget = dash.DashboardWidget()
    widget.on_export_metrics()

    out = tmp_path / "metrics.csv"
    assert out.exists()
    rows = _read(out)
    assert [r["metric"] for r in rows] == ["count_products"]
