"""Tests for the Phase 5 deterministic UI framework (brains/templates/sqlite_support).

Because ui/widgets.py and ui/master_detail.py are deterministic, we can do more than
smoke tests: we generate the whole project into a temp dir and actually CONSTRUCT the
MasterDetailWindow headless (offscreen) against a real SQLite DB, then assert the
master table, search, and child detail panels behave. No Ollama needed.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must precede PySide6 import

import importlib
import sys

import pytest

from brains.templates import sqlite_support as ss
from brains.templates import sqlite_desktop as sql

CRM = "Build a customer CRM desktop app with SQLite"


@pytest.fixture(scope="session", autouse=True)
def _qapp():
    """A single offscreen QApplication for the whole test session."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _load_project(tmp_path, schema):
    """Materialise the generated project (db/crud/export + ui package) in tmp_path,
    import it fresh, and return (crud, master_detail) modules."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    (tmp_path / "export.py").write_text(ss.export_py(schema))
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "__init__.py").write_text("# ui package\n")
    (ui / "widgets.py").write_text(ss.widgets_py())
    (ui / "master_detail.py").write_text(ss.master_detail_py(schema))

    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud", "export", "ui", "ui.widgets", "ui.master_detail"):
        sys.modules.pop(name, None)
    try:
        crud = importlib.import_module("crud")
        master_detail = importlib.import_module("ui.master_detail")
    finally:
        sys.path.remove(str(tmp_path))
    return crud, master_detail


# --------------------------------------------------------------------------- #
# Generated source shape + compiles
# --------------------------------------------------------------------------- #
def test_widgets_py_shape():
    src = ss.widgets_py()
    for fn in ("def populate_table", "def fill_list", "def selected_row_value",
               "def read_form", "def clear_form", "def fill_form"):
        assert fn in src, fn
    compile(src, "widgets.py", "exec")


def test_master_detail_py_shape():
    src = ss.master_detail_py(ss.detect_schema(CRM))
    assert "class MasterDetailWindow(QMainWindow)" in src
    assert "CONFIG = {" in src
    assert "from ui import widgets" in src
    assert "import crud" in src and "import export" in src
    compile(src, "master_detail.py", "exec")


def test_ui_config_for_crm():
    cfg = ss._ui_config(ss.detect_schema(CRM))
    assert cfg["title"] == "Customers"
    assert cfg["primary"]["list_fn"] == "get_customers_with_counts"
    assert cfg["primary"]["columns"] == [
        "id", "name", "email", "phone", "notes_count", "interactions_count"
    ]
    assert cfg["primary"]["add_fn"] == "add_customer"
    assert cfg["primary"]["export_fn"] == "export_customers_with_counts_csv"
    assert [c["title"] for c in cfg["children"]] == ["Notes", "Interactions"]
    assert cfg["children"][0]["list_fn"] == "get_notes_for_customer"
    assert cfg["children"][0]["add_fn"] == "add_note"


def test_ui_config_single_table_has_no_children():
    cfg = ss._ui_config(ss.detect_schema("a desktop inventory manager"))
    assert cfg["children"] == []
    assert cfg["primary"]["list_fn"] == "get_products"          # no counts
    assert cfg["primary"]["export_fn"] == "export_products_csv"


# --------------------------------------------------------------------------- #
# The window actually works (constructed offscreen against a real DB)
# --------------------------------------------------------------------------- #
def test_window_constructs_and_loads_master_table(tmp_path):
    crud, md = _load_project(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    crud.add_customer("Ada Lovelace", "ada@x.com", "1")
    crud.add_customer("Alan Turing", "alan@y.com", "2")

    win = md.MasterDetailWindow()  # __init__ calls refresh()
    assert win.table.rowCount() == 2
    # id, name, email, phone, notes_count, interactions_count
    assert win.table.columnCount() == 6
    assert win.table.item(0, 1).text() == "Ada Lovelace"
    assert len(win.children_state) == 2  # notes + interactions panels


def test_window_add_and_search(tmp_path):
    crud, md = _load_project(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    win = md.MasterDetailWindow()

    # Add via the form + on_add handler.
    win.fields["name"].setText("Grace Hopper")
    win.fields["email"].setText("grace@navy.mil")
    win.fields["phone"].setText("7")
    win.on_add()
    assert win.table.rowCount() == 1
    assert win.table.item(0, 1).text() == "Grace Hopper"

    # Search filters the master table.
    crud.add_customer("Bob", "bob@x.com", "9")
    win.refresh()
    assert win.table.rowCount() == 2
    win.search_input.setText("grace")
    win.on_search()
    assert win.table.rowCount() == 1
    assert win.table.item(0, 1).text() == "Grace Hopper"


def test_window_detail_panel_loads_children_on_select(tmp_path):
    crud, md = _load_project(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    cid = crud.add_customer("Ada", "ada@x.com", "1")
    crud.add_note(cid, "met at PyCon")
    crud.add_note(cid, "follow up")

    win = md.MasterDetailWindow()
    win.table.setCurrentCell(0, 0)
    win.on_select()
    assert win.selected_id == cid

    notes_state = win.children_state[0]   # the Notes panel
    assert notes_state["cfg"]["title"] == "Notes"
    assert notes_state["list"].count() == 2

    # Add a note through the detail panel handler.
    notes_state["fields"]["body"].setText("third note")
    win._add_child(notes_state)
    assert notes_state["list"].count() == 3
