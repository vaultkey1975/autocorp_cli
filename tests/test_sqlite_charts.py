"""Phase 7 (Step 4.1, TDD) — dashboard charts (ui/charts.py).

Written FIRST, before sqlite_support.charts_py / _charts_config exist, so these are
expected to fail. Charts are deterministic and metadata-driven (CHARTS) on top of the
existing reporting layer; the widget renders with core Qt (QProgressBar) - no charting
dependency. The widget is constructed offscreen against a real SQLite DB. No Ollama.
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
    """customers=2, notes=3, interactions=1 -> max=3."""
    a = crud.add_customer("Ada", "a@x.com", "1")
    b = crud.add_customer("Bob", "b@x.com", "2")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_note(a, "n3")
    crud.add_interaction(a, "call", "hi")
    return a, b


# --------------------------------------------------------------------------- #
# 1. Generated source shape + compiles
# --------------------------------------------------------------------------- #
def test_charts_py_source_shape():
    src = ss.charts_py(ss.detect_schema(CRM))
    for token in ("class ChartWidget(QWidget)", "CHARTS = {", "def chart_data",
                  "import reports", "QProgressBar"):
        assert token in src, token
    compile(src, "charts.py", "exec")


# --------------------------------------------------------------------------- #
# 2. Chart config (metadata-driven)
# --------------------------------------------------------------------------- #
def test_charts_config():
    crm = ss._charts_config(ss.detect_schema(CRM))
    assert [bar["label"] for bar in crm["bars"]] == ["Customers", "Notes", "Interactions"]
    inv = ss._charts_config(ss.detect_schema(INVENTORY))
    assert [bar["label"] for bar in inv["bars"]] == ["Products"]


# --------------------------------------------------------------------------- #
# 3. chart_data() values + ratios
# --------------------------------------------------------------------------- #
def test_chart_data_values_and_ratios(tmp_path):
    crud, charts = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    _seed_crm(crud)

    data = charts.chart_data()
    values = {bar["label"]: bar["value"] for bar in data["bars"]}
    ratios = {bar["label"]: bar["ratio"] for bar in data["bars"]}
    assert values == {"Customers": 2, "Notes": 3, "Interactions": 1}
    assert data["max"] == 3
    assert ratios["Notes"] == pytest.approx(1.0)
    assert ratios["Customers"] == pytest.approx(2 / 3)
    assert ratios["Interactions"] == pytest.approx(1 / 3)
    assert data["is_empty"] is False


# --------------------------------------------------------------------------- #
# 4. Empty dataset
# --------------------------------------------------------------------------- #
def test_chart_data_empty(tmp_path):
    crud, charts = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    data = charts.chart_data()
    assert data["is_empty"] is True
    assert all(bar["ratio"] == 0.0 for bar in data["bars"])


# --------------------------------------------------------------------------- #
# 5. ChartWidget (constructed offscreen)
# --------------------------------------------------------------------------- #
def test_chart_widget_offscreen(tmp_path):
    crud, charts = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a, b = _seed_crm(crud)

    widget = charts.ChartWidget()  # __init__ calls refresh()
    assert widget.is_empty is False
    assert widget.bars["Notes"].value() == 100
    assert widget.bars["Customers"].value() == 67   # round(2/3 * 100)
    assert widget.bars["Interactions"].value() == 33  # round(1/3 * 100)
    assert widget.empty_label.isHidden() is True      # hidden when data present

    # Clear everything -> empty state.
    crud.delete_customer(a)
    crud.delete_customer(b)
    widget.refresh()
    assert widget.is_empty is True
    assert all(widget.bars[label].value() == 0 for label in widget.bars)
    assert widget.empty_label.isHidden() is False     # visible when empty


# --------------------------------------------------------------------------- #
# 6. Deterministic ordering (regression guard)
# --------------------------------------------------------------------------- #
def test_chart_data_deterministic_order(tmp_path):
    # Config order is stable and matches schema/table order on every build.
    for _ in range(3):
        cfg = ss._charts_config(ss.detect_schema(CRM))
        assert [bar["label"] for bar in cfg["bars"]] == ["Customers", "Notes", "Interactions"]

    # chart_data() preserves that order too.
    crud, charts = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    _seed_crm(crud)
    data = charts.chart_data()
    assert [bar["label"] for bar in data["bars"]] == ["Customers", "Notes", "Interactions"]
