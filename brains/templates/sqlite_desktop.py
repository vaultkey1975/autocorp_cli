#!/usr/bin/env python3
"""
SQLite Desktop template  (AutoCorp CLI - brains.templates)  [SQLite Gen Phase 2]
===============================================================================

Recognises SQLite-backed desktop app requests ("a customer CRM desktop app with
SQLite", "a GUI inventory manager with a database", ...) and produces a
deterministic plan for a PySide6 desktop app with a SQLite persistence layer:

    project/
    |-- main.py              QApplication entry point (self-terminating headless)
    |-- database.py          connection + init_db() + CREATE TABLE(s)   (deterministic)
    |-- crud.py              add/get/update/delete + search + FK queries (deterministic)
    |-- requirements.txt     PySide6   (sqlite3 is in the standard library)
    +-- ui/
        |-- __init__.py      package marker
        +-- main_window.py   QMainWindow wired to crud.*  (table view / search / CRUD)

Phase 2: the persistence layer is generated from a multi-table SCHEMA
(`sqlite_support.detect_schema`). A CRM gets three related tables -
customers, notes(customer_id -> customers.id), interactions(... -> customers.id) -
with foreign keys enforced and a search_<table> helper per table. database.py,
crud.py, main.py, requirements.txt and ui/__init__.py are emitted as exact
`content` (written verbatim by the Builder); only ui/main_window.py is generated
by the engine, from a purpose that names the exact crud functions to call.

Routing: registered before `pyside6_desktop`; matches only when the request
carries BOTH a data signal (sqlite/database/crud/crm/...) AND a GUI signal
(desktop/gui/window/...), so plain desktop apps still go to `pyside6_desktop`.

Validation gate: execute the entry point headless and require exit 0:
    QT_QPA_PLATFORM=offscreen python main.py
"""

from brains.project_plan import sanitize_name
from brains.templates import sqlite_support

NAME = "sqlite_desktop"

# A request must contain at least one of each group to select this template.
DATA_KEYWORDS = ("sqlite", "database", "crud", "crm", "persistence", "sql")
GUI_KEYWORDS = ("desktop", "gui", "window", "qt", "pyside", "pyside6", "app")

# Trigger / filler words stripped when deriving a project name from the request.
_STOP = {
    "build", "a", "an", "the", "me", "please", "create", "make", "app",
    "application", "desktop", "gui", "window", "pyside6", "pyside", "qt", "with",
    "sqlite", "database", "db", "backed", "using", "and",
}

# The single acceptance command: execute the real entry point, headless.
TEST_COMMAND = "QT_QPA_PLATFORM=offscreen python main.py"

# The exact entry point (same self-terminating pattern as pyside6_desktop: runs
# normally on a desktop; under the offscreen platform it starts the event loop
# then quits immediately so headless tests exit 0). Written verbatim.
MAIN_PY_REFERENCE = '''\
import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    # Headless/CI run (offscreen): start the event loop, then quit immediately so
    # the process exits 0 without a user closing the window. Real desktop runs
    # (no offscreen platform) skip this branch and run interactively.
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        QTimer.singleShot(0, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
'''


def matches(request: str) -> bool:
    text = (request or "").lower()
    has_data = any(k in text for k in DATA_KEYWORDS)
    has_gui = any(k in text for k in GUI_KEYWORDS)
    return has_data and has_gui


def _project_name(request: str) -> str:
    words = [w for w in (request or "").lower().split() if w.isalnum() and w not in _STOP]
    name = "_".join(words[:4]) if words else "sqlite_app"
    name = sanitize_name(name)
    if "app" not in name and "desktop" not in name:
        name = f"{name}_app"
    return name or "sqlite_app"


def _main_window_purpose(table, one, field_names, app_desc):
    """The richer Phase 2 UI purpose: table view + select + add/edit/delete +
    search + refresh, wired to the exact crud functions for the primary table."""
    params = ", ".join(field_names)
    headers = ", ".join(["Id"] + [f.capitalize() for f in field_names])
    first = field_names[0] if field_names else "value"
    return (
        "Define a class named MainWindow that inherits QMainWindow (from "
        "PySide6.QtWidgets). Import the persistence layer with exactly: "
        "`import crud`. In __init__: call super().__init__(); set the window "
        "title with setWindowTitle(); set the size with resize(900, 600); create "
        "a central QWidget and pass it to self.setCentralWidget(central); create "
        "a QVBoxLayout and apply it to that central widget; and call "
        "crud.init_db() FIRST so the database/tables exist before any query. "
        f"Build a full management UI for {table}:\n"
        f"- A search row: a QLineEdit stored as self.search_input and a 'Search' "
        f"QPushButton whose handler calls crud.search_{table}("
        "self.search_input.text()) and repopulates the table; plus a 'Refresh' "
        f"QPushButton that reloads all rows via crud.get_{table}().\n"
        f"- A QTableWidget stored as self.table with columns ({headers}) listing "
        f"every {table} row. When a row is selected, fill the input fields from "
        "that row and remember the row's id as self.selected_id.\n"
        f"- One QLineEdit per field ({params}), each stored as an attribute on "
        f"self (e.g. self.{first}_input).\n"
        f"- An 'Add' QPushButton whose handler reads the inputs and calls "
        f"crud.add_{one}({params}), then refreshes the table.\n"
        f"- An 'Edit' QPushButton -> crud.update_{one}(self.selected_id, {params}) "
        "then refresh.\n"
        f"- A 'Delete' QPushButton -> crud.delete_{one}(self.selected_id) then "
        "refresh.\n"
        f"Use ONLY these crud functions, by these exact names: "
        f"crud.add_{one}({params}); crud.get_{table}(); crud.get_{one}(record_id); "
        f"crud.update_{one}(record_id, {params}); crud.delete_{one}(record_id); "
        f"crud.search_{table}(query). "
        "Store every widget a method refers to as an attribute on self so there "
        "are NO undefined attributes. Construction must NOT raise: MainWindow() "
        "must build with no arguments and __init__ must run to completion without "
        "error. Do NOT create a QApplication and do NOT call app.exec() here. "
        f"This application is: {app_desc}."
    )


def build_plan(request: str) -> dict:
    """Return a ProjectPlan-shaped dict for a SQLite-backed PySide6 desktop app."""
    app_desc = (request or "a SQLite desktop application").strip()

    schema = sqlite_support.detect_schema(request)
    primary = schema[0]
    table = primary.name
    one = sqlite_support._singular(table)
    field_names = [name for name, _ in primary.columns]
    table_names = [t.name for t in schema]

    database_code = sqlite_support.schema_database_py(schema)
    crud_code = sqlite_support.schema_crud_py(schema)
    main_window_purpose = _main_window_purpose(table, one, field_names, app_desc)

    files = [
        {
            "path": "requirements.txt",
            "purpose": "Python dependencies (PySide6; sqlite3 is stdlib).",
            "content": "PySide6\n",
        },
        {
            "path": "database.py",
            "purpose": (
                f"Persistence layer: get_connection() (foreign keys enforced), "
                f"init_db(), and CREATE TABLE for {', '.join(table_names)}. "
                "Generated deterministically."
            ),
            "content": database_code,
        },
        {
            "path": "crud.py",
            "purpose": (
                f"CRUD + search helpers for {', '.join(table_names)} using "
                "parameterised SQL. Generated deterministically."
            ),
            "content": crud_code,
        },
        {
            "path": "ui/__init__.py",
            "purpose": "Package marker so `ui` is an importable package.",
            "content": "# ui package\n",
        },
        {"path": "ui/main_window.py", "purpose": main_window_purpose},
        {
            "path": "main.py",
            "purpose": (
                "Application entry point; starts QApplication, shows MainWindow, "
                "and self-quits under QT_QPA_PLATFORM=offscreen so headless runs "
                "exit 0. Generated deterministically."
            ),
            "content": MAIN_PY_REFERENCE,
        },
    ]

    return {
        "project_name": _project_name(request),
        "project_type": "desktop",
        "language": "python",
        "summary": (
            f"A SQLite-backed PySide6 desktop application ({', '.join(table_names)}): "
            f"{app_desc}"
        ),
        "files": files,
        # Dependency-safe: database.py before crud.py (which imports it); the ui
        # package + main_window (imports crud) before main.py (imports the window).
        "build_order": [
            "requirements.txt",
            "database.py",
            "crud.py",
            "ui/__init__.py",
            "ui/main_window.py",
            "main.py",
        ],
        "test_command": TEST_COMMAND,
        "success_criteria": [
            "main.py, database.py, crud.py, ui/main_window.py and "
            "requirements.txt all exist",
            "requirements.txt contains PySide6",
            f"database.py creates the {', '.join(table_names)} table(s) with "
            "foreign keys enforced and exposes init_db()",
            f"crud.py provides add_{one}, get_{table}, get_{one}, update_{one}, "
            f"delete_{one} and search_{table} using parameterised SQL",
            "MainWindow inherits QMainWindow, calls crud.init_db(), shows a table "
            "view, and supports add / edit / delete / search / refresh",
            "`QT_QPA_PLATFORM=offscreen python main.py` starts QApplication, "
            "constructs and shows MainWindow, runs the event loop, and exits 0",
        ],
    }
