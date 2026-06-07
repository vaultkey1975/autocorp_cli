"""Phase 7 (Step 4.2, TDD) — dashboard chart integration.

Written FIRST, before DashboardWidget mounts the chart, so these are expected to
fail. The (already-GREEN) ChartWidget should be mounted inside DashboardWidget below
the metrics, via a graceful optional import, and refreshed by DashboardWidget.refresh().
Constructed offscreen against a real SQLite DB. No Ollama.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must precede PySide6 import

import importlib
import sys

import pytest

from PySide6.QtWidgets import QGridLayout

from brains.templates import sqlite_support as ss

CRM = "Build a customer CRM desktop app with SQLite"


@pytest.fixture(scope="session", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _load(tmp_path, schema, with_charts=True):
    """Materialise db/crud/reports + ui/dashboard (and optionally ui/charts), import
    fresh, return (crud, dashboard_module, charts_module_or_None)."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    (tmp_path / "reports.py").write_text(ss.reports_py(schema))
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "__init__.py").write_text("# ui package\n")
    if with_charts:
        (ui / "charts.py").write_text(ss.charts_py(schema))
    (ui / "dashboard.py").write_text(ss.dashboard_py(schema))

    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud", "reports", "ui", "ui.charts", "ui.dashboard"):
        sys.modules.pop(name, None)
    try:
        crud = importlib.import_module("crud")
        charts = importlib.import_module("ui.charts") if with_charts else None
        dashboard = importlib.import_module("ui.dashboard")
    finally:
        sys.path.remove(str(tmp_path))
    return crud, dashboard, charts


# --------------------------------------------------------------------------- #
# 1. Guarded import in the generated dashboard source
# --------------------------------------------------------------------------- #
def test_dashboard_source_imports_chartwidget_gracefully():
    src = ss.dashboard_py(ss.detect_schema(CRM))
    assert "from ui.charts import ChartWidget" in src
    assert "except" in src   # guarded so a missing ui/charts never hard-fails


# --------------------------------------------------------------------------- #
# 2. Chart mounts when ui/charts is available
# --------------------------------------------------------------------------- #
def test_dashboard_mounts_chart_when_charts_available(tmp_path):
    crud, dash, charts = _load(tmp_path, ss.detect_schema(CRM), with_charts=True)
    crud.init_db()
    widget = dash.DashboardWidget()
    assert isinstance(widget.chart, charts.ChartWidget)


# --------------------------------------------------------------------------- #
# 3. DashboardWidget.refresh() propagates to the chart
# --------------------------------------------------------------------------- #
def test_dashboard_refresh_propagates_to_chart(tmp_path):
    crud, dash, _charts = _load(tmp_path, ss.detect_schema(CRM), with_charts=True)
    crud.init_db()
    widget = dash.DashboardWidget()
    crud.add_customer("Ada", "a@x.com", "1")
    widget.refresh()
    assert widget.chart.is_empty is False
    assert widget.chart.bars["Customers"].value() == 100   # 1 of max 1 -> full bar


# --------------------------------------------------------------------------- #
# 4. Graceful build when ui/charts is absent
# --------------------------------------------------------------------------- #
def test_dashboard_builds_without_charts_module(tmp_path):
    crud, dash, _charts = _load(tmp_path, ss.detect_schema(CRM), with_charts=False)
    crud.init_db()
    widget = dash.DashboardWidget()   # must not raise
    assert widget.chart is None


# --------------------------------------------------------------------------- #
# 5. Chart mounted below the metrics (cards grid)
# --------------------------------------------------------------------------- #
def test_chart_mounted_below_metrics(tmp_path):
    crud, dash, _charts = _load(tmp_path, ss.detect_schema(CRM), with_charts=True)
    crud.init_db()
    widget = dash.DashboardWidget()

    root = widget.layout()
    grid_index = None
    chart_index = None
    for i in range(root.count()):
        item = root.itemAt(i)
        if item.layout() is not None and isinstance(item.layout(), QGridLayout):
            grid_index = i
        if item.widget() is widget.chart:
            chart_index = i
    assert grid_index is not None, "cards grid not found in dashboard layout"
    assert chart_index is not None, "chart not mounted in dashboard layout"
    assert chart_index > grid_index   # chart appears after the metrics
