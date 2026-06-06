#!/usr/bin/env python3
"""
SQLite Desktop template  (AutoCorp CLI - brains.templates)  [SQLite Gen Phase 5]
===============================================================================

Recognises SQLite-backed desktop app requests ("a customer CRM desktop app with
SQLite", "a GUI inventory manager with a database", ...) and produces a
deterministic plan for a PySide6 desktop app with a SQLite persistence layer and
a deterministic UI framework:

    project/
    |-- main.py              QApplication entry point (self-terminating headless)
    |-- database.py          connection + init_db() + CREATE TABLE(s)   (deterministic)
    |-- crud.py              CRUD + search + FK queries + counts          (deterministic)
    |-- export.py            CSV export helpers over crud                 (deterministic)
    |-- requirements.txt     PySide6
    +-- ui/
        |-- __init__.py      package marker
        |-- widgets.py       generic UI helpers                          (deterministic)
        |-- master_detail.py generic config-driven MasterDetailWindow    (deterministic)
        +-- main_window.py   thin assembly: re-exports MainWindow         (model-generated)

Phase 5: nearly the entire UI is deterministic. ui/widgets.py is generic helpers and
ui/master_detail.py is a config-driven MasterDetailWindow (the master table with
child counts, search, refresh, export, primary add/edit/delete, and a detail panel
per child) - both written verbatim. The only model-generated file, ui/main_window.py,
shrinks to a one-line re-export, so the model's UI burden (and failure rate) drops
sharply. database.py, crud.py, export.py, widgets.py, master_detail.py, main.py,
requirements.txt and ui/__init__.py are all emitted as exact `content`.

Routing: registered before `pyside6_desktop`; matches only when the request carries
BOTH a data signal (sqlite/database/crud/crm/...) AND a GUI signal
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

# The exact entry point (same self-terminating pattern as pyside6_desktop). Verbatim.
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

# The thin, model-generated assembly file. With the framework deterministic, this is
# all the model has to produce - a single re-export - so the failure rate collapses.
MAIN_WINDOW_PURPOSE = (
    "This file is a THIN assembly layer only. The real UI is the deterministic "
    "MasterDetailWindow already written in ui/master_detail.py. Output EXACTLY this "
    "one line and nothing else - no extra imports, no class definition, no comments:\n\n"
    "from ui.master_detail import MasterDetailWindow as MainWindow\n"
)


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


def build_plan(request: str) -> dict:
    """Return a ProjectPlan-shaped dict for a SQLite-backed PySide6 desktop app."""
    app_desc = (request or "a SQLite desktop application").strip()

    schema = sqlite_support.detect_schema(request)
    primary = schema[0]
    table = primary.name
    one = sqlite_support._singular(table)
    table_names = [t.name for t in schema]

    files = [
        {
            "path": "requirements.txt",
            "purpose": "Python dependencies (PySide6; sqlite3 and csv are stdlib).",
            "content": "PySide6\n",
        },
        {
            "path": "database.py",
            "purpose": (
                f"Persistence layer: get_connection() (foreign keys enforced), "
                f"init_db(), and CREATE TABLE for {', '.join(table_names)}. "
                "Generated deterministically."
            ),
            "content": sqlite_support.schema_database_py(schema),
        },
        {
            "path": "crud.py",
            "purpose": (
                f"CRUD + search + counts helpers for {', '.join(table_names)}. "
                "Generated deterministically."
            ),
            "content": sqlite_support.schema_crud_py(schema),
        },
        {
            "path": "export.py",
            "purpose": "CSV export helpers over crud. Generated deterministically.",
            "content": sqlite_support.export_py(schema),
        },
        {
            "path": "ui/__init__.py",
            "purpose": "Package marker so `ui` is an importable package.",
            "content": "# ui package\n",
        },
        {
            "path": "ui/widgets.py",
            "purpose": "Generic, reusable UI helpers. Generated deterministically.",
            "content": sqlite_support.widgets_py(),
        },
        {
            "path": "ui/master_detail.py",
            "purpose": (
                "Generic config-driven MasterDetailWindow (master table with child "
                "counts, search, refresh, export, primary add/edit/delete, and a "
                "detail panel per child). Generated deterministically."
            ),
            "content": sqlite_support.master_detail_py(schema),
        },
        {"path": "ui/main_window.py", "purpose": MAIN_WINDOW_PURPOSE},
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
        # Dependency-safe order: database -> crud -> export; the ui package, then
        # widgets, then master_detail (imports crud/export/widgets), then the thin
        # main_window (imports master_detail), then main.py (imports the window).
        "build_order": [
            "requirements.txt",
            "database.py",
            "crud.py",
            "export.py",
            "ui/__init__.py",
            "ui/widgets.py",
            "ui/master_detail.py",
            "ui/main_window.py",
            "main.py",
        ],
        "test_command": TEST_COMMAND,
        "success_criteria": [
            "main.py, database.py, crud.py, export.py, ui/widgets.py, "
            "ui/master_detail.py, ui/main_window.py and requirements.txt all exist",
            "requirements.txt contains PySide6",
            f"database.py creates the {', '.join(table_names)} table(s) with "
            "foreign keys enforced and exposes init_db()",
            f"crud.py provides add_{one}, get_{table}, get_{one}, update_{one}, "
            f"delete_{one}, search_{table}, the child CRUD, and get_{table}_with_counts",
            "export.py provides CSV export over the crud helpers",
            "ui/master_detail.py defines a config-driven MasterDetailWindow; "
            "ui/main_window.py re-exports it as MainWindow",
            "`QT_QPA_PLATFORM=offscreen python main.py` starts QApplication, "
            "constructs and shows MainWindow, runs the event loop, and exits 0",
        ],
    }
