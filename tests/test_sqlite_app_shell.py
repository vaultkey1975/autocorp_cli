"""Phase 7 (Step 3.1, TDD) — tabbed application shell (ui/app_window.py).

Written FIRST, before sqlite_support.app_window_py exists, so these are expected to
fail. Architecture (option b): a thin ManageWidget(QWidget) wraps the existing
MasterDetailWindow's central panel for the "Manage Data" tab, so a QMainWindow is
never placed inside the QTabWidget. AppWindow(QMainWindow) hosts Dashboard +
Manage Data tabs. Constructed offscreen against a real SQLite DB. No Ollama.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must precede PySide6 import

import importlib
import sys

import pytest

from PySide6.QtWidgets import QMainWindow, QWidget, QTabWidget

from brains.templates import sqlite_support as ss

CRM = "Build a customer CRM desktop app with SQLite"
INVENTORY = "a desktop inventory manager with sqlite"


@pytest.fixture(scope="session", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _load(tmp_path, schema):
    """Materialise the full generated project (data + ui package incl. app_window),
    import fresh, and return (crud_module, app_window_module)."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    (tmp_path / "export.py").write_text(ss.export_py(schema))
    (tmp_path / "reports.py").write_text(ss.reports_py(schema))
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "__init__.py").write_text("# ui package\n")
    (ui / "widgets.py").write_text(ss.widgets_py())
    (ui / "master_detail.py").write_text(ss.master_detail_py(schema))
    (ui / "dashboard.py").write_text(ss.dashboard_py(schema))
    (ui / "app_window.py").write_text(ss.app_window_py(schema))

    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud", "export", "reports", "ui", "ui.widgets",
                 "ui.master_detail", "ui.dashboard", "ui.app_window"):
        sys.modules.pop(name, None)
    try:
        crud = importlib.import_module("crud")
        app_window = importlib.import_module("ui.app_window")
    finally:
        sys.path.remove(str(tmp_path))
    return crud, app_window


def _tabs(window):
    central = window.centralWidget()
    assert isinstance(central, QTabWidget)
    return central


# --------------------------------------------------------------------------- #
# Source shape + compiles
# --------------------------------------------------------------------------- #
def test_app_window_py_compiles():
    compile(ss.app_window_py(ss.detect_schema(CRM)), "app_window.py", "exec")


def test_app_window_py_shape():
    src = ss.app_window_py(ss.detect_schema(CRM))
    for token in ("class AppWindow(QMainWindow)", "QTabWidget",
                  "DashboardWidget", "ManageWidget"):
        assert token in src, token


# --------------------------------------------------------------------------- #
# Constructed offscreen (CRM)
# --------------------------------------------------------------------------- #
def test_app_window_has_exactly_two_named_tabs(tmp_path):
    crud, app_mod = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    win = app_mod.AppWindow()
    tabs = _tabs(win)
    assert tabs.count() == 2
    assert [tabs.tabText(i) for i in range(2)] == ["Dashboard", "Manage Data"]


def test_manage_tab_is_qwidget_not_qmainwindow(tmp_path):
    crud, app_mod = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    win = app_mod.AppWindow()
    tabs = _tabs(win)
    manage = tabs.widget(1)
    assert isinstance(manage, QWidget)
    assert not isinstance(manage, QMainWindow)


def test_dashboard_tab_contains_dashboard_widget(tmp_path):
    crud, app_mod = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    win = app_mod.AppWindow()
    tabs = _tabs(win)
    dashboard_cls = sys.modules["ui.dashboard"].DashboardWidget
    assert isinstance(tabs.widget(0), dashboard_cls)


def test_manage_tab_hosts_master_detail_ui(tmp_path):
    crud, app_mod = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    crud.add_customer("Ada", "a@x.com", "1")
    win = app_mod.AppWindow()
    manage = _tabs(win).widget(1)
    master_cls = sys.modules["ui.master_detail"].MasterDetailWindow
    # The wrapper exposes the MasterDetailWindow it hosts; its master table works.
    assert hasattr(manage, "master")
    assert isinstance(manage.master, master_cls)
    assert manage.master.table.rowCount() == 1


def test_switch_to_dashboard_tab_refreshes(tmp_path):
    crud, app_mod = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    win = app_mod.AppWindow()
    dash = _tabs(win).widget(0)
    assert dash._values["count_customers"].text() == "0"

    # Move to Manage, add a customer, then activate Dashboard -> it must refresh.
    _tabs(win).setCurrentIndex(1)
    crud.add_customer("Bob", "b@x.com", "2")
    _tabs(win).setCurrentIndex(0)
    assert dash._values["count_customers"].text() == "1"


# --------------------------------------------------------------------------- #
# Single-table (inventory) schema
# --------------------------------------------------------------------------- #
def test_inventory_shell_constructs(tmp_path):
    crud, app_mod = _load(tmp_path, ss.detect_schema(INVENTORY))
    crud.init_db()
    win = app_mod.AppWindow()
    tabs = _tabs(win)
    assert tabs.count() == 2
    assert [tabs.tabText(i) for i in range(2)] == ["Dashboard", "Manage Data"]
    assert not isinstance(tabs.widget(1), QMainWindow)
