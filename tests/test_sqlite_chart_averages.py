"""Phase 7 (Step 4.9, TDD) — chart symmetry: visualize FK averages.

Written FIRST, before _charts_config emits an "averages" group and ChartWidget
renders it, so these are expected to fail. The count group (bars / title / ratios)
stays byte-identical to today; averages are a NEW, independently-normalized bar
group (ratio within the averages-group max). Single-table schemas (no foreign keys)
have no averages — mirroring export_metrics_csv. Constructed offscreen against a
real SQLite DB. No Ollama.
"""

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


def _seed_crm(crud):
    """customers=2, notes=3, interactions=1 ->
    avg notes/customer = 1.5, avg interactions/customer = 0.5."""
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_customer("Bob", "b@x.com", "2")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_note(a, "n3")
    crud.add_interaction(a, "call", "hi")
    return a


# --------------------------------------------------------------------------- #
# 1. Config exposes a new "averages" list (FK order); counts unchanged
# --------------------------------------------------------------------------- #
def test_charts_config_has_averages_crm():
    cfg = ss._charts_config(ss.detect_schema(CRM))
    # regression guards — count group unchanged
    assert cfg["title"] == "Records by Table"
    assert [b["label"] for b in cfg["bars"]] == ["Customers", "Notes", "Interactions"]
    # new — averages group mirrors dashboard average cards + CSV metric order
    assert [a["metric_fn"] for a in cfg["averages"]] == [
        "avg_notes_per_customer",
        "avg_interactions_per_customer",
    ]
    assert [a["label"] for a in cfg["averages"]] == [
        "Avg Notes per Customer",
        "Avg Interactions per Customer",
    ]


# --------------------------------------------------------------------------- #
# 2. Single-table schema -> no averages (mirrors export_metrics single-table)
# --------------------------------------------------------------------------- #
def test_charts_config_no_averages_single_table():
    cfg = ss._charts_config(ss.detect_schema(INVENTORY))
    assert [b["label"] for b in cfg["bars"]] == ["Products"]   # unchanged
    assert cfg["averages"] == []


# --------------------------------------------------------------------------- #
# 3. Generated source keeps chart structure + references the averages group
# --------------------------------------------------------------------------- #
def test_charts_py_source_renders_averages():
    src = ss.charts_py(ss.detect_schema(CRM))
    assert "class ChartWidget(QWidget)" in src
    assert "CHARTS = {" in src
    assert "bars" in src
    assert "averages" in src


# --------------------------------------------------------------------------- #
# 4. chart_data(): counts unchanged + averages normalized within their own max
# --------------------------------------------------------------------------- #
def test_chart_data_averages_normalized(tmp_path):
    crud, charts = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    _seed_crm(crud)

    data = charts.chart_data()
    # count group unchanged (regression guard)
    counts = {b["label"]: b["value"] for b in data["bars"]}
    assert counts == {"Customers": 2, "Notes": 3, "Interactions": 1}
    # averages group: independent normalization (max_avg = 1.5)
    avgs = {a["label"]: (a["value"], round(a["ratio"], 2)) for a in data["averages"]}
    assert avgs == {
        "Avg Notes per Customer": (1.5, 1.0),
        "Avg Interactions per Customer": (0.5, 0.33),
    }


# --------------------------------------------------------------------------- #
# 5. Empty DB: averages all zero, ratio 0.0, no crash
# --------------------------------------------------------------------------- #
def test_chart_widget_empty_averages_safe(tmp_path):
    crud, charts = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()

    data = charts.chart_data()
    assert "averages" in data
    assert all(a["value"] == 0 for a in data["averages"])
    assert all(a["ratio"] == 0.0 for a in data["averages"])

    widget = charts.ChartWidget()
    widget.refresh()   # constructs + refreshes without raising


# --------------------------------------------------------------------------- #
# 6. Single-table schema: no average bars, no crash
# --------------------------------------------------------------------------- #
def test_chart_widget_single_table_no_average_bars(tmp_path):
    crud, charts = _load(tmp_path, ss.detect_schema(INVENTORY))
    crud.init_db()

    assert charts.chart_data()["averages"] == []
    charts.ChartWidget().refresh()   # no average rows, no crash
