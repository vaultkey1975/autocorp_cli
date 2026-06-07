"""Phase 7 (Step 4.0, TDD) — dashboard polish.

Written FIRST, before the polish exists in sqlite_support, so these are expected to
fail. They cover: a format_value() helper in the generated dashboard, card `kind`
metadata + a top-level `primary_metric`, human-friendly labels for underscored table
names, empty-state handling, and consistent average rounding. Offscreen construction
against a real SQLite DB. No Ollama.
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
# 1. format_value helper
# --------------------------------------------------------------------------- #
def test_format_value_counts_and_averages(tmp_path):
    _crud, dash = _load(tmp_path, ss.detect_schema(CRM))
    assert dash.format_value("count", 1234) == "1,234"
    assert dash.format_value("count", 0) == "0"
    assert dash.format_value("average", 1.5) == "1.5"
    assert dash.format_value("average", 1 / 3) == "0.3"
    assert dash.format_value("average", 0) == "0.0"


# --------------------------------------------------------------------------- #
# 2. card kind metadata
# --------------------------------------------------------------------------- #
def test_cards_carry_kind():
    cfg = ss._dashboard_config(ss.detect_schema(CRM))
    kinds = {c["metric_fn"]: c["kind"] for c in cfg["cards"]}
    assert kinds["count_customers"] == "count"
    assert kinds["count_notes"] == "count"
    assert kinds["avg_notes_per_customer"] == "average"
    assert kinds["avg_interactions_per_customer"] == "average"


# --------------------------------------------------------------------------- #
# 3. primary_metric
# --------------------------------------------------------------------------- #
def test_config_has_primary_metric():
    cfg = ss._dashboard_config(ss.detect_schema(CRM))
    assert cfg["primary_metric"] == "count_customers"


# --------------------------------------------------------------------------- #
# 4. human-friendly labels for underscored table names
# --------------------------------------------------------------------------- #
def test_human_friendly_label_for_underscored_table():
    schema = [ss.Table("order_items", [("name", "TEXT")])]
    cfg = ss._dashboard_config(schema)
    assert cfg["cards"][0]["label"] == "Total Order Items"


# --------------------------------------------------------------------------- #
# 5. empty-state handling
# --------------------------------------------------------------------------- #
def test_empty_state_offscreen(tmp_path):
    crud, dash = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    widget = dash.DashboardWidget()  # empty DB
    assert widget.is_empty is True
    assert "No data" in widget.empty_label.text()
    assert widget._values["count_customers"].text() == "0"
    assert widget._values["avg_notes_per_customer"].text() == "0.0"

    crud.add_customer("Ada", "a@x.com", "1")
    widget.refresh()
    assert widget.is_empty is False


# --------------------------------------------------------------------------- #
# 6. consistent average rounding
# --------------------------------------------------------------------------- #
def test_average_rounding_offscreen(tmp_path):
    crud, dash = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_customer("Bob", "b@x.com", "2")
    crud.add_customer("Cy", "c@x.com", "3")
    crud.add_note(a, "n1")  # 1 note / 3 customers = 0.333...

    widget = dash.DashboardWidget()
    assert widget._values["avg_notes_per_customer"].text() == "0.3"
