"""Phase 7 (Step 4.3, TDD) — chart total summary.

Written FIRST, before chart_data() returns a total and ChartWidget exposes a
total_label, so these are expected to fail. The total is the sum of the bar values;
the footer shows a grouped integer ("Total: 0", "Total: 6", "Total: 1,234"). Empty
databases stay safe. Constructed offscreen against a real SQLite DB. No Ollama.
"""

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
    """Materialise db/crud/reports + ui/charts, import fresh, return (crud, charts)."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    (tmp_path / "reports.py").write_text(ss.reports_py(schema))
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "__init__.py").write_text("# ui package\n")
    (ui / "charts.py").write_text(ss.charts_py(schema))

    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud", "reports", "ui", "ui.charts"):
        sys.modules.pop(name, None)
    try:
        crud = importlib.import_module("crud")
        charts = importlib.import_module("ui.charts")
    finally:
        sys.path.remove(str(tmp_path))
    return crud, charts


def _seed_six(crud):
    """customers=2, notes=3, interactions=1 -> total = 6."""
    a = crud.add_customer("Ada", "a@x.com", "1")
    b = crud.add_customer("Bob", "b@x.com", "2")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_note(a, "n3")
    crud.add_interaction(a, "call", "hi")
    return a, b


# --------------------------------------------------------------------------- #
# 1. chart_data() exposes total
# --------------------------------------------------------------------------- #
def test_chart_data_has_total(tmp_path):
    crud, charts = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()

    assert charts.chart_data()["total"] == 0          # empty DB stays safe
    _seed_six(crud)
    assert charts.chart_data()["total"] == 6          # 2 + 3 + 1


# --------------------------------------------------------------------------- #
# 2. ChartWidget footer label (0 and 6)
# --------------------------------------------------------------------------- #
def test_chart_widget_total_label_offscreen(tmp_path):
    crud, charts = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a, b = _seed_six(crud)

    widget = charts.ChartWidget()  # __init__ calls refresh()
    assert widget.total_label.text() == "Total: 6"

    crud.delete_customer(a)        # cascades notes/interactions
    crud.delete_customer(b)
    widget.refresh()
    assert widget.total_label.text() == "Total: 0"


# --------------------------------------------------------------------------- #
# 3. Grouped-thousands formatting (1,234)
# --------------------------------------------------------------------------- #
def test_chart_total_grouped_thousands(tmp_path):
    crud, charts = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    database = sys.modules["database"]
    with database.get_connection() as conn:
        conn.executemany(
            "INSERT INTO customers (name, email, phone) VALUES (?, ?, ?)",
            [("n", "e", "p")] * 1234,
        )

    widget = charts.ChartWidget()
    assert widget.total_label.text() == "Total: 1,234"
