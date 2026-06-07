"""Tests for the SQLite desktop template + its routing (brains/templates).

No Ollama needed: these check template selection, the generated plan shape, the
dependency-safe build order, that the deterministic data + UI + reporting code is
embedded as exact `content`, and that the model-generated ui/main_window.py is a
thin re-export of the AppWindow shell (Phase 7).
"""

from brains.templates import select_template
from brains.templates import sqlite_desktop as sql
from brains.project_plan import ProjectPlan

SQLITE_FILES = {
    "requirements.txt", "database.py", "crud.py", "export.py", "reports.py",
    "ui/__init__.py", "ui/widgets.py", "ui/master_detail.py", "ui/charts.py",
    "ui/dashboard.py", "ui/app_window.py", "ui/main_window.py", "main.py",
}
CRM_REQUEST = "Build a customer CRM desktop app with SQLite"


def _purpose(plan, path):
    return next(f["purpose"] for f in plan["files"] if f["path"] == path)


def _content(plan, path):
    return next(f.get("content", "") for f in plan["files"] if f["path"] == path)


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def test_sqlite_template_wins_for_db_desktop_requests():
    assert select_template(CRM_REQUEST).NAME == "sqlite_desktop"
    assert select_template("a GUI inventory manager with a database").NAME == "sqlite_desktop"


def test_plain_desktop_requests_still_go_to_pyside():
    assert select_template("build a desktop calculator").NAME == "pyside6_desktop"
    assert select_template("a GUI todo app").NAME == "pyside6_desktop"


def test_non_app_requests_match_no_template():
    assert select_template("a command-line argument parser") is None
    assert select_template("a sqlite library with no ui") is None


def test_matches_requires_both_data_and_gui():
    assert sql.matches(CRM_REQUEST) is True
    assert sql.matches("a database gui") is True
    assert sql.matches("a sqlite report library") is False
    assert sql.matches("build a desktop calculator") is False


# --------------------------------------------------------------------------- #
# Plan shape / build order
# --------------------------------------------------------------------------- #
def test_build_plan_has_all_files():
    plan = sql.build_plan(CRM_REQUEST)
    assert {f["path"] for f in plan["files"]} == SQLITE_FILES
    assert plan["project_name"] == "customer_crm_app"
    assert plan["test_command"] == "QT_QPA_PLATFORM=offscreen python main.py"


def test_build_order_is_dependency_safe():
    order = sql.build_plan(CRM_REQUEST)["build_order"]
    assert order == [
        "requirements.txt", "database.py", "crud.py", "export.py", "reports.py",
        "ui/__init__.py", "ui/widgets.py", "ui/master_detail.py", "ui/charts.py",
        "ui/dashboard.py", "ui/app_window.py", "ui/main_window.py", "main.py",
    ]
    assert order.index("database.py") < order.index("crud.py")
    assert order.index("crud.py") < order.index("export.py")
    assert order.index("database.py") < order.index("reports.py")
    assert order.index("reports.py") < order.index("ui/charts.py")
    assert order.index("ui/charts.py") < order.index("ui/dashboard.py")
    assert order.index("reports.py") < order.index("ui/dashboard.py")
    assert order.index("ui/widgets.py") < order.index("ui/master_detail.py")
    assert order.index("ui/master_detail.py") < order.index("ui/app_window.py")
    assert order.index("ui/dashboard.py") < order.index("ui/app_window.py")
    assert order.index("ui/app_window.py") < order.index("ui/main_window.py")
    assert order.index("ui/main_window.py") < order.index("main.py")


def test_plan_is_a_valid_project_plan():
    assert ProjectPlan.from_dict(sql.build_plan(CRM_REQUEST)).is_valid


def test_charts_file_embedded():
    charts = _content(sql.build_plan(CRM_REQUEST), "ui/charts.py")
    assert "CHARTS = {" in charts
    assert "class ChartWidget(QWidget)" in charts


# --------------------------------------------------------------------------- #
# Deterministic code embedded as exact content
# --------------------------------------------------------------------------- #
def test_data_layer_code_embedded_as_content():
    plan = sql.build_plan(CRM_REQUEST)
    db = _content(plan, "database.py")
    crud = _content(plan, "crud.py")
    export = _content(plan, "export.py")
    assert "CREATE TABLE IF NOT EXISTS customers" in db
    assert "def add_customer" in crud and "VALUES (?, ?, ?)" in crud
    assert "def get_customers_with_counts" in crud
    assert "def export_customers_with_counts_csv(path)" in export


def test_reporting_and_dashboard_embedded_as_content():
    plan = sql.build_plan(CRM_REQUEST)
    reports = _content(plan, "reports.py")
    dashboard = _content(plan, "ui/dashboard.py")
    app_window = _content(plan, "ui/app_window.py")
    assert "def count_customers(" in reports
    assert "def summary(" in reports
    assert "class DashboardWidget(QWidget)" in dashboard
    assert "DASHBOARD = {" in dashboard
    assert "class AppWindow(QMainWindow)" in app_window
    assert "ManageWidget" in app_window


def test_ui_framework_embedded_as_content():
    plan = sql.build_plan(CRM_REQUEST)
    widgets = _content(plan, "ui/widgets.py")
    md = _content(plan, "ui/master_detail.py")
    assert "def populate_table" in widgets
    assert "class MasterDetailWindow(QMainWindow)" in md
    assert "CONFIG = {" in md
    assert "from ui import widgets" in md


def test_main_py_content_has_offscreen_guard():
    main_py = _content(sql.build_plan(CRM_REQUEST), "main.py")
    for token in ("import sys", "import os", "QApplication", "QTimer",
                  "QT_QPA_PLATFORM", "singleShot"):
        assert token in main_py, token


def test_main_window_is_thin_model_generated_reexport():
    plan = sql.build_plan(CRM_REQUEST)
    # Still the only model-generated file (no exact content)...
    assert _content(plan, "ui/main_window.py") == ""
    purpose = _purpose(plan, "ui/main_window.py")
    assert "from ui.app_window import AppWindow as MainWindow" in purpose
    # ...and the purpose stays tiny.
    assert len(purpose) < 600


def test_entity_detection_drives_schema_for_inventory():
    plan = sql.build_plan("a desktop inventory manager with sqlite")
    db = _content(plan, "database.py")
    crud = _content(plan, "crud.py")
    assert "products" in db
    assert "def add_product" in crud
