"""Phase 7 (Step 2, TDD) — deterministic dashboard widget (ui/dashboard.py).

Written FIRST, before sqlite_support._dashboard_config / dashboard_py exist. The
DashboardWidget is generic and config-driven; it is constructed offscreen against a
real SQLite DB and its summary-card values are asserted. No Ollama needed.
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
    """Materialise db/crud/reports + ui/dashboard, import fresh, return (crud, dash)."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    (tmp_path / "reports.py").write_text(ss.reports_py(schema))
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "__init__.py").write_text("# ui package\n")
    (ui / "dashboard.py").write_text(ss.dashboard_py(schema))

    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud", "reports", "ui", "ui.dashboard"):
        sys.modules.pop(name, None)
    try:
        crud = importlib.import_module("crud")
        dash = importlib.import_module("ui.dashboard")
    finally:
        sys.path.remove(str(tmp_path))
    return crud, dash


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_dashboard_config_for_crm():
    cfg = ss._dashboard_config(ss.detect_schema(CRM))
    assert cfg["title"] == "Customers"
    metric_fns = [c["metric_fn"] for c in cfg["cards"]]
    assert metric_fns == [
        "count_customers", "count_notes", "count_interactions",
        "avg_notes_per_customer", "avg_interactions_per_customer",
    ]
    labels = [c["label"] for c in cfg["cards"]]
    assert labels[0] == "Total Customers"
    assert "Avg Notes per Customer" in labels


def test_dashboard_config_single_table_has_only_counts():
    cfg = ss._dashboard_config(ss.detect_schema(INVENTORY))
    assert [c["metric_fn"] for c in cfg["cards"]] == ["count_products"]


# --------------------------------------------------------------------------- #
# Source shape + compiles
# --------------------------------------------------------------------------- #
def test_dashboard_py_shape():
    src = ss.dashboard_py(ss.detect_schema(CRM))
    assert "class DashboardWidget(QWidget)" in src
    assert "DASHBOARD = {" in src
    assert "import reports" in src
    assert "def refresh(self)" in src
    compile(src, "dashboard.py", "exec")


# --------------------------------------------------------------------------- #
# Widget behaviour (constructed offscreen)
# --------------------------------------------------------------------------- #
def test_dashboard_widget_shows_live_values(tmp_path):
    crud, dash = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_customer("Bob", "b@x.com", "2")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_note(a, "n3")
    crud.add_interaction(a, "call", "hi")

    widget = dash.DashboardWidget()  # __init__ calls refresh()
    assert widget._values["count_customers"].text() == "2"
    assert widget._values["count_notes"].text() == "3"
    assert widget._values["count_interactions"].text() == "1"
    assert widget._values["avg_notes_per_customer"].text() == "1.5"


def test_dashboard_refresh_reflects_changes(tmp_path):
    crud, dash = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    crud.add_customer("Ada", "a@x.com", "1")
    widget = dash.DashboardWidget()
    assert widget._values["count_customers"].text() == "1"

    crud.add_customer("Bob", "b@x.com", "2")
    widget.refresh()
    assert widget._values["count_customers"].text() == "2"
