"""Phase 7 (Step 4.5, TDD) — dashboard "Export Summary" button.

Written FIRST, before DashboardWidget gains an Export Summary button + on_export_summary
handler, so these are expected to fail. The handler simply calls
reports.export_summary_csv("summary.csv") (no dialogs, no save-as, no message boxes,
no new dependency). Constructed offscreen; the CSV is isolated via monkeypatch.chdir.
No Ollama.
"""

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must precede PySide6 import

import importlib
import sys

import pytest

from brains.templates import sqlite_support as ss

CRM = "Build a customer CRM desktop app with SQLite"


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
def test_dashboard_source_has_export_summary_button():
    src = ss.dashboard_py(ss.detect_schema(CRM))
    assert "Export Summary" in src
    assert "def on_export_summary" in src
    assert "reports.export_summary_csv(" in src


# --------------------------------------------------------------------------- #
# 2. Handler writes a correct summary.csv
# --------------------------------------------------------------------------- #
def test_dashboard_export_summary_writes_csv(tmp_path, monkeypatch):
    crud, reports, dash = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_customer("Bob", "b@x.com", "2")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_note(a, "n3")
    crud.add_interaction(a, "call", "hi")

    monkeypatch.chdir(tmp_path)
    widget = dash.DashboardWidget()
    widget.on_export_summary()

    out = tmp_path / "summary.csv"
    assert out.exists()
    rows = _read(out)
    assert list(rows[0].keys()) == ["metric", "value"]
    data = {r["metric"]: int(r["value"]) for r in rows}
    assert data == reports.summary() == {"customers": 2, "notes": 3, "interactions": 1}


# --------------------------------------------------------------------------- #
# 3. Empty database stays safe (zeros, no crash)
# --------------------------------------------------------------------------- #
def test_dashboard_export_summary_empty_safe(tmp_path, monkeypatch):
    crud, reports, dash = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()

    monkeypatch.chdir(tmp_path)
    widget = dash.DashboardWidget()
    widget.on_export_summary()

    out = tmp_path / "summary.csv"
    assert out.exists()
    data = {r["metric"]: int(r["value"]) for r in _read(out)}
    assert data == {"customers": 0, "notes": 0, "interactions": 0}
