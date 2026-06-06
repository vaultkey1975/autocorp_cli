"""Tests for the SQLite desktop template + its routing (brains/templates).

No Ollama needed: these check template selection, the generated plan shape, the
dependency-safe build order, and that the deterministic DB/CRUD/main code is
embedded as exact `content` (written verbatim by the Builder) while the UI is
left to the engine via a crud-wired purpose.
"""

from brains.templates import select_template
from brains.templates import sqlite_desktop as sql
from brains.project_plan import ProjectPlan

SQLITE_FILES = {
    "requirements.txt", "database.py", "crud.py",
    "ui/__init__.py", "ui/main_window.py", "main.py",
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
    # Regression: no data keyword -> the pure-GUI template must keep these.
    assert select_template("build a desktop calculator").NAME == "pyside6_desktop"
    assert select_template("a GUI todo app").NAME == "pyside6_desktop"


def test_non_app_requests_match_no_template():
    assert select_template("a command-line argument parser") is None
    assert select_template("a sqlite library with no ui") is None  # data but no GUI


def test_matches_requires_both_data_and_gui():
    assert sql.matches(CRM_REQUEST) is True
    assert sql.matches("a database gui") is True
    assert sql.matches("a sqlite report library") is False   # no GUI signal
    assert sql.matches("build a desktop calculator") is False  # no data signal


# --------------------------------------------------------------------------- #
# Plan shape / build order
# --------------------------------------------------------------------------- #
def test_build_plan_has_all_six_files():
    plan = sql.build_plan(CRM_REQUEST)
    assert {f["path"] for f in plan["files"]} == SQLITE_FILES
    assert plan["project_name"] == "customer_crm_app"
    assert plan["test_command"] == "QT_QPA_PLATFORM=offscreen python main.py"


def test_build_order_is_dependency_safe():
    order = sql.build_plan(CRM_REQUEST)["build_order"]
    assert order == [
        "requirements.txt", "database.py", "crud.py",
        "ui/__init__.py", "ui/main_window.py", "main.py",
    ]
    assert order.index("database.py") < order.index("crud.py")
    assert order.index("crud.py") < order.index("ui/main_window.py")
    assert order.index("ui/main_window.py") < order.index("main.py")


def test_plan_is_a_valid_project_plan():
    plan = sql.build_plan(CRM_REQUEST)
    assert ProjectPlan.from_dict(plan).is_valid


# --------------------------------------------------------------------------- #
# Deterministic code embedded as exact content
# --------------------------------------------------------------------------- #
def test_database_and_crud_code_embedded_as_content():
    plan = sql.build_plan(CRM_REQUEST)
    db = _content(plan, "database.py")
    crud = _content(plan, "crud.py")
    assert "CREATE TABLE IF NOT EXISTS customers" in db
    assert "def init_db" in db
    assert "def add_customer" in crud
    assert "def get_customers" in crud
    assert "from database import get_connection, init_db" in crud
    assert "VALUES (?, ?, ?)" in crud  # correct placeholder count


def test_main_py_content_has_offscreen_guard():
    main_py = _content(sql.build_plan(CRM_REQUEST), "main.py")
    for token in ("import sys", "import os", "QApplication", "QTimer",
                  "QT_QPA_PLATFORM", "singleShot"):
        assert token in main_py, token


def test_main_window_is_model_generated_and_wires_crud():
    plan = sql.build_plan(CRM_REQUEST)
    # The UI is the one file left to the engine (no exact content).
    assert _content(plan, "ui/main_window.py") == ""
    purpose = _purpose(plan, "ui/main_window.py")
    for token in ("import crud", "crud.init_db()",
                  "crud.add_customer", "crud.get_customers"):
        assert token in purpose, token


def test_entity_detection_drives_schema_for_inventory():
    plan = sql.build_plan("a desktop inventory manager with sqlite")
    db = _content(plan, "database.py")
    crud = _content(plan, "crud.py")
    assert "products" in db
    assert "def add_product" in crud
